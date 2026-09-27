"""Sequential, isolated server runs. Raw results retained for every trial."""
import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import signal
import socket
import statistics
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timezone

import psutil

ROOT = Path(__file__).resolve().parent
APPS = ("rustic-server", "flask", "django")
SCENARIOS = (("c1", 1, None), ("c32", 32, None), ("rate500", 32, 500))


def command(app, port):
    if app == "rustic-server":
        return [sys.executable, str(ROOT / "rustic_app.py"), str(port)]
    return [
        sys.executable, "-m", "gunicorn", f"{app}_app:app",
        "--bind", f"127.0.0.1:{port}", "--workers", "1",
        "--worker-class", "gthread", "--threads", "4", "--keep-alive", "5",
        "--log-level", "warning",
    ]


def portable_command(arguments):
    """Keep result artifacts reproducible without recording host paths."""
    displayed = []
    for index, argument in enumerate(arguments):
        path = Path(argument)
        if index == 0 and path.name.startswith("python"):
            displayed.append("python")
        elif path.is_absolute():
            try:
                displayed.append(path.resolve().relative_to(ROOT.parent).as_posix())
            except ValueError:
                displayed.append(path.name)
        else:
            displayed.append(argument)
    return displayed


def snapshot(processes):
    cpu = rss = 0
    for process in processes:
        times = process.cpu_times()
        cpu += times.user + times.system
        rss += process.memory_info().rss
    return cpu, rss / 1024**2


def wait_ready(process, url):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError("Server exited; inspect its log")
        try:
            with opener.open(url, timeout=0.5) as response:
                assert response.status == 200
                assert response.read() == b'{"status":"ok"}'
                assert response.headers.get_content_type() == "application/json"
                return
        except (OSError, urllib.error.URLError):
            time.sleep(0.05)
    raise RuntimeError("Server did not become ready")


def trial(args, app, scenario, connections, rate, number, output):
    name = f"{scenario}-{app}-{number}"
    with socket.socket() as reserve:
        reserve.bind(("127.0.0.1", 0))
        port = reserve.getsockname()[1]
    env = {**os.environ, "TOKIO_WORKER_THREADS": "1", "PYTHONUNBUFFERED": "1"}
    env.pop("GUNICORN_CMD_ARGS", None)
    server_command = command(app, port)
    with (output / f"{name}.server.log").open("w") as log:
        server = subprocess.Popen(server_command, cwd=ROOT, env=env, stdout=log, stderr=log, start_new_session=True)
        load = None
        try:
            url = f"http://127.0.0.1:{port}/health"
            wait_ready(server, url)
            base = [str(args.oha), "--no-tui", "--output-format", "json", "-w", "-t", "3s", "-c", str(connections)]
            if rate:
                base += ["-q", str(rate)]
            client_env = {**os.environ, "TOKIO_WORKER_THREADS": "2"}
            subprocess.run(base + ["-z", f"{args.warmup}s", url], env=client_env, check=True, capture_output=True, timeout=args.warmup + 15)
            root = psutil.Process(server.pid)
            processes = [root, *root.children(recursive=True)]
            initial_cpu, idle_rss = snapshot(processes)
            memories = []
            measured_command = base + ["-z", f"{args.duration}s", url]
            start = time.monotonic()
            with (output / f"{name}.oha.json").open("w") as raw, (output / f"{name}.oha.log").open("w") as err:
                load = subprocess.Popen(measured_command, stdout=raw, stderr=err, env=client_env)
                while load.poll() is None:
                    _, rss = snapshot(processes)
                    memories.append(rss)
                    if time.monotonic() - start > args.duration + 15:
                        raise TimeoutError("Load generator timed out")
                    time.sleep(0.1)
                if load.returncode:
                    raise RuntimeError("Load generator failed; inspect its log")
            elapsed = time.monotonic() - start
            final_cpu, _ = snapshot(processes)
            data = json.loads((output / f"{name}.oha.json").read_text())
            statuses = data["statusCodeDistribution"]
            count = sum(statuses.values())
            errors = data.get("errorDistribution", {})
            result = dict(
                app=app, scenario=scenario, trial=number, connections=connections, target_rps=rate,
                requests=count, rps=data["summary"]["requestsPerSec"],
                p50_ms=data["latencyPercentiles"]["p50"] * 1000,
                p95_ms=data["latencyPercentiles"]["p95"] * 1000,
                p99_ms=data["latencyPercentiles"]["p99"] * 1000,
                cpu_percent=(final_cpu-initial_cpu) / elapsed * 100,
                cpu_us_per_request=(final_cpu-initial_cpu) / max(count, 1) * 1e6,
                idle_rss_mib=idle_rss, mean_rss_mib=statistics.mean(memories), peak_rss_mib=max(memories),
                cpu_seconds=final_cpu-initial_cpu, measurement_seconds=elapsed,
                statuses=statuses, errors=errors,
                server_command=portable_command(server_command),
                load_command=portable_command(measured_command),
            )
            if not count or any(code != "200" for code in statuses) or errors:
                raise RuntimeError(f"Invalid benchmark responses: {statuses}, {errors}")
            (output / f"{name}.metrics.json").write_text(json.dumps(result, indent=2) + "\n")
            print(f"{name}: {result['rps']:,.0f} req/s; p95 {result['p95_ms']:.3f} ms; CPU {result['cpu_percent']:.1f}%; RSS {result['mean_rss_mib']:.1f} MiB", flush=True)
            return result
        finally:
            if load is not None and load.poll() is None:
                load.kill()
                load.wait(timeout=5)
            if server.poll() is None:
                os.killpg(server.pid, signal.SIGINT if app == "rustic-server" else signal.SIGTERM)
                try:
                    server.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    os.killpg(server.pid, signal.SIGKILL)
                    server.wait(timeout=5)


def report(results, metadata, output):
    lines = ["# Local benchmark results", "", f"Run: {metadata['started_utc']}", "", "All numbers below are independent medians of repeated trials. CPU: 100% = one core. RSS includes the full server process tree, including Python and Gunicorn master/workers.", ""]
    for scenario, _, _ in SCENARIOS:
        lines += [f"## {scenario}", "", "| Server | req/s | p50 ms | p95 ms | p99 ms | CPU % | CPU µs/req | Mean RSS MiB | Peak RSS MiB |", "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"]
        for app in APPS:
            rows = [r for r in results if r["app"] == app and r["scenario"] == scenario]
            values = [statistics.median(r[key] for r in rows) for key in ("rps", "p50_ms", "p95_ms", "p99_ms", "cpu_percent", "cpu_us_per_request", "mean_rss_mib", "peak_rss_mib")]
            lines.append(f"| {app} | " + " | ".join(f"{value:,.3f}" for value in values) + " |")
        lines.append("")
    lines += ["## Environment and method", "", "```json", json.dumps(metadata, indent=2), "```", "", "See [README](../README.md) for setup, constraints, and interpretation. Raw per-trial metrics and oha responses accompany this report.", "", "All recorded trials returned only HTTP 200 with no reported transport errors. Endpoint correctness was checked before each trial.", "", "These are local static-response measurements, not a general framework ranking. Flask and Django use pre-encoded bytes too. Saturation CPU percentages represent different achieved throughputs; use rate500 for equal-load comparisons. RSS sums may count shared pages more than once. Client and server compete for the same host; short runs, scheduling, thermal state, and other applications can affect results. Latencies at saturation use a closed-loop client and do not correct coordinated omission."]
    (output / "REPORT.md").write_text("\n".join(lines) + "\n")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--duration", type=int, default=5)
    parser.add_argument("--warmup", type=int, default=2)
    parser.add_argument("--trials", type=int, default=3)
    parser.add_argument("--oha", type=Path, default=ROOT / ".tools/bin/oha")
    parser.add_argument("--output", type=Path, default=ROOT / "results-portable")
    args = parser.parse_args()
    if min(args.duration, args.warmup, args.trials) < 1:
        parser.error("duration, warmup, and trials must be positive")
    args.oha = args.oha.resolve()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    if (output / "metadata.json").exists():
        parser.error("Output already contains results; choose a new --output directory")
    sources = [*ROOT.glob("*.py"), ROOT / "pyproject.toml", ROOT / "uv.lock", ROOT.parent / "src/lib.rs", ROOT.parent / "python/rustic_server/__init__.py", ROOT.parent / "Cargo.lock"]
    metadata = dict(
        started_utc=datetime.now(timezone.utc).isoformat(), platform=platform.platform(), machine=platform.machine(),
        python=sys.version, physical_cpus=psutil.cpu_count(logical=False), logical_cpus=psutil.cpu_count(),
        ram_gib=psutil.virtual_memory().total / 1024**3,
        packages={name: importlib.metadata.version(name) for name in ("rustic-server", "flask", "django", "gunicorn", "psutil")},
        oha=subprocess.check_output([str(args.oha), "--version"], text=True).strip(),
        duration_seconds=args.duration, warmup_seconds=args.warmup, trials=args.trials,
        configuration="Rust: 1 Tokio worker; Flask/Django: Gunicorn gthread, 1 worker, 4 threads; oha: 2 Tokio threads. HTTP/1.1 keep-alive over loopback. 14-byte pre-encoded JSON on all servers.",
        source_sha256={str(path.relative_to(ROOT.parent)): hashlib.sha256(path.read_bytes()).hexdigest() for path in sources},
    )
    (output / "metadata.json").write_text(json.dumps(metadata, indent=2) + "\n")
    results = []
    for scenario, connections, rate in SCENARIOS:
        for number in range(args.trials):
            # Rotate ordering to reduce systematic warm-machine bias.
            for app in APPS[number % 3:] + APPS[:number % 3]:
                results.append(trial(args, app, scenario, connections, rate, number + 1, output))
    report(results, metadata, output)
    try:
        report_path = output.relative_to(ROOT.parent).as_posix()
    except ValueError:
        report_path = output.name
    print(f"Report: {report_path}/REPORT.md", flush=True)


if __name__ == "__main__":
    main()
