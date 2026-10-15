"""Round-trip tests for state_rebuild --all / --check (F-02).

Drives the real CLI in a subprocess against a temp state root:
- --all must not overwrite a STATE.md that was written through WriteAPI.
- --check must exit 0 immediately after --all (timestamps excluded).
- Rebuild must use the single tracker DB (no stray events.db).
"""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from state_store.write_api import WriteAPI  # noqa: E402

REBUILD = str(ROOT / "tools" / "state_rebuild.py")
SEEDED = "# STATE\n\nNEXT STEPS: ship the thing\n"


def _run(args, state_root):
    env = dict(os.environ)
    env["AESOP_STATE_ROOT"] = str(state_root)
    return subprocess.run(
        [sys.executable, REBUILD, *args, "--state-root", str(state_root)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        env=env, cwd=str(ROOT), timeout=120,
    )


class StateRebuildRoundTripTest(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.state_root = Path(self._tmp.name) / "state"
        self.state_root.mkdir(parents=True)

    def tearDown(self):
        self._tmp.cleanup()

    def _seed_state_md(self):
        api = WriteAPI(self.state_root)
        try:
            api.write_state_md(SEEDED, actor="test")
        finally:
            api.close()

    def test_rebuild_all_preserves_written_state_md(self):
        self._seed_state_md()
        result = _run(["--all"], self.state_root)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            (self.state_root / "STATE.md").read_text(encoding="utf-8"), SEEDED
        )

    def test_check_passes_right_after_all(self):
        self._seed_state_md()
        self.assertEqual(_run(["--all"], self.state_root).returncode, 0)
        check = _run(["--check"], self.state_root)
        self.assertEqual(check.returncode, 0, check.stdout + check.stderr)

    def test_single_tracker_db_after_rebuild(self):
        self._seed_state_md()
        self.assertEqual(_run(["--all"], self.state_root).returncode, 0)
        dbs = sorted(p.name for p in self.state_root.glob("*.db"))
        self.assertEqual(dbs, ["tracker_events.db"])


if __name__ == "__main__":
    unittest.main()
