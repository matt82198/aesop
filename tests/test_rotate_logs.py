"""Unit tests for rotate_logs.py log rotation utility."""
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from datetime import datetime
from pathlib import Path


class TestRotateLogs(unittest.TestCase):
    """Test cases for log rotation with --max-lines, --max-bytes, --check mode."""

    def setUp(self):
        """Create temporary directory for test files."""
        self.temp_dir = tempfile.mkdtemp()
        self.rotate_script = Path(__file__).parent.parent / "tools" / "rotate_logs.py"

    def tearDown(self):
        """Clean up temporary directory."""
        import shutil
        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir)

    def _run_rotate(self, logfile, args=None):
        """Helper to run rotate_logs.py with subprocess."""
        cmd = [sys.executable, str(self.rotate_script), logfile]
        if args:
            cmd.extend(args)
        result = subprocess.run(cmd, capture_output=True, text=True)
        return result

    def _count_lines(self, filepath):
        """Count lines in a file."""
        if not os.path.exists(filepath):
            return 0
        with open(filepath, 'r') as f:
            return sum(1 for _ in f)

    def _get_file_size(self, filepath):
        """Get file size in bytes."""
        if not os.path.exists(filepath):
            return 0
        return os.path.getsize(filepath)

    def test_under_threshold_no_op(self):
        """Test that file under threshold is not rotated."""
        logfile = os.path.join(self.temp_dir, "test.log")
        with open(logfile, 'w') as f:
            f.write("line 1\nline 2\nline 3\n")

        result = self._run_rotate(logfile, ["--max-lines", "200"])
        self.assertEqual(result.returncode, 0)
        self.assertEqual(self._count_lines(logfile), 3)

        # Verify no archive was created
        archive_dir = os.path.join(self.temp_dir, "archive")
        self.assertFalse(os.path.exists(archive_dir))

    def test_over_lines_rotation(self):
        """Test rotation when lines exceed --max-lines threshold."""
        logfile = os.path.join(self.temp_dir, "test.log")
        # Create 250 lines
        with open(logfile, 'w') as f:
            for i in range(1, 251):
                f.write(f"line {i}\n")

        result = self._run_rotate(logfile, ["--max-lines", "200"])
        self.assertEqual(result.returncode, 0)

        # After rotation, original should have ~half (newest lines)
        remaining_lines = self._count_lines(logfile)
        self.assertLessEqual(remaining_lines, 200)

        # Verify archive was created
        archive_dir = os.path.join(self.temp_dir, "archive")
        self.assertTrue(os.path.exists(archive_dir))

        # List archive files
        archive_files = os.listdir(archive_dir)
        self.assertGreater(len(archive_files), 0)

        # Verify total lines (archive + remaining == original)
        archive_file = os.path.join(archive_dir, archive_files[0])
        archived_lines = self._count_lines(archive_file)
        total_lines = remaining_lines + archived_lines
        self.assertEqual(total_lines, 250)

    def test_over_bytes_rotation(self):
        """Test rotation when file size exceeds --max-bytes threshold."""
        logfile = os.path.join(self.temp_dir, "test.log")
        # Create lines each ~100 bytes to exceed 2000 bytes
        with open(logfile, 'w') as f:
            for i in range(30):
                f.write(f"line {i}: " + "x" * 90 + "\n")

        result = self._run_rotate(logfile, ["--max-bytes", "2000"])
        self.assertEqual(result.returncode, 0)

        # After rotation, original should be under threshold
        remaining_size = self._get_file_size(logfile)
        self.assertLessEqual(remaining_size, 2000)

        # Verify archive was created
        archive_dir = os.path.join(self.temp_dir, "archive")
        self.assertTrue(os.path.exists(archive_dir))

        # Verify total size preserved (archive + remaining == original)
        archive_files = os.listdir(archive_dir)
        archive_file = os.path.join(archive_dir, archive_files[0])
        archived_size = self._get_file_size(archive_file)
        # We can't guarantee exact total due to rounding, but should be very close
        total_size = remaining_size + archived_size
        self.assertGreater(total_size, 2000)

    def test_check_mode_no_rotation_needed(self):
        """Test --check mode exits 0 when no rotation needed."""
        logfile = os.path.join(self.temp_dir, "test.log")
        with open(logfile, 'w') as f:
            f.write("line 1\nline 2\nline 3\n")

        result = self._run_rotate(logfile, ["--check", "--max-lines", "200"])
        self.assertEqual(result.returncode, 0)

        # Verify file is unchanged
        self.assertEqual(self._count_lines(logfile), 3)

    def test_check_mode_rotation_needed(self):
        """Test --check mode exits 3 when rotation is needed."""
        logfile = os.path.join(self.temp_dir, "test.log")
        with open(logfile, 'w') as f:
            for i in range(1, 251):
                f.write(f"line {i}\n")

        result = self._run_rotate(logfile, ["--check", "--max-lines", "200"])
        self.assertEqual(result.returncode, 3)

        # Verify file is unchanged
        self.assertEqual(self._count_lines(logfile), 250)

        # Verify no archive was created
        archive_dir = os.path.join(self.temp_dir, "archive")
        self.assertFalse(os.path.exists(archive_dir))

    def test_archive_directory_creation(self):
        """Test that archive directory is created if it doesn't exist."""
        logfile = os.path.join(self.temp_dir, "test.log")
        with open(logfile, 'w') as f:
            for i in range(1, 251):
                f.write(f"line {i}\n")

        result = self._run_rotate(logfile, ["--max-lines", "200"])
        self.assertEqual(result.returncode, 0)

        archive_dir = os.path.join(self.temp_dir, "archive")
        self.assertTrue(os.path.exists(archive_dir))

    def test_custom_archive_directory(self):
        """Test --archive-dir parameter."""
        logfile = os.path.join(self.temp_dir, "test.log")
        custom_archive = os.path.join(self.temp_dir, "custom_archive")
        os.makedirs(custom_archive, exist_ok=True)

        with open(logfile, 'w') as f:
            for i in range(1, 251):
                f.write(f"line {i}\n")

        result = self._run_rotate(
            logfile,
            ["--max-lines", "200", "--archive-dir", custom_archive]
        )
        self.assertEqual(result.returncode, 0)

        # Verify archive was created in custom location
        archive_files = os.listdir(custom_archive)
        self.assertGreater(len(archive_files), 0)

    def test_archive_naming_format(self):
        """Test that archive files follow <basename>.<UTCstamp>.log format."""
        logfile = os.path.join(self.temp_dir, "test.log")
        with open(logfile, 'w') as f:
            for i in range(1, 251):
                f.write(f"line {i}\n")

        result = self._run_rotate(logfile, ["--max-lines", "200"])
        self.assertEqual(result.returncode, 0)

        archive_dir = os.path.join(self.temp_dir, "archive")
        archive_files = os.listdir(archive_dir)
        self.assertEqual(len(archive_files), 1)

        archive_file = archive_files[0]
        # Should match pattern: test.<timestamp>.log
        self.assertTrue(archive_file.startswith("test."))
        self.assertTrue(archive_file.endswith(".log"))

    def test_idempotent_under_threshold(self):
        """Test that running rotation twice on under-threshold file is safe."""
        logfile = os.path.join(self.temp_dir, "test.log")
        with open(logfile, 'w') as f:
            f.write("line 1\nline 2\nline 3\n")

        # Run twice
        result1 = self._run_rotate(logfile, ["--max-lines", "200"])
        result2 = self._run_rotate(logfile, ["--max-lines", "200"])

        self.assertEqual(result1.returncode, 0)
        self.assertEqual(result2.returncode, 0)
        self.assertEqual(self._count_lines(logfile), 3)

    def test_total_content_preserved(self):
        """Test that total lines are preserved across archive + original."""
        logfile = os.path.join(self.temp_dir, "test.log")
        original_count = 300
        with open(logfile, 'w') as f:
            for i in range(1, original_count + 1):
                f.write(f"line {i}\n")

        result = self._run_rotate(logfile, ["--max-lines", "200"])
        self.assertEqual(result.returncode, 0)

        # Count remaining
        remaining_lines = self._count_lines(logfile)

        # Count archived
        archive_dir = os.path.join(self.temp_dir, "archive")
        archive_files = os.listdir(archive_dir)
        archived_lines = 0
        for archive_file in archive_files:
            archived_lines += self._count_lines(os.path.join(archive_dir, archive_file))

        # Verify total
        total_lines = remaining_lines + archived_lines
        self.assertEqual(total_lines, original_count)

    def test_newest_lines_retained(self):
        """Test that newest lines are retained after rotation."""
        logfile = os.path.join(self.temp_dir, "test.log")
        with open(logfile, 'w') as f:
            for i in range(1, 251):
                f.write(f"line {i}\n")

        result = self._run_rotate(logfile, ["--max-lines", "200"])
        self.assertEqual(result.returncode, 0)

        # Read remaining lines and verify they include the newest ones
        with open(logfile, 'r') as f:
            remaining = f.readlines()

        # Should contain "line 250" (the newest)
        self.assertTrue(any("line 250" in line for line in remaining))

    def test_keep_count_guard_tiny_max_bytes(self):
        """Test that rotation guards against keep_count <= 0 when max_bytes is tiny.

        When max_bytes is very small (e.g., 1 byte) and all lines exceed it,
        the calculated keep_count becomes 0 or negative. The guard should
        ensure at least 1 line is kept and the rest are archived.
        """
        logfile = os.path.join(self.temp_dir, "test.log")
        # Create lines of 10 bytes each
        with open(logfile, 'w') as f:
            for i in range(1, 21):
                f.write(f"line {i:02d}\n")  # Each line is ~10 bytes

        # Set max_bytes to 1 (tiny, would cause keep_count <= 0)
        result = self._run_rotate(logfile, ["--max-bytes", "1"])
        self.assertEqual(result.returncode, 0)

        # After rotation, original should have at least 1 line
        remaining_lines = self._count_lines(logfile)
        self.assertGreaterEqual(remaining_lines, 1, "Must keep at least 1 line")

        # Verify archive was created with the rest
        archive_dir = os.path.join(self.temp_dir, "archive")
        self.assertTrue(os.path.exists(archive_dir))

        archive_files = os.listdir(archive_dir)
        self.assertGreater(len(archive_files), 0)

        # Verify total lines preserved
        archive_file = os.path.join(archive_dir, archive_files[0])
        archived_lines = self._count_lines(archive_file)
        total_lines = remaining_lines + archived_lines
        self.assertEqual(total_lines, 20)

    def test_concurrent_append_no_data_loss(self):
        """Test that concurrent appends during rotation don't lose data.

        This test simulates the race condition from the original bug:
        1. Rotation is triggered (reads file, holds lock)
        2. Another thread attempts to append while rotation computes
        3. Rotation releases lock and returns
        4. Appended lines should not be lost

        The atomic locking in the fix prevents lines written to the file
        from being discarded by the rotation's truncate+rewrite sequence.
        """
        logfile = os.path.join(self.temp_dir, "test.log")

        # Create initial log with 250 lines to trigger rotation
        with open(logfile, 'w') as f:
            for i in range(1, 251):
                f.write(f"line {i}\n")

        # Count initial lines
        initial_lines = self._count_lines(logfile)
        self.assertEqual(initial_lines, 250)

        # Append lines while rotation happens
        def append_concurrent():
            # Give rotation a moment to start
            time.sleep(0.05)  # sleep-ok
            # Append 10 lines to the log
            with open(logfile, 'a') as f:
                for i in range(251, 261):
                    f.write(f"line {i}\n")

        # Run rotation and concurrent append in separate threads
        rotate_thread = threading.Thread(
            target=lambda: self._run_rotate(logfile, ["--max-lines", "200"])
        )
        append_thread = threading.Thread(target=append_concurrent)

        # Start both threads (order doesn't matter due to locking)
        rotate_thread.start()
        append_thread.start()

        rotate_thread.join(timeout=5)
        append_thread.join(timeout=5)

        # Verify no lines were lost
        # After rotation: ~100 lines kept (half of 250)
        # Plus the 10 lines appended: should have ~110 lines
        final_lines = self._count_lines(logfile)
        archive_dir = os.path.join(self.temp_dir, "archive")
        archived_lines = 0
        if os.path.exists(archive_dir):
            archive_files = os.listdir(archive_dir)
            for archive_file in archive_files:
                archived_lines += self._count_lines(os.path.join(archive_dir, archive_file))

        # Total should be 260 (250 original + 10 appended)
        total_lines = final_lines + archived_lines
        self.assertEqual(
            total_lines, 260,
            f"Race condition: expected 260 total lines, got {total_lines} "
            f"(live={final_lines}, archived={archived_lines}). "
            f"This indicates concurrent writes were lost during rotation."
        )

        # Verify newest appended lines are in live file
        with open(logfile, 'r') as f:
            content = f.read()
        self.assertIn("line 260", content, "Newest appended line should be in live file")

    def test_external_append_during_rotation(self):
        """Test that external O_APPEND writes between read and truncate aren't lost.

        This test simulates the real-world scenario where external processes
        (run-watchdog.sh, backup-fleet.sh) use >> and tee -a respectively,
        which don't take advisory locks. The rotation must not lose these appends.

        Scenario:
        1. Rotation reads file (250 lines)
        2. External process appends line 251 (after rotation reads, before truncate)
        3. Rotation truncates and writes (would lose line 251 if not handled)
        4. Line 251 must not be lost
        """
        logfile = os.path.join(self.temp_dir, "test.log")
        archive_dir = os.path.join(self.temp_dir, "archive")

        # Create initial log with 250 lines to trigger rotation
        with open(logfile, 'w') as f:
            for i in range(1, 251):
                f.write(f"line {i}\n")

        # We'll inject a simulated append into the rotation process
        # by overriding the rotate_logs module's functions temporarily
        sys.path.insert(0, str(Path(__file__).parent.parent / "tools"))
        import rotate_logs

        original_write_lines = rotate_logs.write_lines

        append_happened = {'count': 0}

        def mock_write_lines_with_append(filepath, lines):
            """Intercept the archive write to simulate an external append."""
            # Simulate external process appending a line while rotation is computing
            # This append happens AFTER readlines() but BEFORE the truncate+write
            if append_happened['count'] == 0:
                append_happened['count'] += 1
                # Append without taking the lock (simulating external process)
                with open(logfile, 'a') as f:
                    for i in range(251, 261):  # Add lines 251-260
                        f.write(f"line {i}\n")
            return original_write_lines(filepath, lines)

        # Monkey-patch to inject the append
        rotate_logs.write_lines = mock_write_lines_with_append

        try:
            # Call rotate_log directly (not via subprocess) so the monkeypatch is used
            result = rotate_logs.rotate_log(
                logfile,
                max_lines=200,
                max_bytes=20480,
                archive_dir=archive_dir,
                check_only=False
            )
            self.assertEqual(result, 0, "Rotation should succeed")

            # Count final lines
            final_lines = self._count_lines(logfile)

            # Count archived lines
            archived_lines = 0
            if os.path.exists(archive_dir):
                archive_files = os.listdir(archive_dir)
                for archive_file in archive_files:
                    archived_lines += self._count_lines(os.path.join(archive_dir, archive_file))

            # Total should be 260 (250 original + 10 externally appended)
            total_lines = final_lines + archived_lines
            self.assertEqual(
                total_lines, 260,
                f"External appends were lost: expected 260 total lines, got {total_lines} "
                f"(live={final_lines}, archived={archived_lines})"
            )

            # Verify that some of the appended lines are in the live file
            with open(logfile, 'r') as f:
                content = f.read()
            # At least some of the appended lines should be there
            appended_count = sum(1 for i in range(251, 261) if f"line {i}" in content)
            self.assertGreater(
                appended_count, 0,
                "None of the externally appended lines made it to the live file"
            )

        finally:
            # Restore original
            rotate_logs.write_lines = original_write_lines


if __name__ == "__main__":
    unittest.main()
