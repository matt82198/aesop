#!/usr/bin/env python3
"""
Tests for CLI hygiene across 7 hand-parsed argv tools.

Unix least-surprise contract these tools must honor (like the 53 argparse-based
tools already do):
  (a) `<tool> --help` exits 0, prints usage to STDOUT, and has NO side effect
      (esp. tools/alert_bridge.py must never trigger a live scan/webhook send).
  (b) An unrecognized flag exits non-zero with a diagnostic on STDERR
      (esp. tools/wave_backlog_analyzer.py previously `return 0` on an unknown
      flag -- a real exit-code bug -- and tools/git_identity_check.py silently
      swallowed unknown flags via `else: i += 1`).

HERMETIC: every subprocess invocation runs with a throwaway temp cwd and (where
relevant) AESOP_STATE_ROOT pointed at a temp dir, so nothing here ever touches
the real project state, the real SECURITY-ALERTS log, or the live repo's git
config. git_identity_check tests set identity only on a scratch --local git
repo, never global config.
"""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
TOOLS_DIR = REPO_ROOT / "tools"


def run_tool(tool_name, args, cwd=None, extra_env=None):
    """Run tools/<tool_name> as a subprocess; hermetic env by default."""
    script = TOOLS_DIR / tool_name
    env = os.environ.copy()
    # Never let a real project config/state leak in via ambient env vars.
    env.pop("AESOP_STATE_ROOT", None)
    env.pop("AESOP_ROOT", None)
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [sys.executable, str(script), *args],
        capture_output=True,
        text=True,
        cwd=str(cwd) if cwd else None,
        env=env,
    )


class HermeticTempDirCase(unittest.TestCase):
    """Base: isolated temp dir per test, cleaned up in tearDown."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="aesop-cli-hygiene-"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)


# ---------------------------------------------------------------------------
# 1. tools/halt.py
# ---------------------------------------------------------------------------
class TestHaltCLIHygiene(HermeticTempDirCase):
    def test_help_exits_zero_with_usage_on_stdout(self):
        state_dir = self.tmp / "state"
        result = run_tool("halt.py", ["--help"], cwd=self.tmp,
                           extra_env={"AESOP_STATE_ROOT": str(state_dir)})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("usage", result.stdout.lower())
        self.assertFalse((state_dir / ".HALT").exists(), "--help must not write a sentinel")

    def test_unknown_flag_exits_nonzero_with_stderr(self):
        result = run_tool("halt.py", ["--bogus"], cwd=self.tmp,
                           extra_env={"AESOP_STATE_ROOT": str(self.tmp / "state")})
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(result.stderr.strip())


# ---------------------------------------------------------------------------
# 2. tools/stateapi_lint.py
# ---------------------------------------------------------------------------
class TestStateapiLintCLIHygiene(HermeticTempDirCase):
    def test_help_exits_zero_with_usage_on_stdout(self):
        result = run_tool("stateapi_lint.py", ["--help"], cwd=self.tmp)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("usage", result.stdout.lower())

    def test_unknown_flag_exits_nonzero_with_stderr(self):
        result = run_tool("stateapi_lint.py", ["--bogus"], cwd=self.tmp)
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(result.stderr.strip())


# ---------------------------------------------------------------------------
# 3. tools/ci_shard_runner.py
# ---------------------------------------------------------------------------
class TestCiShardRunnerCLIHygiene(HermeticTempDirCase):
    def test_help_exits_zero_with_usage_on_stdout(self):
        result = run_tool("ci_shard_runner.py", ["--help"], cwd=self.tmp)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("usage", result.stdout.lower())
        # No int('--help') crash / traceback.
        self.assertNotIn("Traceback", result.stderr)

    def test_unknown_flag_exits_nonzero_with_stderr(self):
        # A lone unrecognized flag used to silently fall through to the
        # env-var default path (spawning a real `git ls-files` subprocess and
        # failing there for an unrelated reason). It must now be rejected
        # immediately, with a clear diagnostic, before any subprocess runs.
        result = run_tool("ci_shard_runner.py", ["--bogus"], cwd=self.tmp)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unknown argument", result.stderr.lower())
        self.assertNotIn("Traceback", result.stderr)


# ---------------------------------------------------------------------------
# 4. tools/session_usage_summary.py
# ---------------------------------------------------------------------------
class TestSessionUsageSummaryCLIHygiene(HermeticTempDirCase):
    def test_help_exits_zero_with_usage_on_stdout(self):
        result = run_tool("session_usage_summary.py", ["--help"], cwd=self.tmp)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("usage", result.stdout.lower())
        self.assertNotIn("Traceback", result.stderr)

    def test_unknown_flag_exits_nonzero_with_stderr(self):
        result = run_tool("session_usage_summary.py", ["--bogus"], cwd=self.tmp)
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(result.stderr.strip())


# ---------------------------------------------------------------------------
# 5. tools/git_identity_check.py
# ---------------------------------------------------------------------------
class TestGitIdentityCheckCLIHygiene(HermeticTempDirCase):
    def setUp(self):
        super().setUp()
        self.repo = self.tmp / "repo"
        self.repo.mkdir()
        subprocess.run(["git", "init"], cwd=str(self.repo), capture_output=True, check=True)
        subprocess.run(
            ["git", "config", "--local", "user.name", "Test User"],
            cwd=str(self.repo), capture_output=True, check=True,
        )
        subprocess.run(
            ["git", "config", "--local", "user.email", "test@example.com"],
            cwd=str(self.repo), capture_output=True, check=True,
        )

    def test_help_exits_zero_with_usage_on_stdout(self):
        result = run_tool("git_identity_check.py", ["--help"], cwd=self.tmp)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("usage", result.stdout.lower())

    def test_unknown_flag_exits_nonzero_with_stderr(self):
        # Previously: unrecognized flags were silently skipped (`else: i += 1`),
        # so a fully-valid, matching invocation plus a typo'd flag still
        # succeeded (exit 0) instead of being rejected.
        result = run_tool(
            "git_identity_check.py",
            [
                "--repo", str(self.repo),
                "--expect-name", "Test User",
                "--expect-email", "test@example.com",
                "--bogus",
            ],
            cwd=self.tmp,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(result.stderr.strip())
        self.assertIn("bogus", result.stderr.lower())

    def test_valid_matching_invocation_still_exits_zero(self):
        # Preserve existing valid behavior: no unknown flag -> success.
        result = run_tool(
            "git_identity_check.py",
            [
                "--repo", str(self.repo),
                "--expect-name", "Test User",
                "--expect-email", "test@example.com",
            ],
            cwd=self.tmp,
        )
        self.assertEqual(result.returncode, 0, result.stderr)


# ---------------------------------------------------------------------------
# 6. tools/wave_backlog_analyzer.py
# ---------------------------------------------------------------------------
class TestWaveBacklogAnalyzerCLIHygiene(HermeticTempDirCase):
    def test_help_exits_zero_with_usage_on_stdout(self):
        result = run_tool("wave_backlog_analyzer.py", ["--help"], cwd=self.tmp)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("usage", result.stdout.lower())

    def test_unknown_flag_exits_nonzero_not_zero(self):
        # Real bug: previously printed to stderr but did `return 0` anyway.
        result = run_tool("wave_backlog_analyzer.py", ["--bogus"], cwd=self.tmp)
        self.assertNotEqual(result.returncode, 0, "must NOT exit 0 on an unknown flag")
        self.assertTrue(result.stderr.strip())


# ---------------------------------------------------------------------------
# 7. tools/alert_bridge.py
# ---------------------------------------------------------------------------
class TestAlertBridgeCLIHygiene(HermeticTempDirCase):
    def test_help_exits_zero_no_scan_side_effect(self):
        # No aesop.config.json in cwd -> if --help fell through to mode_scan
        # (the pre-fix bug: unknown mode silently ran mode_scan), it would
        # print the mode_scan "no-op" diagnostic. --help must short-circuit
        # before that, with a clean usage message and nothing scan-related.
        result = run_tool("alert_bridge.py", ["--help"], cwd=self.tmp)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("usage", result.stdout.lower())
        self.assertNotIn("no-op", result.stdout.lower())
        self.assertNotIn("no-op", result.stderr.lower())
        self.assertNotIn("[alert_bridge]", result.stdout)
        self.assertNotIn("[alert_bridge]", result.stderr)

    def test_unknown_mode_exits_nonzero_no_scan_side_effect(self):
        # Previously: `mode = args[0]`; anything not --test-message/--dry-run
        # fell through to `else: return mode_scan(config)` -- a live scan.
        result = run_tool("alert_bridge.py", ["--totally-bogus-mode"], cwd=self.tmp)
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(result.stderr.strip())
        self.assertNotIn("no-op", result.stdout.lower())
        self.assertNotIn("[alert_bridge]", result.stdout)


if __name__ == "__main__":
    unittest.main()
