#!/usr/bin/env python3
"""Unit tests for fleet_ledger.py injection/idempotency vulnerabilities."""
import os
import sys
import subprocess
import tempfile
import unittest
import json
from pathlib import Path


class TestFleetLedgerInjectionTimestamp(unittest.TestCase):
    """Test cases for iso_ts injection vulnerability fix."""

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
        """Create a minimal workflow report JSON for testing."""
        report_dir = Path(self.temp_dir) / "reports"
        report_dir.mkdir(parents=True, exist_ok=True)
        report_file = report_dir / "workflow-report.json"

        report = {
            "tokens": {"buildOut": 100},
            "integration": {"green": True}
        }
        for key, value in overrides.items():
            report[key] = value

        report_file.write_text(json.dumps(report), encoding='utf-8')
        return report_file

    def test_timestamp_with_payload_rejected(self):
        """Test that timestamp with concatenated payload is rejected."""
        report_file = self._create_test_report()
        # Payload injection: newline+data that would corrupt timestamp field when stripped
        malicious_ts = "2024-07-13T10:00:00\nfake_row"

        result = self._run_ledger(
            "append-wave",
            "--report-file", str(report_file),
            "--wave", "1",
            "--phase", "build",
            "--timestamp", malicious_ts
        )

        # Should REJECT with non-zero exit or mark error
        # The proper fix should reject this as invalid ISO timestamp
        if result.returncode == 0:
            # If it did append, verify the timestamp is valid ISO format
            sys.path.insert(0, str(Path(__file__).parent.parent / "tools"))
            try:
                from fleet_ledger import parse_ledger_rows
            finally:
                sys.path.pop(0)

            old_state_root = os.environ.get("AESOP_STATE_ROOT")
            os.environ["AESOP_STATE_ROOT"] = str(self.state_dir)

            try:
                rows = parse_ledger_rows()
                for row in rows:
                    if row['wave'] == 1:
                        # Timestamp must be valid ISO 8601 format
                        # After fix: should match YYYY-MM-DDTHH:MM:SS pattern (with optional timezone)
                        iso_pattern = r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}'
                        self.assertRegex(row['iso_ts'], iso_pattern,
                            f"Timestamp {row['iso_ts']} is not valid ISO 8601 format")
                        # Critical: no payload data should be in the timestamp
                        self.assertNotIn('fake_row', row['iso_ts'],
                            "Payload data found in timestamp - injection successful!")
            finally:
                if old_state_root:
                    os.environ["AESOP_STATE_ROOT"] = old_state_root
                else:
                    os.environ.pop("AESOP_STATE_ROOT", None)

    def test_timestamp_pipe_rejected(self):
        """Test that pipe in timestamp is rejected."""
        report_file = self._create_test_report()
        malicious_ts = "2024-07-13T10:00:00 | injected_column"

        result = self._run_ledger(
            "append-wave",
            "--report-file", str(report_file),
            "--wave", "1",
            "--phase", "build",
            "--timestamp", malicious_ts
        )

        # Should REJECT with error (pipe is forbidden character in ISO timestamp)
        self.assertNotEqual(result.returncode, 0, "Invalid timestamp should be rejected")
        self.assertIn("Timestamp contains forbidden characters", result.stdout + result.stderr,
                      "Should indicate timestamp validation failed")
        # Verify error goes to stderr, not stdout (shell-scripting semantics)
        self.assertIn("Timestamp contains forbidden characters", result.stderr,
                      "Error message must be on stderr")
        self.assertNotIn("Timestamp contains forbidden characters", result.stdout,
                      "Error message should not be on stdout")

        # Verify no rows were created
        sys.path.insert(0, str(Path(__file__).parent.parent / "tools"))
        try:
            from fleet_ledger import parse_ledger_rows
        finally:
            sys.path.pop(0)

        old_state_root = os.environ.get("AESOP_STATE_ROOT")
        os.environ["AESOP_STATE_ROOT"] = str(self.state_dir)

        try:
            rows = parse_ledger_rows()
            self.assertEqual(len(rows), 0, "Invalid timestamp should not be written to ledger")
        finally:
            if old_state_root:
                os.environ["AESOP_STATE_ROOT"] = old_state_root
            else:
                os.environ.pop("AESOP_STATE_ROOT", None)

    def test_timestamp_newline_rejected(self):
        """Test that newline in timestamp is rejected (prevents injection)."""
        report_file = self._create_test_report()
        malicious_ts = "2024-07-13T10:00:00\n| fake_row | fake_model | 0 | 0 | 0 | FAKE | phase | 99 |"

        result = self._run_ledger(
            "append-wave",
            "--report-file", str(report_file),
            "--wave", "1",
            "--phase", "build",
            "--timestamp", malicious_ts
        )

        # Should REJECT with error
        self.assertNotEqual(result.returncode, 0, "Invalid timestamp should be rejected")
        self.assertIn("Timestamp contains forbidden characters", result.stdout + result.stderr,
                      "Should indicate timestamp validation failed")

        # Verify no rows were created from invalid input
        sys.path.insert(0, str(Path(__file__).parent.parent / "tools"))
        try:
            from fleet_ledger import parse_ledger_rows
        finally:
            sys.path.pop(0)

        old_state_root = os.environ.get("AESOP_STATE_ROOT")
        os.environ["AESOP_STATE_ROOT"] = str(self.state_dir)

        try:
            rows = parse_ledger_rows()
            # Should have no rows (invalid input rejected)
            self.assertEqual(len(rows), 0, "Invalid timestamp should not be written to ledger")
        finally:
            if old_state_root:
                os.environ["AESOP_STATE_ROOT"] = old_state_root
            else:
                os.environ.pop("AESOP_STATE_ROOT", None)

    def test_timestamp_carriage_return_rejected(self):
        """Test that carriage return in timestamp is rejected."""
        report_file = self._create_test_report()
        malicious_ts = "2024-07-13T10:00:00\r\ninjected"

        result = self._run_ledger(
            "append-wave",
            "--report-file", str(report_file),
            "--wave", "1",
            "--phase", "build",
            "--timestamp", malicious_ts
        )

        # Should REJECT with error
        self.assertNotEqual(result.returncode, 0, "Invalid timestamp should be rejected")
        self.assertIn("Timestamp contains forbidden characters", result.stdout + result.stderr,
                      "Should indicate timestamp validation failed")

        # Verify no rows were created
        sys.path.insert(0, str(Path(__file__).parent.parent / "tools"))
        try:
            from fleet_ledger import parse_ledger_rows
        finally:
            sys.path.pop(0)

        old_state_root = os.environ.get("AESOP_STATE_ROOT")
        os.environ["AESOP_STATE_ROOT"] = str(self.state_dir)

        try:
            rows = parse_ledger_rows()
            self.assertEqual(len(rows), 0, "Invalid timestamp should not be written")
        finally:
            if old_state_root:
                os.environ["AESOP_STATE_ROOT"] = old_state_root
            else:
                os.environ.pop("AESOP_STATE_ROOT", None)


class TestFleetLedgerInjectionIdempotency(unittest.TestCase):
    """Test cases for idempotency bypass vulnerability fix."""

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
        """Create a minimal workflow report JSON for testing."""
        report_dir = Path(self.temp_dir) / "reports"
        report_dir.mkdir(parents=True, exist_ok=True)
        report_file = report_dir / "workflow-report.json"

        report = {
            "tokens": {"buildOut": 100},
            "integration": {"green": True}
        }
        for key, value in overrides.items():
            report[key] = value

        report_file.write_text(json.dumps(report), encoding='utf-8')
        return report_file

    def test_long_phase_no_duplicate_on_retry(self):
        """Test that a phase >15 chars does not create duplicate rows on retry."""
        report_file = self._create_test_report()
        # Phase longer than 15 chars (limit in append_ledger_line)
        long_phase = "verification_step_extended_name"
        ts = "2024-07-13T10:00:00"

        # First append
        result1 = self._run_ledger(
            "append-wave",
            "--report-file", str(report_file),
            "--wave", "1",
            "--phase", long_phase,
            "--timestamp", ts
        )
        self.assertEqual(result1.returncode, 0)

        # Get ledger after first append
        ledger_file = self.state_dir / "ledger" / "OUTCOMES-LEDGER.md"
        lines_after_first = len([l for l in ledger_file.read_text().split('\n') if l.strip() and '|' in l and '---|' not in l])

        # Second append with same parameters (should be idempotent)
        result2 = self._run_ledger(
            "append-wave",
            "--report-file", str(report_file),
            "--wave", "1",
            "--phase", long_phase,
            "--timestamp", ts
        )
        self.assertEqual(result2.returncode, 0)
        self.assertIn("already exists", result2.stdout)

        # Get ledger after second append
        lines_after_second = len([l for l in ledger_file.read_text().split('\n') if l.strip() and '|' in l and '---|' not in l])

        # No new row should be created (idempotent)
        self.assertEqual(lines_after_first, lines_after_second)

    def test_phase_with_pipe_rejected(self):
        """Test that a phase with pipe is rejected."""
        report_file = self._create_test_report()
        # Phase with pipe character (forbidden)
        phase_with_pipe = "build|malicious"
        ts = "2024-07-13T10:00:00"

        # Try to append
        result = self._run_ledger(
            "append-wave",
            "--report-file", str(report_file),
            "--wave", "2",
            "--phase", phase_with_pipe,
            "--timestamp", ts
        )

        # Should REJECT because phase contains forbidden character
        self.assertNotEqual(result.returncode, 0, "Phase with pipe should be rejected")
        self.assertIn("Phase contains forbidden characters", result.stdout + result.stderr,
                      "Should indicate phase validation failed")

        # Verify no rows were created
        sys.path.insert(0, str(Path(__file__).parent.parent / "tools"))
        try:
            from fleet_ledger import parse_ledger_rows
        finally:
            sys.path.pop(0)

        old_state_root = os.environ.get("AESOP_STATE_ROOT")
        os.environ["AESOP_STATE_ROOT"] = str(self.state_dir)

        try:
            rows = parse_ledger_rows()
            self.assertEqual(len(rows), 0, "Invalid phase should not be written to ledger")
        finally:
            if old_state_root:
                os.environ["AESOP_STATE_ROOT"] = old_state_root
            else:
                os.environ.pop("AESOP_STATE_ROOT", None)

    def test_timestamp_with_pipe_rejected(self):
        """Test that a timestamp with pipe is rejected."""
        report_file = self._create_test_report()
        # Timestamp with pipe character (forbidden)
        ts_with_pipe = "2024-07-13T10:00:00 | extra_column"

        # Try to append
        result = self._run_ledger(
            "append-wave",
            "--report-file", str(report_file),
            "--wave", "3",
            "--phase", "build",
            "--timestamp", ts_with_pipe
        )

        # Should REJECT because timestamp contains forbidden character
        self.assertNotEqual(result.returncode, 0, "Timestamp with pipe should be rejected")
        self.assertIn("Timestamp contains forbidden characters", result.stdout + result.stderr,
                      "Should indicate timestamp validation failed")

        # Verify no rows were created
        sys.path.insert(0, str(Path(__file__).parent.parent / "tools"))
        try:
            from fleet_ledger import parse_ledger_rows
        finally:
            sys.path.pop(0)

        old_state_root = os.environ.get("AESOP_STATE_ROOT")
        os.environ["AESOP_STATE_ROOT"] = str(self.state_dir)

        try:
            rows = parse_ledger_rows()
            self.assertEqual(len(rows), 0, "Invalid timestamp should not be written to ledger")
        finally:
            if old_state_root:
                os.environ["AESOP_STATE_ROOT"] = old_state_root
            else:
                os.environ.pop("AESOP_STATE_ROOT", None)

    def test_long_phase_with_pipe_rejected(self):
        """Test that a long phase with pipe is rejected."""
        report_file = self._create_test_report()
        # Both long and contains pipe
        complex_phase = "verification_step_extended_name|malicious"
        ts = "2024-07-13T10:00:00"

        # Try to append
        result = self._run_ledger(
            "append-wave",
            "--report-file", str(report_file),
            "--wave", "4",
            "--phase", complex_phase,
            "--timestamp", ts
        )

        # Should REJECT because phase contains forbidden character
        self.assertNotEqual(result.returncode, 0, "Phase with pipe should be rejected")
        self.assertIn("Phase contains forbidden characters", result.stdout + result.stderr,
                      "Should indicate phase validation failed")

        # Verify no rows were created
        sys.path.insert(0, str(Path(__file__).parent.parent / "tools"))
        try:
            from fleet_ledger import parse_ledger_rows
        finally:
            sys.path.pop(0)

        old_state_root = os.environ.get("AESOP_STATE_ROOT")
        os.environ["AESOP_STATE_ROOT"] = str(self.state_dir)

        try:
            rows = parse_ledger_rows()
            self.assertEqual(len(rows), 0, "Invalid phase should not be written to ledger")
        finally:
            if old_state_root:
                os.environ["AESOP_STATE_ROOT"] = old_state_root
            else:
                os.environ.pop("AESOP_STATE_ROOT", None)


class TestFleetLedgerInjectionRoundTrip(unittest.TestCase):
    """Test that injection fixes don't break normal round-trip behavior."""

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
        """Create a minimal workflow report JSON for testing."""
        report_dir = Path(self.temp_dir) / "reports"
        report_dir.mkdir(parents=True, exist_ok=True)
        report_file = report_dir / "workflow-report.json"

        report = {
            "tokens": {"buildOut": 100},
            "integration": {"green": True}
        }
        for key, value in overrides.items():
            report[key] = value

        report_file.write_text(json.dumps(report), encoding='utf-8')
        return report_file

    def test_normal_timestamp_still_works(self):
        """Test that normal clean timestamps still append and round-trip correctly."""
        report_file = self._create_test_report()
        ts = "2024-07-13T10:00:00"

        result = self._run_ledger(
            "append-wave",
            "--report-file", str(report_file),
            "--wave", "5",
            "--phase", "build",
            "--timestamp", ts
        )

        self.assertEqual(result.returncode, 0)

        # Parse and verify round-trip
        sys.path.insert(0, str(Path(__file__).parent.parent / "tools"))
        try:
            from fleet_ledger import parse_ledger_rows
        finally:
            sys.path.pop(0)

        old_state_root = os.environ.get("AESOP_STATE_ROOT")
        os.environ["AESOP_STATE_ROOT"] = str(self.state_dir)

        try:
            rows = parse_ledger_rows()
            found = False
            for row in rows:
                if row['iso_ts'] == ts and row['wave'] == 5 and row['phase'] == 'build':
                    found = True
                    self.assertEqual(row['tokens_out'], 100)
                    break

            self.assertTrue(found)
        finally:
            if old_state_root:
                os.environ["AESOP_STATE_ROOT"] = old_state_root
            else:
                os.environ.pop("AESOP_STATE_ROOT", None)

    def test_normal_phase_still_works(self):
        """Test that normal phases still append and round-trip correctly."""
        report_file = self._create_test_report(
            tokens={"buildOut": 100, "verifyOut": 200}
        )
        ts = "2024-07-13T10:00:00"
        phase = "verify"

        result = self._run_ledger(
            "append-wave",
            "--report-file", str(report_file),
            "--wave", "6",
            "--phase", phase,
            "--timestamp", ts
        )

        self.assertEqual(result.returncode, 0)

        # Parse and verify round-trip
        sys.path.insert(0, str(Path(__file__).parent.parent / "tools"))
        try:
            from fleet_ledger import parse_ledger_rows
        finally:
            sys.path.pop(0)

        old_state_root = os.environ.get("AESOP_STATE_ROOT")
        os.environ["AESOP_STATE_ROOT"] = str(self.state_dir)

        try:
            rows = parse_ledger_rows()
            found = False
            for row in rows:
                if row['iso_ts'] == ts and row['wave'] == 6 and row['phase'] == phase:
                    found = True
                    self.assertEqual(row['tokens_out'], 200)
                    break

            self.assertTrue(found)
        finally:
            if old_state_root:
                os.environ["AESOP_STATE_ROOT"] = old_state_root
            else:
                os.environ.pop("AESOP_STATE_ROOT", None)


if __name__ == "__main__":
    unittest.main()
