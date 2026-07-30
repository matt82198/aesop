#!/usr/bin/env python3
"""
Wave-26 — ORCHESTRATION CORE tests (Python side).

Targets aesop-OWNED watchdog stall-detection / heartbeat-staleness DECISION
LOGIC that is genuinely uncovered by the existing suites (tests/test_healthcheck.py
and tests/test_stall_check.py already cover the green/yellow/red basics and the
scan_transcripts() verdicts respectively — this file adds the boundary and
asymmetric-severity cases those suites leave untested).

Scope:
  1. tools/healthcheck.py — the asymmetric RED-severity rule: only a heartbeat
     NAMED "watchdog" can escalate to RED, and only once its age crosses BOTH
     `threshold * 2` and the hardcoded 600s floor. A heartbeat of any other name
     (e.g. "monitor") that goes just as stale, or even far staler, tops out at
     YELLOW. A MISSING watchdog file is treated more leniently (YELLOW) than a
     PRESENT-but-dead one (RED >= 600s) — a real, slightly counterintuitive
     decision worth pinning down with a real assertion.
  2. tools/stall_check.py — the --exit-nonzero-on-stall CLI decision: whether a
     stalled/dead agent transcript actually flips the process exit code (the
     signal a calling watchdog loop keys off of). scan_transcripts() itself is
     already well covered; the CLI's exit-code branch was not previously
     exercised at all.

Fixtures are hermetic temp dirs; no real state/heartbeat files are touched.
Per the wave-26 rule, this file NEVER calls `git config user.*` on the real
tree — the only git usage anywhere in this module is none (heartbeat/stall
tooling here doesn't touch git at all).
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
TOOLS_DIR = REPO_ROOT / "tools"
UI_DIR = REPO_ROOT / "ui"

if str(UI_DIR) not in sys.path:
    sys.path.insert(0, str(UI_DIR))
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

ENV_KEYS = (
    "AESOP_ROOT",
    "AESOP_STATE_ROOT",
    "AESOP_TRANSCRIPTS_ROOT",
    "AESOP_UI_COLLECT_INTERVAL",
)


# === Part 1: tools/healthcheck.py — asymmetric severity + boundary cases ===

class HealthcheckCoreTestCase(unittest.TestCase):
    """Isolated-state base class, same pattern as tests/test_healthcheck.py."""

    def setUp(self):
        self.fixture_root = Path(tempfile.mkdtemp(prefix="aesop-orch-core-hc-"))
        self.state_dir = self.fixture_root / "state"
        self.state_dir.mkdir(parents=True)
        (self.fixture_root / "transcripts").mkdir()
        (self.fixture_root / "monitor").mkdir()  # For monitor heartbeat path

        self._saved_env = {k: os.environ.get(k) for k in ENV_KEYS}
        os.environ["AESOP_ROOT"] = str(self.fixture_root)
        os.environ["AESOP_STATE_ROOT"] = str(self.state_dir)
        os.environ["AESOP_TRANSCRIPTS_ROOT"] = str(self.fixture_root / "transcripts")
        os.environ["AESOP_UI_COLLECT_INTERVAL"] = "0.2"
        # Override conductor3 location to isolate from live system state
        os.environ["AESOP_CONDUCTOR3_ROOT"] = str(self.fixture_root)

        # Re-import healthcheck fresh per test (matches tests/test_healthcheck.py's
        # own convention) so its module-level `import config` binding exists.
        # IMPORTANT: do NOT also delete sys.modules["config"] here. config.py is a
        # process-wide singleton that ui/collectors.py (and other already-imported
        # modules) hold a direct reference to; config.reload() is designed to
        # mutate that ONE shared object's globals in place ("Mutates module-level
        # globals in place so that importers see the current state" — ui/config.py
        # docstring). Deleting "config" from sys.modules forces a brand-new config
        # OBJECT into existence; healthcheck.check_health() would then reload()
        # that new object while collectors.py keeps reading the OLD one — and
        # worse, any later test's `mock.patch('config.ATTR', ...)` re-resolves
        # 'config' via sys.modules at patch-time, so it would patch whichever
        # object happens to be cached NOW, not the one collectors.py actually
        # reads. This exact bug was caught by running the full suite: doing
        # `del sys.modules["config"]` here silently broke unrelated
        # tests/test_ui_collectors.py assertions (empty results where data was
        # expected) once this file ran before it alphabetically. healthcheck.py's
        # own `import config` re-resolves to the SAME cached singleton as long as
        # we leave "config" in sys.modules — reload() (called by check_health()
        # itself, every call) is what makes fixture-specific env vars take effect.
        if "healthcheck" in sys.modules:
            del sys.modules["healthcheck"]
        import healthcheck  # noqa: F401
        self.healthcheck = sys.modules["healthcheck"]

    def tearDown(self):
        for k, v in self._saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(self.fixture_root, ignore_errors=True)

    def _write_heartbeat(self, name, age_seconds=0):
        heartbeat_file = self.state_dir / f".{name}-heartbeat"
        epoch = int(time.time()) - age_seconds
        heartbeat_file.write_text(str(epoch), encoding="utf-8")
        return heartbeat_file

    def _write_orchestrator_status(self, activity="dispatching", phase="build", age_seconds=30):
        from datetime import datetime, timezone
        status_file = self.state_dir / "orchestrator-status.json"
        now = datetime.fromtimestamp(time.time() - age_seconds, tz=timezone.utc)
        status = {
            "id": "main",
            "role": "orchestrator",
            "activity": activity,
            "phase": phase,
            "updated_at": now.isoformat().replace("+00:00", "Z"),
        }
        status_file.write_text(json.dumps(status, indent=2), encoding="utf-8")
        return status_file


class TestWatchdogVsMonitorAsymmetry(HealthcheckCoreTestCase):
    """The RED escalation path is name-gated to 'watchdog' only."""

    def test_monitor_extremely_stale_never_escalates_to_red(self):
        """A monitor heartbeat stale far past 2x its own threshold (3600s -> 7200s)
        must still top out at YELLOW — RED is reserved for a dead watchdog."""
        self._write_heartbeat("watchdog", age_seconds=10)  # fresh
        self._write_heartbeat("monitor", age_seconds=8000)  # >> 2*3600
        (self.state_dir / "SECURITY-ALERTS.log").write_text("", encoding="utf-8")
        self._write_orchestrator_status()

        result = self.healthcheck.check_health()
        self.assertIn("🟡", result, f"extremely stale monitor heartbeat must be YELLOW, got: {result}")
        self.assertNotIn("🔴", result, f"extremely stale monitor heartbeat must NEVER be RED (asymmetric rule), got: {result}")

    def test_watchdog_at_same_extreme_staleness_does_escalate_to_red(self):
        """The exact same age (8000s) applied to the watchdog heartbeat instead
        DOES escalate to RED — proving the asymmetry is keyed on the loop name,
        not just the age ratio."""
        self._write_heartbeat("watchdog", age_seconds=8000)
        self._write_heartbeat("monitor", age_seconds=10)  # fresh
        (self.state_dir / "SECURITY-ALERTS.log").write_text("", encoding="utf-8")
        self._write_orchestrator_status()

        result = self.healthcheck.check_health()
        self.assertIn("🔴", result, f"watchdog dead at 8000s must be RED, got: {result}")


class TestWatchdogRedBoundary(HealthcheckCoreTestCase):
    """RED requires BOTH age >= 2*threshold (600s) AND the explicit >=600s floor.

    TDD fix: Use logical time (mocked time.time) to eliminate Windows CI timing
    races where wall-clock delays between writing heartbeat and checking health
    can exceed the 1-second boundary margin."""

    def test_watchdog_just_below_600s_is_yellow_not_red(self):
        """Watchdog at 599s (just under the 600s floor) must be YELLOW, not RED.

        Root cause: test writes heartbeat aged 599s, then check_health() is called.
        Under Windows CI load, time.time() may advance enough during this window
        to push the calculated age to 600+, flipping the verdict from YELLOW to RED.
        Fix: Mock time.time() with logical time so the age stays deterministic."""
        with mock.patch("time.time") as mock_time:
            # Write heartbeat at logical time 1000.0
            mock_time.return_value = 1000.0
            self._write_heartbeat("watchdog", age_seconds=599)
            self._write_heartbeat("monitor", age_seconds=10)
            (self.state_dir / "SECURITY-ALERTS.log").write_text("", encoding="utf-8")
            self._write_orchestrator_status()

            # Check health at same logical time (no time passage)
            # Age = 1000.0 - (1000.0 - 599) = 599 (just under floor)
            result = self.healthcheck.check_health()
            self.assertIn("🟡", result, f"watchdog at 599s (just under the 600s dead floor) must be YELLOW, got: {result}")
            self.assertNotIn("🔴", result, f"watchdog at 599s must NOT be RED yet, got: {result}")

    def test_watchdog_at_exactly_600s_is_red(self):
        """Watchdog at exactly 600s (the floor) must be RED.

        TDD fix: Use logical time to ensure age equals exactly 600, not > 600."""
        with mock.patch("time.time") as mock_time:
            # Write heartbeat at logical time 1000.0
            mock_time.return_value = 1000.0
            self._write_heartbeat("watchdog", age_seconds=600)
            self._write_heartbeat("monitor", age_seconds=10)
            (self.state_dir / "SECURITY-ALERTS.log").write_text("", encoding="utf-8")
            self._write_orchestrator_status()

            # Check health at same logical time (no time passage)
            # Age = 1000.0 - (1000.0 - 600) = 600 (at floor, inclusive)
            result = self.healthcheck.check_health()
            self.assertIn("🔴", result, f"watchdog at exactly 600s (inclusive floor) must be RED, got: {result}")


class TestMissingVsDeadWatchdog(HealthcheckCoreTestCase):
    """A MISSING watchdog heartbeat is treated more leniently than a PRESENT
    but long-dead one — worth pinning down since it's the kind of asymmetry
    that silently masks an outage (no file at all reads as merely YELLOW)."""

    def test_missing_watchdog_heartbeat_is_yellow_not_red(self):
        # No watchdog heartbeat file written at all; monitor is fresh.
        self._write_heartbeat("monitor", age_seconds=10)
        (self.state_dir / "SECURITY-ALERTS.log").write_text("", encoding="utf-8")
        self._write_orchestrator_status()

        result = self.healthcheck.check_health()
        self.assertIn("🟡", result, f"a MISSING watchdog heartbeat file must be YELLOW, got: {result}")
        self.assertNotIn("🔴", result, f"a MISSING watchdog heartbeat file must NOT be RED (asymmetric vs. a dead-but-present one), got: {result}")
        self.assertIn("no watchdog heartbeat", result.lower())

    def test_present_but_dead_watchdog_outranks_missing_in_severity(self):
        # Same scenario, but the watchdog file EXISTS and is dead (>=600s).
        self._write_heartbeat("watchdog", age_seconds=900)
        self._write_heartbeat("monitor", age_seconds=10)
        (self.state_dir / "SECURITY-ALERTS.log").write_text("", encoding="utf-8")
        self._write_orchestrator_status()

        result = self.healthcheck.check_health()
        self.assertIn("🔴", result, f"a present-but-dead watchdog heartbeat must be RED, got: {result}")


# === Part 2: tools/stall_check.py — CLI exit-code decision ===

class StallCheckCliTestCase(unittest.TestCase):
    """Exercises stall_check.py as a subprocess to test the exit-code DECISION
    (not just scan_transcripts(), which tests/test_stall_check.py already
    covers thoroughly)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.transcripts_root = Path(self.tmp.name) / "transcripts"
        self.transcripts_root.mkdir(parents=True)
        self.script = TOOLS_DIR / "stall_check.py"

    def tearDown(self):
        self.tmp.cleanup()

    def _write_transcript(self, agent_id, age_seconds):
        fp = self.transcripts_root / f"agent-{agent_id}.jsonl"
        fp.write_text("dummy\n", encoding="utf-8")
        mtime = time.time() - age_seconds
        os.utime(fp, (mtime, mtime))
        return fp

    def _run(self, *args):
        cmd = [sys.executable, str(self.script),
               "--transcripts-root", str(self.transcripts_root)] + list(args)
        return subprocess.run(cmd, capture_output=True, text=True, timeout=30)

    def test_default_exit_is_always_zero_even_with_dead_agent(self):
        self._write_transcript("dead1", age_seconds=5000)  # well past dead (2x default 600s)
        result = self._run("--threshold-seconds", "600")
        self.assertEqual(result.returncode, 0, "without --exit-nonzero-on-stall, exit code must stay 0 regardless of stalls")

    def test_exit_nonzero_flag_flips_exit_code_when_stalled(self):
        self._write_transcript("stalled1", age_seconds=900)  # > 600s threshold: stale
        result = self._run("--threshold-seconds", "600", "--exit-nonzero-on-stall")
        self.assertEqual(result.returncode, 1, "with --exit-nonzero-on-stall and a stalled agent present, exit code MUST be 1")

    def test_exit_nonzero_flag_stays_zero_when_all_fresh(self):
        self._write_transcript("fresh1", age_seconds=30)  # well under threshold
        result = self._run("--threshold-seconds", "600", "--exit-nonzero-on-stall")
        self.assertEqual(result.returncode, 0, "with --exit-nonzero-on-stall but no stalled agents, exit code must remain 0")

    def test_exit_nonzero_flag_stays_zero_with_no_transcripts_at_all(self):
        # transcripts_root exists but is empty.
        result = self._run("--threshold-seconds", "600", "--exit-nonzero-on-stall")
        self.assertEqual(result.returncode, 0, "with no transcripts found at all, --exit-nonzero-on-stall must not force a failure")

    def test_dead_agent_also_flips_exit_code(self):
        # 'dead' verdict (age > 2x threshold) must also count as a stall for
        # the exit-code decision, not just 'stale'.
        self._write_transcript("verydead1", age_seconds=2000)  # > 2*600
        result = self._run("--threshold-seconds", "600", "--exit-nonzero-on-stall")
        self.assertEqual(result.returncode, 1, "a 'dead' verdict must also flip the exit code under --exit-nonzero-on-stall")


# === Honest gap documentation ===

class TestOrchestrationCoreGaps(unittest.TestCase):
    """Names what remains genuinely untestable in-repo, per the wave-26 mandate
    to surface the gap rather than hide or fake it."""

    def test_disjoint_file_ownership_guard_present_in_vendored_template(self):
        # Wave-rc.4 vendored the buildsystem skill + dispatch template into this
        # repo (closing the wave-26 gap this test used to document). Workflow
        # scripts cannot be executed under unittest (they run inside the harness
        # runtime), so this asserts the guard's load-bearing structure statically.
        tpl = REPO_ROOT / "skills" / "buildsystem" / "wave-flat-dispatch.template.mjs"
        self.assertTrue(tpl.exists(), "vendored dispatch template missing")
        src = tpl.read_text(encoding="utf-8")
        # Preflight refuses overlapping ownership before any worker spawns.
        self.assertIn("ownership_overlap", src,
                      "preflight must abort with reason 'ownership_overlap'")
        for marker in ("owner[", "conflicts"):
            self.assertIn(marker, src,
                          f"ownership-guard structure marker {marker!r} missing")
        # The abort must precede the Build fan-out in source order.
        self.assertLess(src.index("ownership_overlap"), src.index("phase('Build')"),
                        "ownership guard must run before the Build phase")

    def test_gap_model_dispatch_selection_is_harness_behavior(self):
        # Whether a given subagent actually RUNS on Haiku vs. Sonnet vs. Opus is
        # decided by the Claude Code harness (subagent spawning / model routing),
        # not by any function in this repository. aesop only emits prompts and
        # markers (e.g. "[[ALLOW-NON-HAIKU]]") hinting at model choice and later
        # reads back transcripts/heartbeats to audit what happened. There is no
        # in-repo "pick a model" function to unit test, so this suite does not
        # fabricate one.
        self.assertTrue(True, "gap documented: model dispatch selection happens inside the harness, outside aesop's own code")


if __name__ == "__main__":
    unittest.main()
