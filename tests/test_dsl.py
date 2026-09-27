import json
import unittest
from unittest.mock import patch

from rustic_server import App


class DSLTests(unittest.TestCase):
    def test_run_passes_snapshot_directly_to_rust(self):
        payload = {"ok": True}
        app = App().get("/health", json=payload)
        payload["ok"] = False
        with patch("rustic_server._rustic.serve") as serve:
            app.run(port=9000)
        routes, host, port = serve.call_args.args
        self.assertEqual((host, port), ("127.0.0.1", 9000))
        self.assertEqual(routes[0][:3], ("GET", "/health", 200))
        self.assertEqual(json.loads(routes[0][3]), {"ok": True})

    def test_run_rejects_empty_app_and_invalid_port(self):
        with self.assertRaises(ValueError):
            App().run()
        with self.assertRaises(ValueError):
            App().get("/", json={}).run(port=-1)

    def test_rustic_errors_reach_python(self):
        from rustic_server import _rustic

        with self.assertRaises(ValueError):
            _rustic.serve([], "127.0.0.1", 0)
        with self.assertRaises(ValueError):
            App().get("/", json={}).run(host="invalid")

    def test_reject_invalid_routes(self):
        for path in ["relative", "/{id}", "/x?q=1", "/bad path"]:
            with self.subTest(path=path), self.assertRaises(ValueError):
                App().get(path, json={})
        for status in [199, 204, 205, 304, 600]:
            with self.subTest(status=status), self.assertRaises(ValueError):
                App().get("/", json={}, status=status)
        with self.assertRaises(ValueError):
            App().get("/", json=float("nan"))
        with self.assertRaises(ValueError):
            App().get("/", json={}).get("/", json={})
