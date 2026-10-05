#!/usr/bin/env python3
"""
Test suite for verify_test_suite_count.py (backward-compatible wrapper).

Structural fix #776 moved suite counts out of hand-maintained tests/CLAUDE.md
lines into generated tests/SUITE-COUNTS.json, produced by tools/gen_suite_counts.py.
verify_test_suite_count.py no longer scans or rewrites tests/CLAUDE.md itself; it
is a thin wrapper that translates its CLI onto tools/gen_suite_counts.py so that
existing pre-push-hook and CI references keep working unchanged.

Contract under test here (the wrapper's own logic, not gen_suite_counts.py's --
that tool's git-derivation, dedup, and merge-in-progress-warning coverage lives
in tests/test_gen_suite_counts.py):

- Default / --check / --strict all translate to `gen_suite_counts.py --check`.
- --regenerate / --fix translate to `gen_suite_counts.py --regenerate`; --dry-run
  is forwarded and also implies --regenerate (so a bare `--dry-run` still works).
- --check and --regenerate (or --fix) together are a usage error (exit 1), never
  silently resolved one way.
- --repo is forwarded verbatim.
- The exit code gen_suite_counts.py returns is passed straight through.
- gen_suite_counts.py is located relative to THIS file (verify_test_suite_count.py's
  own directory), never a cwd-relative string -- a pre-push hook or test runs this
  wrapper with the process cwd set to the repo being graded (via --repo or a plain
  cwd change), not to this aesop checkout, so a cwd-relative lookup would silently
  resolve to nothing in exactly the cases this wrapper exists to serve.
"""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class TestVerifyTestSuiteCountWrapper(unittest.TestCase):
    """Test the verify_test_suite_count.py -> gen_suite_counts.py delegation."""

    @classmethod
    def setUpClass(cls):
        cls.repo_root = Path(__file__).parent.parent
        cls.wrapper = cls.repo_root / "tools" / "verify_test_suite_count.py"

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.temp_root = Path(self.temp_dir.name)

        tests_dir = self.temp_root / "tests"
        tests_dir.mkdir(parents=True, exist_ok=True)
        (tests_dir / "test_a.py").touch()
        (tests_dir / "test_a.test.mjs").touch()
        (tests_dir / "test_a.sh").touch()

        subprocess.run(
            ["git", "init", "-q"], cwd=str(self.temp_root), check=True, capture_output=True
        )
        subprocess.run(
            ["git", "-C", str(self.temp_root), "config", "user.email", "test@example.com"],
            check=True, capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(self.temp_root), "config", "user.name", "Test User"],
            check=True, capture_output=True,
        )
        subprocess.run(
            ["git", "add", "-A"], cwd=str(self.temp_root), check=True, capture_output=True
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def _run(self, *args, cwd=None):
        cmd = [sys.executable, str(self.wrapper)]
        cmd.extend(args)
        return subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            cwd=str(cwd) if cwd else str(self.temp_root),
            timeout=30,
        )

    def test_default_mode_is_check(self):
        """No mode flag defaults to --check (read-only, fails when artifact missing)."""
        result = self._run()
        self.assertEqual(result.returncode, 1, result.stderr)
        json_path = self.temp_root / "tests" / "SUITE-COUNTS.json"
        self.assertFalse(json_path.exists(), "default mode must not write")

    def test_check_and_regenerate_are_mutually_exclusive(self):
        """Combining --check and --regenerate is a usage error, not silently resolved."""
        result = self._run("--check", "--regenerate")
        self.assertEqual(result.returncode, 1)
        self.assertIn("mutually exclusive", result.stderr)

    def test_check_and_fix_are_mutually_exclusive(self):
        """--fix is the deprecated alias for --regenerate; same mutual exclusion applies."""
        result = self._run("--check", "--fix")
        self.assertEqual(result.returncode, 1)
        self.assertIn("mutually exclusive", result.stderr)

    def test_regenerate_creates_generated_artifact(self):
        """--regenerate delegates to gen_suite_counts.py --regenerate."""
        result = self._run("--regenerate")
        self.assertEqual(result.returncode, 0, result.stderr)

        json_path = self.temp_root / "tests" / "SUITE-COUNTS.json"
        self.assertTrue(json_path.exists())
        content = json_path.read_text()
        start, end = content.find("{"), content.rfind("}") + 1
        data = json.loads(content[start:end])
        self.assertEqual(data, {"Node": 1, "Shell": 1, "Python": 1})

    def test_fix_is_an_alias_for_regenerate(self):
        """--fix keeps working as an alias so existing callers (auto_merge.py) do not break."""
        result = self._run("--fix")
        self.assertEqual(result.returncode, 0, result.stderr)
        json_path = self.temp_root / "tests" / "SUITE-COUNTS.json"
        self.assertTrue(json_path.exists())

    def test_dry_run_implies_regenerate_without_writing(self):
        """A bare --dry-run (no explicit --regenerate) must still not write."""
        result = self._run("--dry-run")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("DRY-RUN", result.stdout)
        json_path = self.temp_root / "tests" / "SUITE-COUNTS.json"
        self.assertFalse(json_path.exists(), "--dry-run must never write")

    def test_check_passes_after_regenerate(self):
        """Round-trip: --regenerate then --check must agree."""
        first = self._run("--regenerate")
        self.assertEqual(first.returncode, 0, first.stderr)

        second = self._run("--check")
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertIn("counts match", second.stdout)

    def test_repo_flag_is_forwarded(self):
        """--repo is forwarded to gen_suite_counts.py, not silently dropped.

        Invoke the wrapper from an UNRELATED cwd (self.repo_root, this aesop
        checkout) and point --repo at the fixture repo -- this is also the
        regression case for the cwd-relative-lookup bug: gen_suite_counts.py must
        be found via the wrapper's own file location, not the invocation cwd.
        """
        result = self._run("--regenerate", "--repo", str(self.temp_root), cwd=self.repo_root)
        self.assertEqual(result.returncode, 0, result.stderr)
        json_path = self.temp_root / "tests" / "SUITE-COUNTS.json"
        self.assertTrue(
            json_path.exists(),
            "--repo must be forwarded so gen_suite_counts.py grades the fixture tree, "
            "not the wrapper's own invocation cwd",
        )

    def test_strict_is_alias_for_check(self):
        """--strict behaves exactly like --check (reserved alias)."""
        self._run("--regenerate")
        result = self._run("--strict")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_claudemd_flag_is_accepted_but_unused(self):
        """--claudemd is kept for CLI compatibility but no longer does anything."""
        result = self._run("--regenerate", "--claudemd", "ignored-path.md")
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
