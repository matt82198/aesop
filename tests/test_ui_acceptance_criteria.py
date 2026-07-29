"""
Tests for acceptanceCriteria authoring in tracker (wave-X user feature).

TDD: write the tests first, then implement.
"""
import http.client
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import threading
import unittest
from pathlib import Path

SERVE_PATH = Path(__file__).parent.parent / "ui" / "serve.py"
UI_PATH = Path(__file__).parent.parent / "ui"

ENV_KEYS = ("AESOP_ROOT", "AESOP_TRANSCRIPTS_ROOT", "AESOP_STATE_ROOT",
            "AESOP_UI_COLLECT_INTERVAL", "PORT")


def load_serve(fixture_root, port=None, extra_env=None):
    """Import a fresh serve module instance bound to a fixture AESOP_ROOT."""
    os.environ["AESOP_ROOT"] = str(fixture_root)
    if port is not None:
        os.environ["PORT"] = str(port)
    for k, v in (extra_env or {}).items():
        os.environ[k] = str(v)
    spec = importlib.util.spec_from_file_location(
        f"serve_ac_test_{id(fixture_root)}", SERVE_PATH)
    serve = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(serve)
    return serve


class AcceptanceCriteriaTestCase(unittest.TestCase):
    def setUp(self):
        self.fixture_root = Path(tempfile.mkdtemp(prefix="aesop-ac-test-"))
        (self.fixture_root / "state").mkdir()
        (self.fixture_root / "transcripts").mkdir()
        self._saved_env = {k: os.environ.get(k) for k in ENV_KEYS}
        os.environ["AESOP_TRANSCRIPTS_ROOT"] = str(self.fixture_root / "transcripts")
        os.environ["AESOP_UI_COLLECT_INTERVAL"] = "0.2"
        os.environ["PORT"] = "18771"

        self.serve = load_serve(self.fixture_root)
        self.token = self.serve.SESSION_TOKEN
        self.assertTrue(self.token, "fixture must produce a session token")
        self.config_port = self.serve.PORT

        if str(UI_PATH) not in sys.path:
            sys.path.insert(0, str(UI_PATH))
        import handler

        self.httpd = handler.QuietThreadingHTTPServer(
            ("127.0.0.1", self.config_port), self.serve.DashboardHandler)
        self.httpd.daemon_threads = True
        self.actual_port = self.httpd.server_address[1]
        self.server_thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.server_thread.start()
        threading.Event().wait(0.1)  # Let server start

    def tearDown(self):
        try:
            self.serve._collector_stop_event.set()
            self.httpd.shutdown()
            self.httpd.server_close()
            self.server_thread.join(timeout=3)
        finally:
            for k, v in self._saved_env.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
            shutil.rmtree(self.fixture_root, ignore_errors=True)

    def _conn(self):
        return http.client.HTTPConnection("127.0.0.1", self.actual_port, timeout=5)

    def test_create_item_with_acceptance_criteria(self):
        """Test POST /api/tracker can create item with acceptanceCriteria."""
        conn = self._conn()
        body = json.dumps({
            "title": "Implement feature X",
            "priority": "P1",
            "acceptanceCriteria": [
                {
                    "statement": "Feature X works",
                    "verifiable_by": "pytest tests/test_feature_x.py"
                },
                {
                    "statement": "No regressions",
                    "verifiable_by": "full test suite"
                }
            ]
        }).encode('utf-8')

        conn.request("POST", "/api/tracker",
                     body=body,
                     headers={
                         "Host": f"127.0.0.1:{self.actual_port}",
                         "Content-Length": str(len(body)),
                         "Content-Type": "application/json",
                         "X-Aesop-Token": self.token,
                         "Origin": f"http://127.0.0.1:{self.actual_port}",
                     })
        resp = conn.getresponse()
        self.assertEqual(resp.status, 201, f"Expected 201, got {resp.status}")

        data = json.loads(resp.read().decode('utf-8'))
        self.assertEqual(data["title"], "Implement feature X")
        self.assertEqual(len(data.get("acceptanceCriteria", [])), 2)
        self.assertEqual(data["acceptanceCriteria"][0]["statement"], "Feature X works")
        conn.close()

    def test_create_item_without_acceptance_criteria(self):
        """Test POST /api/tracker still works without acceptanceCriteria."""
        conn = self._conn()
        body = json.dumps({
            "title": "Simple task",
            "priority": "P2"
        }).encode('utf-8')

        conn.request("POST", "/api/tracker",
                     body=body,
                     headers={
                         "Host": f"127.0.0.1:{self.actual_port}",
                         "Content-Length": str(len(body)),
                         "Content-Type": "application/json",
                         "X-Aesop-Token": self.token,
                         "Origin": f"http://127.0.0.1:{self.actual_port}",
                     })
        resp = conn.getresponse()
        self.assertEqual(resp.status, 201)

        data = json.loads(resp.read().decode('utf-8'))
        self.assertEqual(data["title"], "Simple task")
        # acceptanceCriteria should either be absent or empty
        self.assertTrue(
            "acceptanceCriteria" not in data or data.get("acceptanceCriteria") is None
        )
        conn.close()

    def test_update_acceptance_criteria(self):
        """Test POST /api/tracker/<id> can update acceptanceCriteria."""
        # First create an item
        conn = self._conn()
        body = json.dumps({
            "title": "Item to update",
            "priority": "P1"
        }).encode('utf-8')

        conn.request("POST", "/api/tracker",
                     body=body,
                     headers={
                         "Host": f"127.0.0.1:{self.actual_port}",
                         "Content-Length": str(len(body)),
                         "Content-Type": "application/json",
                         "X-Aesop-Token": self.token,
                         "Origin": f"http://127.0.0.1:{self.actual_port}",
                     })
        resp = conn.getresponse()
        item = json.loads(resp.read().decode('utf-8'))
        item_id = item["id"]
        conn.close()

        # Now update it with acceptanceCriteria
        conn = self._conn()
        update_body = json.dumps({
            "acceptanceCriteria": [
                {
                    "statement": "Updated AC 1",
                    "verifiable_by": "test command 1"
                }
            ]
        }).encode('utf-8')

        conn.request("POST", f"/api/tracker/{item_id}",
                     body=update_body,
                     headers={
                         "Host": f"127.0.0.1:{self.actual_port}",
                         "Content-Length": str(len(update_body)),
                         "Content-Type": "application/json",
                         "X-Aesop-Token": self.token,
                         "Origin": f"http://127.0.0.1:{self.actual_port}",
                     })
        resp = conn.getresponse()
        self.assertEqual(resp.status, 200)

        updated = json.loads(resp.read().decode('utf-8'))
        self.assertEqual(len(updated.get("acceptanceCriteria", [])), 1)
        self.assertEqual(updated["acceptanceCriteria"][0]["statement"], "Updated AC 1")
        conn.close()

    def test_get_tracker_items_includes_acceptance_criteria(self):
        """Test GET /api/tracker includes acceptanceCriteria in response."""
        # Create an item with AC
        conn = self._conn()
        body = json.dumps({
            "title": "Item with AC",
            "acceptanceCriteria": [
                {
                    "statement": "Works correctly",
                    "verifiable_by": "test"
                }
            ]
        }).encode('utf-8')

        conn.request("POST", "/api/tracker",
                     body=body,
                     headers={
                         "Host": f"127.0.0.1:{self.actual_port}",
                         "Content-Length": str(len(body)),
                         "Content-Type": "application/json",
                         "X-Aesop-Token": self.token,
                         "Origin": f"http://127.0.0.1:{self.actual_port}",
                     })
        resp = conn.getresponse()
        resp.read()
        conn.close()

        # Fetch items
        conn = self._conn()
        conn.request("GET", "/api/tracker",
                     headers={
                         "Host": f"127.0.0.1:{self.actual_port}",
                     })
        resp = conn.getresponse()
        items = json.loads(resp.read().decode('utf-8'))

        # Find our item
        our_item = next((i for i in items if i.get("title") == "Item with AC"), None)
        self.assertIsNotNone(our_item)
        self.assertEqual(len(our_item.get("acceptanceCriteria", [])), 1)
        conn.close()


if __name__ == '__main__':
    unittest.main()
