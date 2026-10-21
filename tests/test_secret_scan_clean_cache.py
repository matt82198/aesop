#!/usr/bin/env python3
# secretscan: allow-pattern-docs
"""
Test suite for the per-file CLEAN-verdict cache in tools/secret_scan.py (path mode).

The receipt matrix runs `secret_scan.py <repo>` (a full-tree scan) on every push, and
the scan dominated the receipt part's wall time. A file whose bytes and scanner source
are unchanged since a scan that found nothing is not re-scanned. Only CLEAN verdicts are
cached: a file with any finding is always re-scanned and always reported, and the cache
never stores matched text.

These tests pin the rules the speed-up must not change:

1. A known-bad input is still denied (exit 1), cold and warm.
2. Editing a cached clean file invalidates its cached verdict.
3. The cache key includes the scanner source, so a different scanner never reuses entries.
4. A corrupt cache is ignored (full scan), never trusted.
5. --no-cache reads and writes nothing.
6. The cache file holds no secret text.

Fixtures assemble their fake secrets at runtime (never a contiguous literal).
Run: python -m unittest tests.test_secret_scan_clean_cache
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCANNER = REPO_ROOT / "tools" / "secret_scan.py"
sys.path.insert(0, str(REPO_ROOT / "tools"))

# Runtime-assembled fake AWS access key (matches the aws_access_key rule).
FAKE_AWS_KEY = "AKIA" + "Q7X3M9ZP2RT5VW8J"


def _load_scanner_module():
    import importlib.util

    spec = importlib.util.spec_from_file_location("secret_scan_under_test", str(SCANNER))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class SecretScanCleanCacheTests(unittest.TestCase):
    """End-to-end verdicts of the path-mode scanner with the clean-verdict cache."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="test_scan_cache_"))
        self.repo = self.tmp / "repo"
        self.repo.mkdir()
        self.state = self.tmp / "state"
        self.state.mkdir()
        self.cache_file = self.state / "cache" / "secret_scan" / "clean.json"
        self.env = dict(os.environ)
        self.env["AESOP_STATE_ROOT"] = str(self.state)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write(self, name, content):
        path = self.repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def _scan(self, *extra_args):
        result = subprocess.run(
            [sys.executable, str(SCANNER), *extra_args, str(self.repo)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=self.env,
            cwd=str(self.tmp),
            timeout=120,
        )
        return result

    def test_clean_run_writes_cache_entry_for_clean_file(self):
        self._write("clean.py", "value = 1\n")
        result = self._scan()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(self.cache_file.exists(), "clean verdicts should be cached after a clean scan")
        data = json.loads(self.cache_file.read_text(encoding="utf-8"))
        self.assertTrue(any(key.endswith("clean.py") for key in data["entries"]), data)

    def test_known_bad_file_denied_cold_and_warm(self):
        self._write("clean.py", "value = 1\n")
        self._write("bad.txt", "aws = " + FAKE_AWS_KEY + "\n")
        cold = self._scan()
        self.assertEqual(cold.returncode, 1, cold.stdout + cold.stderr)
        self.assertIn("aws_access_key", cold.stdout + cold.stderr)
        warm = self._scan()
        self.assertEqual(warm.returncode, 1, "a warm cache must not stop a known-bad file being denied")
        self.assertIn("aws_access_key", warm.stdout + warm.stderr)

    def test_edit_of_cached_clean_file_to_contain_secret_is_denied(self):
        target = self._write("notes.txt", "nothing to see here\n")
        first = self._scan()
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        target.write_text("leaked = " + FAKE_AWS_KEY + "\n", encoding="utf-8")
        second = self._scan()
        self.assertEqual(second.returncode, 1, "editing a cached clean file must invalidate its verdict")
        self.assertIn("aws_access_key", second.stdout + second.stderr)

    def test_corrupt_cache_is_ignored_and_verdicts_hold(self):
        self._write("clean.py", "value = 1\n")
        self._write("bad.txt", "aws = " + FAKE_AWS_KEY + "\n")
        self.cache_file.parent.mkdir(parents=True, exist_ok=True)
        self.cache_file.write_text("{not json", encoding="utf-8")
        result = self._scan()
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("aws_access_key", result.stdout + result.stderr)

    def test_no_cache_flag_reads_and_writes_nothing(self):
        self._write("clean.py", "value = 1\n")
        result = self._scan("--no-cache")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse(self.cache_file.exists(), "--no-cache must not write the cache")

    def test_cache_file_holds_no_secret_text(self):
        self._write("clean.py", "value = 1\n")
        self._write("bad.txt", "aws = " + FAKE_AWS_KEY + "\n")
        self._scan()
        self._scan()
        if self.cache_file.exists():
            self.assertNotIn(FAKE_AWS_KEY, self.cache_file.read_text(encoding="utf-8"))


class SecretScanCacheKeyTests(unittest.TestCase):
    """Unit-level invalidation rules: what the cache key covers."""

    @classmethod
    def setUpClass(cls):
        cls.scanner = _load_scanner_module()

    def test_tag_changes_when_scanner_source_changes(self):
        tag_a = self.scanner._clean_cache_tag(b"PATTERNS = {}\n")
        tag_b = self.scanner._clean_cache_tag(b"PATTERNS = {'x': 1}\n")
        self.assertNotEqual(tag_a, tag_b)

    def test_tag_is_stable_for_identical_scanner_source(self):
        self.assertEqual(
            self.scanner._clean_cache_tag(b"same source\n"),
            self.scanner._clean_cache_tag(b"same source\n"),
        )

    def test_lookup_misses_when_content_hash_differs(self):
        entries = {"/repo/a.py": {"sha": "aaaa", "notices": []}}
        self.assertIsNotNone(self.scanner._clean_cache_lookup(entries, "/repo/a.py", "aaaa"))
        self.assertIsNone(self.scanner._clean_cache_lookup(entries, "/repo/a.py", "bbbb"))
        self.assertIsNone(self.scanner._clean_cache_lookup(entries, "/repo/other.py", "aaaa"))


if __name__ == "__main__":
    unittest.main()
