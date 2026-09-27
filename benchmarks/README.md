# Isolated Flask / Django / rustic-server benchmark

Benchmark apps, dependencies, tools, and results live here. Main server source and dependencies are unchanged.

## Run from repository root

```sh
uv sync --project benchmarks
cargo install oha --version 1.16.0 --locked --root benchmarks/.tools
uv run --project benchmarks python benchmarks/run.py
```

Requires Cargo and uv. The separate `benchmarks/.venv` contains Flask, Django, Gunicorn, psutil, and a release build of the local rustic-server package. `benchmarks/uv.lock` locks Python dependencies. oha is installed only into `benchmarks/.tools`.

Output: [results-portable/REPORT.md](results-portable/REPORT.md), environment metadata, raw oha JSON, per-trial metrics, and server logs. Recorded commands use relative project paths; absolute tool paths outside the project are reduced to executable names. Existing result folders are protected against accidental overwrite.

For a longer rerun:

```sh
uv run --project benchmarks python benchmarks/run.py --duration 15 --warmup 3 --trials 5 --output benchmarks/results-long
```

## Workload

Every app serves `GET /health` with HTTP 200, `application/json`, and exactly `{"status":"ok"}` (14 bytes). Response bytes are encoded in advance on all three servers. Each trial checks status, body, and content type before load generation.

- **rustic-server:** actual PyO3 extension, one Tokio worker via `TOKIO_WORKER_THREADS=1`.
- **Flask / Django:** Gunicorn gthread, one worker process with four threads, five-second keep-alive. Development servers are not used.
- **Django:** no database, middleware, or installed apps; DEBUG off.
- **Client:** oha, two Tokio threads, HTTP/1.1 persistent connections over loopback.
- Fresh server per trial; framework order rotates each round. No concurrent framework runs.
- Defaults: two-second warmup followed by five-second measurement, three trials per scenario.
- oha drains in-flight requests after the measurement deadline (`-w`), with a three-second per-request timeout.

| Scenario | Connections | Request rate | Purpose |
| --- | ---: | --- | --- |
| c1 | 1 | Unrestricted | Serial request latency and throughput |
| c32 | 32 | Unrestricted | Throughput under concurrent load |
| rate500 | 32 | 500 requests/second | CPU and memory at comparable traffic |

## Metrics and limits

Report shows independent medians across trials. CPU is server process-tree CPU seconds divided by elapsed wall time; 100% means one core. CPU microseconds/request measures server CPU cost per completed request. Memory is sampled every 100 ms and includes Python, the native library, and Gunicorn master plus worker. Shared pages can be counted twice when summing RSS across processes.

Saturation CPU percentages correspond to different achieved throughputs. Use rate500 to compare CPU at similar traffic. This is a one-worker baseline, not a tuned multi-worker deployment comparison. Thread counts, HTTP implementations, and framework functionality differ.

Client and server share one machine. OS scheduling, background activity, thermals, and client capacity affect results. These short static-response tests do not predict database, TLS, authentication, or dynamic-handler performance. Saturation latency is closed-loop and not corrected for coordinated omission. Application startup and compilation are excluded. Transport errors or non-200 results invalidate a trial.

`pilot-deadline-abort/` retains an excluded initial trial: oha cancelled one in-flight request at its deadline before `-w` was enabled. It is not included in the report.

## References

- [Flask production deployment](https://flask.palletsprojects.com/en/stable/deploying/)
- [Django with Gunicorn](https://docs.djangoproject.com/en/5.2/howto/deployment/wsgi/gunicorn/)
- [Gunicorn worker design and keep-alive](https://docs.gunicorn.org/en/stable/design.html)
- [oha load generator](https://github.com/hatoo/oha)
