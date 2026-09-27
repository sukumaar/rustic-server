"""Exercise the real extension, sockets, and Python signal handling."""

import json
import queue
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from rustic_server import App


class NativeServerTests(unittest.TestCase):
    def test_bind_error_reaches_python(self):
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen()
            with self.assertRaises(OSError):
                App().get("/", json={}).run(port=listener.getsockname()[1])

    def test_entry_point_serves_without_files_and_stops_on_ctrl_c(self):
        code = """
from rustic_server import App
app = App()
app.get('/health', json={'status': 'ok'})
app.route('POST', '/accepted', json={'accepted': True}, status=202)
app.run(port=0)
"""
        with tempfile.TemporaryDirectory() as directory:
            process = subprocess.Popen(
                [sys.executable, "-c", code], cwd=directory,
                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
            )
            lines = queue.Queue()
            reader = threading.Thread(target=lambda: lines.put(process.stderr.readline()), daemon=True)
            reader.start()
            try:
                line = lines.get(timeout=15)
                self.assertTrue(line.startswith("Listening on http://"), line)
                url = line.strip().removeprefix("Listening on ")
                opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
                with opener.open(url + "/health", timeout=3) as response:
                    self.assertEqual(response.status, 200)
                    self.assertEqual(json.load(response), {"status": "ok"})
                with opener.open(urllib.request.Request(url + "/accepted", method="POST"), timeout=3) as response:
                    self.assertEqual(response.status, 202)
                    self.assertEqual(json.load(response), {"accepted": True})
                with opener.open(urllib.request.Request(url + "/health", method="HEAD"), timeout=3) as response:
                    self.assertEqual(response.read(), b"")
                for path, method, status in [("/missing", "GET", 404), ("/health", "DELETE", 405)]:
                    with self.assertRaises(urllib.error.HTTPError) as error:
                        opener.open(urllib.request.Request(url + path, method=method), timeout=3)
                    self.assertEqual(error.exception.code, status)
                    error.exception.close()
                self.assertEqual(list(Path(directory).iterdir()), [])
                process.send_signal(signal.SIGINT)
                self.assertEqual(process.wait(timeout=10), 0)
            finally:
                if process.poll() is None:
                    process.kill()
                process.wait(timeout=5)
                reader.join(timeout=1)
                process.stderr.close()
