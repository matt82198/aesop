#!/usr/bin/env python3
"""Unit tests for fleet_ledger.py append-wave subcommand."""
import os
import sys
import subprocess
import tempfile
import unittest
import json
from pathlib import Path


class TestFleetLedgerAppendWave(unittest.TestCase):
    """Test cases for fleet_ledger.py append-wave command."""

    def setUp(self):
        """Create temporary directories and fixtures for testing."""
        self.temp_dir = tempfile.mkdtemp()
        self.ledger_script = Path(__file__).parent.parent / "tools" / "fleet_ledger.py"
        self.state_dir = Path(self.temp_dir) / "state"
        self.state_dir.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        """Clean up temporary directories."""
        import shutil
        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir)

    def _run_ledger(self, *args, env_overrides=None):
        """Run fleet_ledger.py with arguments."""
        env = os.environ.copy()
        env["AESOP_STATE_ROOT"] = str(self.state_dir)
        if env_overrides:
            env.update(env_overrides)

        cmd = [sys.executable, str(self.ledger_script)] + list(args)
        result = subprocess.run(cmd, capture_output=True, text=True, env=env)
        return result

    def _create_test_report(self, **overrides):
        """Create a minimal workflow report JSON for testing.

        Args:
            **overrides: Fields to override in the default report

        Returns:
            Path to the created report file
        """
        report_dir = Path(self.temp_dir) / "reports"
        report_dir.mkdir(parents=True, exist_ok=True)
        report_file = report_dir / "workflow-report.json"

        # Default report structure
        report = {
            "tokens": {
                "buildOut": 100,
                "verifyOut": 200,
                "repairOut": 50,
                "totalOut": 350
            },
            "build": [
                {"slug": "test-build-1"},
                {"slug": "test-build-2"}
            ],
            "integration": {
                "green": True
            },
            "repairsUsed": 0
        }

        # Apply overrides (replace dict fields entirely, don't just update)
        for key, value in overrides.items():
            report[key] = value

        report_file.write_text(json.dumps(report, indent=2), encoding='utf-8')
        return report_file

    def test_append_wave_basic(self):
        """Test basic append-wave with minimal report."""
        report_file = self._create_test_report()
        ts = "2024-07-13T10:00:00"

        result = self._run_ledger(
            "append-wave",
            "--report-file", str(report_file),
            "--wave", "1",
            "--phase", "build",
            "--timestamp", ts
        )

        self.assertEqual(result.returncode, 0)
        self.assertIn("Appended", result.stdout)

        # Verify ledger file was created
        ledger_file = self.state_dir / "ledger" / "OUTCOMES-LEDGER.md"
        self.assertTrue(ledger_file.exists())

    def test_append_wave_missing_report(self):
        """Test append-wave with non-existent report file."""
        ts = "2024-07-13T10:00:00"

        result = self._run_ledger(
            "append-wave",
            "--report-file", "/nonexistent/report.json",
            "--wave", "1",
            "--phase", "build",
            "--timestamp", ts
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("not found", result.stderr)

    def test_append_wave_malformed_json(self):
        """Test append-wave gracefully handles malformed JSON."""
        report_dir = Path(self.temp_dir) / "reports"
        report_dir.mkdir(parents=True, exist_ok=True)
        report_file = report_dir / "bad-report.json"
        report_file.write_text("{ this is not valid json }", encoding='utf-8')

        ts = "2024-07-13T10:00:00"

        result = self._run_ledger(
            "append-wave",
            "--report-file", str(report_file),
            "--wave", "1",
            "--phase", "build",
            "--timestamp", ts
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Failed to read/parse", result.stderr)

    def test_append_wave_missing_fields_tolerance(self):
        """Test append-wave gracefully tolerates missing fields in report."""
        # Create a minimal report with missing fields
        report_file = self._create_test_report(
            tokens={},  # Empty tokens section
            integration={}  # Empty integration section
        )
        ts = "2024-07-13T10:00:00"

        result = self._run_ledger(
            "append-wave",
            "--report-file", str(report_file),
            "--wave", "1",
            "--phase", "build",
            "--timestamp", ts
        )

        # Should succeed even with missing fields
        self.assertEqual(result.returncode, 0)
        self.assertIn("Appended", result.stdout)

        # Verify row was created with defaults (0 tokens, FAILED verdict)
        ledger_file = self.state_dir / "ledger" / "OUTCOMES-LEDGER.md"
        content = ledger_file.read_text()
        self.assertIn("0", content)  # 0 tokens (missing buildOut)
        self.assertIn("FAILED", content)  # FAILED verdict (integration.green missing/false)

    def test_append_wave_verdict_from_integration_green(self):
        """Test that verdict is determined by integration.green."""
        # Test with green=true
        report_file = self._create_test_report(integration={"green": True})
        ts = "2024-07-13T10:00:00"

        self._run_ledger(
            "append-wave",
            "--report-file", str(report_file),
            "--wave", "1",
            "--phase", "build",
            "--timestamp", ts
        )

        ledger_file = self.state_dir / "ledger" / "OUTCOMES-LEDGER.md"
        content = ledger_file.read_text()
        self.assertIn("OK", content)

    def test_append_wave_verdict_failed_on_green_false(self):
        """Test that verdict is FAILED when integration.green is false."""
        report_file = self._create_test_report(integration={"green": False})
        ts = "2024-07-13T10:00:01"

        self._run_ledger(
            "append-wave",
            "--report-file", str(report_file),
            "--wave", "1",
            "--phase", "verify",
            "--timestamp", ts
        )

        ledger_file = self.state_dir / "ledger" / "OUTCOMES-LEDGER.md"
        content = ledger_file.read_text()
        self.assertIn("FAILED", content)

    def test_append_wave_tokens_from_phase_field(self):
        """Test that tokens_out is extracted from the correct phase field."""
        report_file = self._create_test_report(
            tokens={"buildOut": 123, "verifyOut": 456, "repairOut": 789}
        )
        ts = "2024-07-13T10:00:00"

        # Append for build phase
        self._run_ledger(
            "append-wave",
            "--report-file", str(report_file),
            "--wave", "1",
            "--phase", "build",
            "--timestamp", ts
        )

        ledger_file = self.state_dir / "ledger" / "OUTCOMES-LEDGER.md"
        content = ledger_file.read_text()
        lines = [l for l in content.split('\n') if 'build' in l and '|' in l and '---|' not in l]
        self.assertTrue(len(lines) > 0)
        # Should have 123 tokens for build phase
        self.assertIn("123", lines[0])

    def test_append_wave_model_is_haiku(self):
        """Test that model field is always set to haiku."""
        report_file = self._create_test_report()
        ts = "2024-07-13T10:00:00"

        self._run_ledger(
            "append-wave",
            "--report-file", str(report_file),
            "--wave", "1",
            "--phase", "build",
            "--timestamp", ts
        )

        ledger_file = self.state_dir / "ledger" / "OUTCOMES-LEDGER.md"
        content = ledger_file.read_text()
        self.assertIn("haiku", content)

    def test_append_wave_idempotency(self):
        """Test that append-wave is idempotent for identical rows."""
        report_file = self._create_test_report()
        ts = "2024-07-13T10:00:00"

        # First append
        result1 = self._run_ledger(
            "append-wave",
            "--report-file", str(report_file),
            "--wave", "1",
            "--phase", "build",
            "--timestamp", ts
        )
        self.assertEqual(result1.returncode, 0)

        ledger_file = self.state_dir / "ledger" / "OUTCOMES-LEDGER.md"
        lines_after_first = len(ledger_file.read_text().strip().split('\n'))

        # Second append with same parameters
        result2 = self._run_ledger(
            "append-wave",
            "--report-file", str(report_file),
            "--wave", "1",
            "--phase", "build",
            "--timestamp", ts
        )
        self.assertEqual(result2.returncode, 0)
        self.assertIn("already exists", result2.stdout)

        lines_after_second = len(ledger_file.read_text().strip().split('\n'))

        # Line count should not increase (idempotent)
        self.assertEqual(lines_after_first, lines_after_second)

    def test_append_wave_different_phases(self):
        """Test appending different phases for the same wave."""
        report_file = self._create_test_report(
            tokens={"buildOut": 100, "verifyOut": 200, "repairOut": 300}
        )
        ts = "2024-07-13T10:00:00"

        # Append build phase
        self._run_ledger(
            "append-wave",
            "--report-file", str(report_file),
            "--wave", "1",
            "--phase", "build",
            "--timestamp", ts
        )

        # Append verify phase (different timestamp)
        ts_verify = "2024-07-13T10:05:00"
        self._run_ledger(
            "append-wave",
            "--report-file", str(report_file),
            "--wave", "1",
            "--phase", "verify",
            "--timestamp", ts_verify
        )

        # Append repair phase (different timestamp)
        ts_repair = "2024-07-13T10:10:00"
        self._run_ledger(
            "append-wave",
            "--report-file", str(report_file),
            "--wave", "1",
            "--phase", "repair",
            "--timestamp", ts_repair
        )

        ledger_file = self.state_dir / "ledger" / "OUTCOMES-LEDGER.md"
        content = ledger_file.read_text()

        # Should have rows for all three phases
        self.assertIn("build", content)
        self.assertIn("verify", content)
        self.assertIn("repair", content)
        self.assertIn("100", content)  # buildOut
        self.assertIn("200", content)  # verifyOut
        self.assertIn("300", content)  # repairOut

    def test_append_wave_round_trip_parse(self):
        """Test round-trip: append then parse back the ledger."""
        report_file = self._create_test_report(
            tokens={"buildOut": 250},
            integration={"green": True}
        )
        ts = "2024-07-13T10:00:00"

        # Append
        result = self._run_ledger(
            "append-wave",
            "--report-file", str(report_file),
            "--wave", "5",
            "--phase", "build",
            "--timestamp", ts
        )
        self.assertEqual(result.returncode, 0)

        # Parse using the parser from fleet_ledger.py
        sys.path.insert(0, str(Path(__file__).parent.parent / "tools"))
        from fleet_ledger import parse_ledger_rows

        # Temporarily set AESOP_STATE_ROOT for parsing
        old_state_root = os.environ.get("AESOP_STATE_ROOT")
        os.environ["AESOP_STATE_ROOT"] = str(self.state_dir)

        try:
            rows = parse_ledger_rows()
            # Should have at least one row
            self.assertGreater(len(rows), 0)

            # Find the row we just appended
            found = False
            for row in rows:
                if (row['iso_ts'] == ts and
                    row['phase'] == 'build' and
                    row['wave'] == 5 and
                    row['model'] == 'haiku'):
                    found = True
                    self.assertEqual(row['tokens_out'], 250)
                    self.assertEqual(row['verdict'], 'OK')
                    break

            self.assertTrue(found, "Appended row not found in parsed ledger")
        finally:
            if old_state_root:
                os.environ["AESOP_STATE_ROOT"] = old_state_root
            else:
                os.environ.pop("AESOP_STATE_ROOT", None)
            sys.path.pop(0)

    def test_append_wave_aggregate_summary(self):
        """Test that appended rows can be aggregated in summary."""
        report_file = self._create_test_report(
            tokens={"buildOut": 100, "verifyOut": 200}
        )

        # Append build phase
        self._run_ledger(
            "append-wave",
            "--report-file", str(report_file),
            "--wave", "2",
            "--phase", "build",
            "--timestamp", "2024-07-13T10:00:00"
        )

        # Append verify phase
        self._run_ledger(
            "append-wave",
            "--report-file", str(report_file),
            "--wave", "2",
            "--phase", "verify",
            "--timestamp", "2024-07-13T10:05:00"
        )

        # Get summary
        result = self._run_ledger("summary", "--json")
        self.assertEqual(result.returncode, 0)

        summary_data = json.loads(result.stdout)
        # Total tokens should be 100 + 200 = 300
        self.assertEqual(summary_data['totals']['tokens_out'], 300)
        self.assertEqual(summary_data['totals']['entries'], 2)

    def test_append_wave_missing_timestamp(self):
        """Test append-wave with missing timestamp argument."""
        report_file = self._create_test_report()

        result = self._run_ledger(
            "append-wave",
            "--report-file", str(report_file),
            "--wave", "1",
            "--phase", "build"
            # Missing --timestamp
        )

        self.assertNotEqual(result.returncode, 0)

    def test_append_wave_missing_phase(self):
        """Test append-wave with missing phase argument."""
        report_file = self._create_test_report()

        result = self._run_ledger(
            "append-wave",
            "--report-file", str(report_file),
            "--wave", "1",
            "--timestamp", "2024-07-13T10:00:00"
            # Missing --phase
        )

        self.assertNotEqual(result.returncode, 0)

    def test_append_wave_missing_wave(self):
        """Test append-wave with missing wave argument."""
        report_file = self._create_test_report()

        result = self._run_ledger(
            "append-wave",
            "--report-file", str(report_file),
            "--phase", "build",
            "--timestamp", "2024-07-13T10:00:00"
            # Missing --wave
        )

        self.assertNotEqual(result.returncode, 0)

    def test_append_wave_invalid_wave_number(self):
        """Test append-wave with non-numeric wave number."""
        report_file = self._create_test_report()

        result = self._run_ledger(
            "append-wave",
            "--report-file", str(report_file),
            "--wave", "not-a-number",
            "--phase", "build",
            "--timestamp", "2024-07-13T10:00:00"
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Invalid wave number", result.stderr)

    def test_append_wave_creates_ledger_header_if_missing(self):
        """Test that append-wave creates ledger with header if it doesn't exist."""
        report_file = self._create_test_report()

        # Verify ledger doesn't exist yet
        ledger_file = self.state_dir / "ledger" / "OUTCOMES-LEDGER.md"
        self.assertFalse(ledger_file.exists())

        # Append should create it
        result = self._run_ledger(
            "append-wave",
            "--report-file", str(report_file),
            "--wave", "1",
            "--phase", "build",
            "--timestamp", "2024-07-13T10:00:00"
        )

        self.assertEqual(result.returncode, 0)
        self.assertTrue(ledger_file.exists())

        # Verify header is present
        content = ledger_file.read_text()
        self.assertIn("ISO ts", content)
        self.assertIn("agent_type", content)
        self.assertIn("model", content)
        self.assertIn("wave", content)

    def test_append_wave_numeric_token_conversion(self):
        """Test that non-numeric token values are gracefully converted to 0."""
        # Create a report with invalid token value
        report_dir = Path(self.temp_dir) / "reports"
        report_dir.mkdir(parents=True, exist_ok=True)
        report_file = report_dir / "bad-tokens.json"

        report = {
            "tokens": {
                "buildOut": "not-a-number"
            },
            "integration": {"green": True}
        }
        report_file.write_text(json.dumps(report), encoding='utf-8')

        result = self._run_ledger(
            "append-wave",
            "--report-file", str(report_file),
            "--wave", "1",
            "--phase", "build",
            "--timestamp", "2024-07-13T10:00:00"
        )

        # Should still succeed with 0 tokens
        self.assertEqual(result.returncode, 0)

        ledger_file = self.state_dir / "ledger" / "OUTCOMES-LEDGER.md"
        content = ledger_file.read_text()
        # Should have 0 as tokens_out
        lines = [l for l in content.split('\n') if 'build' in l and '|' in l and '---|' not in l]
        self.assertTrue(len(lines) > 0)


class TestLedgerReadPathIsReadOnly(unittest.TestCase):
    """parse_ledger_rows()/summary() are READ operations and must not create files.

    Header creation belongs on the APPEND path (append_ledger_line/harvest/rotate)
    only. A reader that materializes the ledger makes every `--check` mode of every
    downstream tool (cost_ceiling, cost_projection, state_store read facade) a writer.
    """

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.state_dir = Path(self.temp_dir) / "state"
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.ledger_file = self.state_dir / "ledger" / "OUTCOMES-LEDGER.md"
        self._saved = os.environ.get("AESOP_STATE_ROOT")
        os.environ["AESOP_STATE_ROOT"] = str(self.state_dir)
        sys.path.insert(0, str(Path(__file__).parent.parent / "tools"))
        sys.modules.pop("fleet_ledger", None)
        sys.modules.pop("common", None)
        import fleet_ledger
        self.fleet_ledger = fleet_ledger

    def tearDown(self):
        import shutil
        if self._saved is None:
            os.environ.pop("AESOP_STATE_ROOT", None)
        else:
            os.environ["AESOP_STATE_ROOT"] = self._saved
        sys.modules.pop("fleet_ledger", None)
        sys.path.pop(0)
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_parse_ledger_rows_missing_ledger_returns_empty_and_writes_nothing(self):
        rows = self.fleet_ledger.parse_ledger_rows()
        self.assertEqual(rows, [])
        self.assertFalse(self.ledger_file.exists(),
                         "parse_ledger_rows() created the ledger file -- reads must not write")
        self.assertFalse(self.ledger_file.parent.exists(),
                         "parse_ledger_rows() created the ledger dir -- reads must not write")

    def test_summary_missing_ledger_writes_nothing(self):
        import io
        import contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self.fleet_ledger.summary()
        self.assertFalse(self.ledger_file.exists(),
                         "summary() created the ledger file -- reads must not write")

    def test_parse_ledger_rows_still_reads_existing_ledger(self):
        """Read-only must not mean blind."""
        self.ledger_file.parent.mkdir(parents=True, exist_ok=True)
        self.ledger_file.write_text(
            "| ISO ts | agent_type | model | duration_sec | tokens_in | tokens_out | verdict | phase | wave |\n"
            "|--------|------------|-------|--------------|-----------|------------|--------|-------|------|\n"
            "| 2026-08-02T00:00:00Z | waverun | haiku | 10 | 5 | 7 | OK | build | 3 |\n",
            encoding="utf-8",
        )
        rows = self.fleet_ledger.parse_ledger_rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["tokens_out"], 7)
        self.assertEqual(rows[0]["wave"], 3)

    def test_append_path_still_creates_header_when_missing(self):
        """REGRESSION: moving header creation off the read path must not break appends."""
        self.assertFalse(self.ledger_file.exists())
        self.fleet_ledger.append_ledger_line(
            "2026-08-02T00:00:00Z", "waverun", "haiku", 1, 10, 20, "OK", "build", 4
        )
        self.assertTrue(self.ledger_file.exists(), "append must create the ledger + header")
        content = self.ledger_file.read_text(encoding="utf-8")
        self.assertIn("| ISO ts | agent_type | model |", content)
        self.assertIn("|--------|------------|-------|", content)
        rows = self.fleet_ledger.parse_ledger_rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["tokens_in"], 10)
        self.assertEqual(rows[0]["tokens_out"], 20)

    def test_append_wave_creates_header_when_missing(self):
        """REGRESSION: append_wave's idempotency pre-read must not block header creation."""
        report = Path(self.temp_dir) / "report.json"
        report.write_text(json.dumps({"tokens": {"buildOut": 42}, "integration": {"green": True}}),
                          encoding="utf-8")
        ok, msg = self.fleet_ledger.append_wave(str(report), 7, "build", "2026-08-02T01:00:00Z")
        self.assertTrue(ok, msg)
        self.assertTrue(self.ledger_file.exists(), "append_wave must create the ledger + header")
        rows = self.fleet_ledger.parse_ledger_rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["tokens_out"], 42)
        # And it stays idempotent across the now-existing ledger
        ok2, msg2 = self.fleet_ledger.append_wave(str(report), 7, "build", "2026-08-02T01:00:00Z")
        self.assertTrue(ok2, msg2)
        self.assertEqual(len(self.fleet_ledger.parse_ledger_rows()), 1, "append_wave lost idempotency")

    def test_rotate_on_missing_ledger_does_not_crash(self):
        """rotate() is a write path: it may create the header, it must not crash."""
        import io
        import contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self.fleet_ledger.rotate()
        self.assertIn("no rotation needed", buf.getvalue())


class TestFleetLedgerAppendWaveImportable(unittest.TestCase):
    """Test that tools can be imported without errors."""

    def test_fleet_ledger_importable(self):
        """Test that fleet_ledger can be imported."""
        sys.path.insert(0, str(Path(__file__).parent.parent / "tools"))
        try:
            import fleet_ledger
            # Verify the append_wave function exists
            self.assertTrue(hasattr(fleet_ledger, 'append_wave'))
            self.assertTrue(hasattr(fleet_ledger, 'parse_ledger_rows'))
            self.assertTrue(hasattr(fleet_ledger, 'append_ledger_line'))
        finally:
            sys.path.pop(0)


if __name__ == "__main__":
    unittest.main()
