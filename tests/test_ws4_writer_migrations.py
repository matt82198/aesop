#!/usr/bin/env python3
"""Tests for WS4 increment 2 — top-3 writer migration to the WriteAPI facade.

Verifies that tools/buildlog.py, tools/ensure_state.py, and tools/eod_sweep.py
write STATE.md / BUILDLOG.md through state_store.write_api.WriteAPI (the
unified write path): every markdown write also lands as an event in the
event store (state/tracker_events.db), and the on-disk markdown format stays
byte-compatible with the legacy direct writers.

TDD: these tests were written FIRST (failing against the direct-write
implementations) and drive the migration.
"""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from state_store import EventStore  # noqa: E402

TOOLS = ROOT / "tools"


class WriterMigrationBase(unittest.TestCase):
    """Shared fixture: isolated temp state dir + subprocess tool runner."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.state_dir = self.tmp / "state"

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_tool(self, script, *args, env_overrides=None):
        """Run a tools/ script in a subprocess with AESOP_STATE_ROOT isolated."""
        env = os.environ.copy()
        env["AESOP_STATE_ROOT"] = str(self.state_dir)
        if env_overrides:
            env.update(env_overrides)
        cmd = [sys.executable, str(TOOLS / script)] + [str(a) for a in args]
        return subprocess.run(
            cmd, capture_output=True, text=True, timeout=60,
            cwd=str(self.tmp), env=env,
        )

    def read_events(self, stream):
        """Read events from the isolated state dir's event store (must exist)."""
        db = self.state_dir / "tracker_events.db"
        self.assertTrue(
            db.exists(),
            f"unified write path must create the event store db at {db}",
        )
        return EventStore(str(db)).read(stream)


class BuildlogToolMigrationTest(WriterMigrationBase):
    """tools/buildlog.py must append BUILDLOG entries via WriteAPI."""

    def test_append_writes_event_and_file(self):
        result = self.run_tool("buildlog.py", "unified write path test")
        self.assertEqual(result.returncode, 0, msg=result.stderr)

        # File side: entry present
        content = (self.state_dir / "BUILDLOG.md").read_text(encoding="utf-8")
        self.assertIn("unified write path test", content)

        # Event side: buildlog stream carries the same line
        events = self.read_events("buildlog")
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["type"], "buildlog_entry")
        self.assertIn("unified write path test", events[0]["payload"]["line"])

    def test_header_format_preserved(self):
        """Legacy header '# Build Log (append-only)' must survive the migration, once."""
        self.run_tool("buildlog.py", "entry 1")
        self.run_tool("buildlog.py", "entry 2")

        content = (self.state_dir / "BUILDLOG.md").read_text(encoding="utf-8")
        lines = content.splitlines()
        self.assertEqual(lines[0], "# Build Log (append-only)")
        self.assertEqual(content.count("Build Log"), 1)

    def test_each_append_is_one_event(self):
        self.run_tool("buildlog.py", "first")
        self.run_tool("buildlog.py", "second")

        events = self.read_events("buildlog")
        self.assertEqual(len(events), 2)
        self.assertIn("first", events[0]["payload"]["line"])
        self.assertIn("second", events[1]["payload"]["line"])


class EnsureStateMigrationTest(WriterMigrationBase):
    """tools/ensure_state.py must scaffold STATE.md/BUILDLOG.md via WriteAPI."""

    def test_create_writes_state_md_event(self):
        result = self.run_tool("ensure_state.py", "--state-dir", self.state_dir)
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("CREATED STATE.md", result.stdout)

        state_content = (self.state_dir / "STATE.md").read_text(encoding="utf-8")
        events = self.read_events("state_markdown")
        written = [e for e in events if e["type"] == "state_md_written"]
        self.assertEqual(len(written), 1)
        self.assertEqual(written[0]["payload"]["content"], state_content)

    def test_create_writes_buildlog_event(self):
        result = self.run_tool("ensure_state.py", "--state-dir", self.state_dir)
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("CREATED BUILDLOG.md", result.stdout)

        events = self.read_events("buildlog")
        self.assertEqual(len(events), 1)
        self.assertIn("created ", events[0]["payload"]["line"])

    def test_buildlog_format_byte_compatible(self):
        """Header + 'created <iso>' line must match the legacy scaffold format."""
        self.run_tool("ensure_state.py", "--state-dir", self.state_dir)

        content = (self.state_dir / "BUILDLOG.md").read_text(encoding="utf-8")
        lines = content.splitlines()
        self.assertIn("BUILDLOG", lines[0])
        self.assertIn("append-only", lines[0])
        self.assertRegex(lines[1], r"^created \d{4}-\d{2}-\d{2}T")

    def test_idempotent_rerun_appends_no_events(self):
        self.run_tool("ensure_state.py", "--state-dir", self.state_dir)
        state_events = len(self.read_events("state_markdown"))
        buildlog_events = len(self.read_events("buildlog"))

        result = self.run_tool("ensure_state.py", "--state-dir", self.state_dir)
        self.assertEqual(result.returncode, 0)
        self.assertIn("EXISTS STATE.md", result.stdout)
        self.assertIn("EXISTS BUILDLOG.md", result.stdout)

        self.assertEqual(len(self.read_events("state_markdown")), state_events)
        self.assertEqual(len(self.read_events("buildlog")), buildlog_events)


class EodSweepMigrationTest(WriterMigrationBase):
    """tools/eod_sweep.py must append its verdict to BUILDLOG via WriteAPI."""

    def test_verdict_event_appended(self):
        buildlog_path = self.state_dir / "BUILDLOG.md"
        result = self.run_tool("eod_sweep.py", "--buildlog", buildlog_path)
        self.assertEqual(result.returncode, 0, msg=result.stdout + result.stderr)
        self.assertIn("EOD-SWEEP: SAFE", result.stdout)

        content = buildlog_path.read_text(encoding="utf-8")
        self.assertIn("EOD-SWEEP: SAFE", content)

        events = self.read_events("buildlog")
        self.assertEqual(len(events), 1)
        self.assertIn("EOD-SWEEP: SAFE", events[0]["payload"]["line"])

    def test_timestamp_entry_format_preserved(self):
        buildlog_path = self.state_dir / "BUILDLOG.md"
        result = self.run_tool(
            "eod_sweep.py", "--buildlog", buildlog_path,
            "--timestamp", "2026-07-29 12:00",
        )
        self.assertEqual(result.returncode, 0)

        content = buildlog_path.read_text(encoding="utf-8")
        self.assertIn("### [2026-07-29 12:00] EOD-SWEEP: SAFE", content)

        events = self.read_events("buildlog")
        self.assertIn("### [2026-07-29 12:00] EOD-SWEEP: SAFE",
                      events[0]["payload"]["line"])

    def test_noncanonical_buildlog_name_fails_closed(self):
        """WriteAPI owns the canonical BUILDLOG.md name; other names are refused
        with a warning (verdict exit code unaffected), never silently redirected."""
        odd_path = self.state_dir / "MYLOG.md"
        result = self.run_tool("eod_sweep.py", "--buildlog", odd_path)
        # Verdict still SAFE (BUILDLOG failure never changes the verdict)
        self.assertEqual(result.returncode, 0)
        self.assertIn("WARNING", result.stderr)
        self.assertFalse(odd_path.exists(), "non-canonical log name must not be written")


if __name__ == "__main__":
    unittest.main()
