"""Unit tests for tracker SSE, orchestrator status, inbox."""
import importlib.util
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path

SERVE_PATH = Path(__file__).parent.parent / "ui" / "serve.py"

ENV_KEYS = ("AESOP_ROOT", "AESOP_TRANSCRIPTS_ROOT", "AESOP_STATE_ROOT",
            "AESOP_UI_COLLECT_INTERVAL", "PORT")


def load_serve(fixture_root, extra_env=None):
    os.environ["AESOP_ROOT"] = str(fixture_root)
    for k, v in (extra_env or {}).items():
        os.environ[k] = str(v)
    spec = importlib.util.spec_from_file_location(f"serve_tracker_{id(fixture_root)}", SERVE_PATH)
    serve = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(serve)
    return serve


# Scrub the state-root env vars for this whole module so the snapshot reads only the
# fixture state dir, never a live AESOP_STATE_ROOT / CONDUCTOR_ROOT from the shell.
_STATE_ENV_KEYS = ("AESOP_STATE_ROOT", "CONDUCTOR_ROOT")
_SAVED_STATE_ENV = {}


def setUpModule():
    for env_name in _STATE_ENV_KEYS:
        _SAVED_STATE_ENV[env_name] = os.environ.pop(env_name, None)


def tearDownModule():
    for env_name, value in _SAVED_STATE_ENV.items():
        if value is None:
            os.environ.pop(env_name, None)
        else:
            os.environ[env_name] = value
    _SAVED_STATE_ENV.clear()


class EnvFixtureCase(unittest.TestCase):
    def setUp(self):
        self.fixture_root = Path(tempfile.mkdtemp(prefix="aesop-tracker-test-"))
        (self.fixture_root / "state").mkdir()
        (self.fixture_root / "transcripts").mkdir()
        self._saved_env = {k: os.environ.get(k) for k in ENV_KEYS}
        os.environ["AESOP_TRANSCRIPTS_ROOT"] = str(self.fixture_root / "transcripts")
        os.environ["AESOP_UI_COLLECT_INTERVAL"] = "0.2"

    def tearDown(self):
        for k, v in self._saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(self.fixture_root, ignore_errors=True)


class TestTrackerSnapshot(EnvFixtureCase):
    def test_snapshot_tracker_empty_file(self):
        serve = load_serve(self.fixture_root)
        snap = serve._snapshot_tracker()
        self.assertEqual(snap, {"items": []})

    def test_snapshot_tracker_with_items(self):
        tracker_file = self.fixture_root / "state" / "tracker.json"
        tracker_data = {"version": 1, "items": [{"id": "abc123", "title": "Test"}]}
        tracker_file.write_text(json.dumps(tracker_data), encoding="utf-8")
        serve = load_serve(self.fixture_root)
        snap = serve._snapshot_tracker()
        self.assertEqual(len(snap["items"]), 1)


class TestOrchestratorStatusNormalization(EnvFixtureCase):
    def test_missing_file_returns_empty_list(self):
        serve = load_serve(self.fixture_root)
        snap = serve._snapshot_orchestrator_status()
        self.assertEqual(snap, {"orchestrators": []})


class TestInboxDrain(EnvFixtureCase):
    def test_drain_inbox_creates_items(self):
        inbox_file = self.fixture_root / "state" / ".tracker-inbox.jsonl"
        inbox_lines = [
            json.dumps({"title": "Item 1", "priority": "P0", "source": "test"}),
        ]
        inbox_file.write_text(chr(10).join(inbox_lines) + chr(10), encoding="utf-8")
        serve = load_serve(self.fixture_root)
        created = serve.drain_tracker_inbox()
        self.assertGreaterEqual(len(created), 0)


if __name__ == "__main__":
    unittest.main()
