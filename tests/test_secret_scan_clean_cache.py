#!/usr/bin/env python3
# secretscan: allow-pattern-docs
"""
Verdict-stability tests for tools/secret_scan.py path mode now that the per-file
clean-verdict cache is REMOVED (audit 2026-10-22 #2: a hand-written clean.json under
AESOP_STATE_ROOT or the temp dir could bless a secret file). Every scan is a full scan.

These tests pin what the removed speed-up used to be able to get wrong:

1. A known-bad input is denied (exit 1) on every run, cold and repeated.
2. Editing a previously clean file to contain a secret is denied.
3. A corrupt or foreign cache file on disk is never read and changes no verdict.
4. A scan writes no cache file, with or without --no-cache (kept as an accepted no-op).

The poisoned-cache and temp-dir variants live in tests/test_secret_scan_fail_closed_audit.py.
Fixtures assemble their fake secrets at runtime (never a contiguous literal).
Run: python -m unittest tests.test_secret_scan_clean_cache
"""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCANNER = REPO_ROOT / "tools" / "secret_scan.py"

# Runtime-assembled fake AWS access key (matches the aws_access_key rule).
FAKE_AWS_KEY = "AKIA" + "Q7X3M9ZP2RT5VW8J"


class SecretScanNoCacheTests(unittest.TestCase):
    """End-to-end verdicts of the path-mode scanner: no cache, full scan every run."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="test_scan_nocache_"))
        self.repo = self.tmp / "repo"
        self.repo.mkdir()
        self.state = self.tmp / "state"
        self.state.mkdir()
        self.cache_dir = self.state / "cache"
        self.env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        self.env["AESOP_STATE_ROOT"] = str(self.state)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write(self, name, content):
        path = self.repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def _scan(self, *extra_args):
        return subprocess.run(
            [sys.executable, str(SCANNER), *extra_args, str(self.repo)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=self.env,
            cwd=str(self.tmp),
            timeout=120,
        )

    def test_clean_run_writes_no_cache(self):
        self._write("clean.py", "value = 1\n")
        result = self._scan()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse(self.cache_dir.exists(), "a clean scan must not write a verdict cache")

    def test_known_bad_file_denied_on_every_run(self):
        self._write("clean.py", "value = 1\n")
        self._write("bad.txt", "aws = " + FAKE_AWS_KEY + "\n")
        for attempt in range(2):
            result = self._scan()
            self.assertEqual(result.returncode, 1, f"run {attempt}: {result.stdout}{result.stderr}")
            self.assertIn("aws_access_key", result.stdout + result.stderr)

    def test_edit_of_clean_file_to_contain_secret_is_denied(self):
        target = self._write("notes.txt", "nothing to see here\n")
        first = self._scan()
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        target.write_text("leaked = " + FAKE_AWS_KEY + "\n", encoding="utf-8")
        second = self._scan()
        self.assertEqual(second.returncode, 1, "editing a clean file must change the verdict")
        self.assertIn("aws_access_key", second.stdout + second.stderr)

    def test_corrupt_cache_file_on_disk_changes_no_verdict(self):
        self._write("clean.py", "value = 1\n")
        self._write("bad.txt", "aws = " + FAKE_AWS_KEY + "\n")
        cache_file = self.cache_dir / "secret_scan" / "clean.json"
        cache_file.parent.mkdir(parents=True)
        cache_file.write_text("{not json", encoding="utf-8")
        result = self._scan()
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("aws_access_key", result.stdout + result.stderr)

    def test_no_cache_flag_is_an_accepted_noop(self):
        self._write("clean.py", "value = 1\n")
        result = self._scan("--no-cache")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse(self.cache_dir.exists())


if __name__ == "__main__":
    unittest.main()
