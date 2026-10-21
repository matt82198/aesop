#!/usr/bin/env python3
"""
Behavioural tests: toolchain_health must read the WATCHDOG heartbeat from the
CONDUCTOR_ROOT state directory, the one daemons/backup-fleet.sh writes, never
from AESOP_STATE_ROOT.

The resolution rule mirrors monitor/collect-signals.mjs resolveWatchdogHeartbeat:
CONDUCTOR_ROOT env wins; otherwise the sibling conductor3 is used only when
AESOP_ROOT's basename is exactly "aesop"; otherwise the beat is UNRESOLVED and
that is a finding, never fresh.

Each test drives run_checks() end to end with real files in temp directories.
No source-text assertions.
"""

import json
import os
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).parent.parent
TOOLS_DIR = REPO_ROOT / "tools"
sys.path.insert(0, str(TOOLS_DIR))
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import toolchain_health  # noqa: E402

WATCHDOG_FILE = ".watchdog-heartbeat"
ENV_KEYS = ("CONDUCTOR_ROOT", "AESOP_ROOT", "AESOP_STATE_ROOT")


def _write_beat(path: Path, epoch: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(str(epoch), encoding="utf-8")


class WatchdogHeartbeatRootTests(unittest.TestCase):
    """run_checks must judge the watchdog beat by the CONDUCTOR_ROOT rule."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.now = int(time.time())
        self.conductor = self.tmp / "conductor3"
        self.state_root = self.tmp / "aesop-state"
        self.elsewhere = self.tmp / "checkout-x"  # basename is not "aesop"
        self.state_root.mkdir(parents=True)
        self.elsewhere.mkdir(parents=True)
        self._env = mock.patch.dict(os.environ, {}, clear=False)
        self._env.start()
        for key in ENV_KEYS:
            os.environ.pop(key, None)

    def tearDown(self):
        self._env.stop()
        self._tmp.cleanup()

    def _run(self, env):
        """Run every check with binaries stubbed out; return the watchdog findings."""
        os.environ.update(env)
        with mock.patch.object(toolchain_health, "REQUIRED_BINARIES", {}):
            buf = StringIO()
            with redirect_stdout(buf):
                toolchain_health.run_checks(json_mode=True, max_age_seconds=300)
        report = json.loads(buf.getvalue())
        return [f for f in report["findings"] if f.get("file") == WATCHDOG_FILE]

    def test_stale_conductor_beat_is_not_masked_by_fresh_state_root_beat(self):
        """A fresh beat under AESOP_STATE_ROOT must not make a stale CONDUCTOR beat ok."""
        _write_beat(self.conductor / "state" / WATCHDOG_FILE, self.now - 5000)
        _write_beat(self.state_root / WATCHDOG_FILE, self.now)
        findings = self._run({
            "CONDUCTOR_ROOT": str(self.conductor),
            "AESOP_STATE_ROOT": str(self.state_root),
            "AESOP_ROOT": str(self.elsewhere),
        })
        self.assertEqual(len(findings), 1, "stale CONDUCTOR beat reported ok (wrong-root read)")
        self.assertEqual(findings[0]["type"], "HEARTBEAT_STALE")

    def test_fresh_conductor_beat_reports_ok(self):
        """A fresh beat under CONDUCTOR_ROOT/state is healthy, with no state_root beat."""
        _write_beat(self.conductor / "state" / WATCHDOG_FILE, self.now)
        findings = self._run({
            "CONDUCTOR_ROOT": str(self.conductor),
            "AESOP_STATE_ROOT": str(self.state_root),
            "AESOP_ROOT": str(self.elsewhere),
        })
        self.assertEqual(findings, [], "fresh CONDUCTOR beat was reported stale")

    def test_unresolved_watchdog_is_a_finding_even_with_fresh_state_root_beat(self):
        """No CONDUCTOR_ROOT and a non-canonical AESOP_ROOT: UNRESOLVED, never fresh."""
        _write_beat(self.state_root / WATCHDOG_FILE, self.now)
        findings = self._run({
            "AESOP_STATE_ROOT": str(self.state_root),
            "AESOP_ROOT": str(self.elsewhere),
        })
        self.assertEqual(len(findings), 1, "unresolved watchdog beat reported ok")
        self.assertIn("UNRESOLVED", findings[0]["message"])

    def test_sibling_conductor_derivation_for_canonical_aesop_root(self):
        """No CONDUCTOR_ROOT, AESOP_ROOT named exactly aesop: sibling conductor3 is read."""
        canonical = self.tmp / "aesop"
        canonical.mkdir()
        _write_beat(self.tmp / "conductor3" / "state" / WATCHDOG_FILE, self.now)
        findings = self._run({
            "AESOP_STATE_ROOT": str(self.state_root),
            "AESOP_ROOT": str(canonical),
        })
        self.assertEqual(findings, [], "sibling conductor3 beat was not read")


if __name__ == "__main__":
    unittest.main()
