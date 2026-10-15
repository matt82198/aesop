"""Wave telemetry phase panel: the phase the orchestrator sets must reach the dashboard.

Live shape: aesop.config.json pins state_root, and the orchestrator runs its CLI from a
directory that is NOT the project root (a worktree or another cwd). Before the fix,
tools/common.get_state_dir() ignored the config and wrote to <cwd>/state, while the
dashboard read <AESOP_ROOT>/state -- so the panel showed "unknown". The orchestrator CLI
is run as a real subprocess; the dashboard side is ui/wave_telemetry.get_wave_telemetry().

Run: python -m unittest tests.test_wave_telemetry_phase
"""
import importlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
UI_PATH = REPO_ROOT / "ui"
ORCH_CLI = REPO_ROOT / "tools" / "orchestrator_status.py"
PHASE = "wave-rc.9: build"


class WaveTelemetryPhaseCase(unittest.TestCase):
    def setUp(self):
        self.base = Path(tempfile.mkdtemp(prefix="aesop-telemetry-phase-"))
        self.root = self.base / "project"
        self.elsewhere = self.base / "worktree"
        self.root.mkdir()
        self.elsewhere.mkdir()
        (self.root / "aesop.config.json").write_text(
            json.dumps({"state_root": "state"}), encoding="utf-8")
        (self.root / "STATE.md").write_text("# STATE\n\nNEXT STEPS: ship\n", encoding="utf-8")
        self._saved = {k: os.environ.get(k) for k in ("AESOP_ROOT", "AESOP_STATE_ROOT")}
        os.environ.pop("AESOP_STATE_ROOT", None)
        os.environ["AESOP_ROOT"] = str(self.root)

    def tearDown(self):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(self.base, ignore_errors=True)

    def test_phase_set_from_other_cwd_reaches_telemetry(self):
        env = dict(os.environ)
        env.pop("AESOP_STATE_ROOT", None)
        env["AESOP_ROOT"] = str(self.root)
        env["PYTHONIOENCODING"] = "utf-8"
        proc = subprocess.run(
            [sys.executable, str(ORCH_CLI), "set",
             "--activity", "dispatching", "--phase", PHASE],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            env=env, cwd=str(self.elsewhere))
        self.assertEqual(proc.returncode, 0, proc.stderr)

        # Pin the reader's freshness clock to the instant the CLI recorded, so the
        # outcome never depends on the test process's wall clock (a frozen or
        # skewed in-process clock made the 24h freshness check reject a fresh record).
        sys.path.insert(0, str(REPO_ROOT))
        try:
            from state_store.read_api import ReadAPI
            record = ReadAPI(str(self.root / "state")).read_orchestrator_status()
        finally:
            sys.path.remove(str(REPO_ROOT))
        self.assertIsNotNone(record, "CLI wrote no orchestrator status record")
        written = datetime.fromisoformat(record["updated_at"].replace("Z", "+00:00"))
        reference_now = written + timedelta(seconds=60)

        # Sibling tests (tests/test_ui_wave_context.py) leave a stub `config` in sys.modules and
        # wave_telemetry binds to whichever `config` was live at its import. Import both fresh so
        # this test reads the root it just configured, then restore whatever was cached.
        names = ("config", "wave_telemetry")
        saved = {name: sys.modules.pop(name, None) for name in names}
        sys.path.insert(0, str(UI_PATH))
        try:
            config = importlib.import_module("config")
            config.reload()
            wave_telemetry = importlib.import_module("wave_telemetry")
            telemetry = wave_telemetry.get_wave_telemetry(now=reference_now)
        finally:
            sys.path.remove(str(UI_PATH))
            for name in names:
                sys.modules.pop(name, None)
                if saved[name] is not None:
                    sys.modules[name] = saved[name]
        self.assertEqual(telemetry["phase"], PHASE.lower())
        self.assertNotEqual(telemetry["phase"], "unknown")


if __name__ == "__main__":
    unittest.main()
