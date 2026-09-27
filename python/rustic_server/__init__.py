"""Declare HTTP routes and run the Rust server through a native binding."""

import json as _json
from . import _rustic

_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"}


class App:
    def __init__(self):
        self._routes = []
        self._keys = set()

    def route(self, method: str, path: str, *, json, status: int = 200):
        method = method.upper()
        if method not in _METHODS:
            raise ValueError(f"Unsupported method: {method}")
        if not isinstance(path, str) or not path.startswith("/") or any(
            c in path for c in "{}*?#"
        ) or any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in path):
            raise ValueError("Expected an exact absolute URL path without query or fragment")
        if type(status) is not int or not 200 <= status <= 599 or status in (204, 205, 304):
            raise ValueError("Expected status 200..599 allowing a JSON response body")
        key = (method, path)
        if key in self._keys:
            raise ValueError(f"Duplicate route: {method} {path}")
        # Snapshot the payload and reject non-JSON values before changing app state.
        payload = _json.dumps(json, allow_nan=False, separators=(",", ":")).encode("utf-8")
        self._routes.append((method, path, status, payload))
        self._keys.add(key)
        return self

    def get(self, path: str, *, json, status: int = 200):
        return self.route("GET", path, json=json, status=status)

    def run(self, host: str = "127.0.0.1", port: int = 8080):
        """Serve in Rust until Ctrl-C. Call from Python's main thread."""
        if not self._routes:
            raise ValueError("Declare at least one route")
        if type(port) is not int or not 0 <= port <= 65535:
            raise ValueError("Expected port 0..65535")
        import threading

        if threading.current_thread() is not threading.main_thread():
            raise RuntimeError("App.run() must be called from the main thread")
        try:
            _rustic.serve(self._routes, host, port)
        except KeyboardInterrupt:
            pass
