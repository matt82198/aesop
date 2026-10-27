#!/usr/bin/env python3
"""
Tests for tools/metrics_gate.py — NO-UNVERIFIED-METRICS gate.

Tests that hard numeric claims in *.md files require source verification unless
they are version numbers, dates, line numbers, or other acceptable values.

Isolation note: the gate scans the git repo at the current working directory,
so each test builds a throwaway temp repo and points the gate at it via
``cwd=repo``. We deliberately do NOT ``os.chdir`` into the temp dir — a leaked
chdir into a since-deleted temp dir poisons every later test in the same
process (this broke wave-25 CI shard 2) and, on Windows, blocks temp-dir
cleanup entirely.
"""

import subprocess
import sys
import tempfile
from pathlib import Path
import unittest


REPO_ROOT = Path(__file__).resolve().parent.parent
METRICS_GATE = REPO_ROOT / "tools" / "metrics_gate.py"


class TestMetricsGate(unittest.TestCase):
    """Test suite for metrics_gate.py"""

    def run_metrics_gate(self, repo, *args):
        """Run metrics_gate.py against ``repo`` and return (exit, out, err)."""
        cmd = [sys.executable, str(METRICS_GATE)] + list(args)
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            cwd=str(repo),
        )
        return result.returncode, result.stdout, result.stderr

    @staticmethod
    def _init_repo(repo):
        """git init a repo with a local (non-global) identity."""
        subprocess.run(["git", "init"], cwd=str(repo), capture_output=True)
        subprocess.run(
            ["git", "config", "user.email", "test@example.com"],
            cwd=str(repo), capture_output=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "Test"],
            cwd=str(repo), capture_output=True,
        )

    @staticmethod
    def _commit(repo, text, message):
        """Write test.md and commit it."""
        repo.joinpath("test.md").write_text(text)
        subprocess.run(["git", "add", "test.md"], cwd=str(repo), capture_output=True)
        subprocess.run(["git", "commit", "-m", message], cwd=str(repo), capture_output=True)

    def test_no_metrics_pass(self):
        """Test: a resolvable range with no .md changes (empty md diff) passes.

        This is the legitimate empty-diff case. The range resolves, git exits 0,
        and only a non-markdown file changed, so there is nothing to scan.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = Path(tmpdir)
            self._init_repo(repo)
            self._commit(repo, "# Test\nNo numbers here.\n", "initial")
            repo.joinpath("notes.txt").write_text("plain text only\n")
            subprocess.run(["git", "add", "notes.txt"], cwd=str(repo), capture_output=True)
            subprocess.run(["git", "commit", "-m", "add-txt"], cwd=str(repo), capture_output=True)

            exit_code, stdout, stderr = self.run_metrics_gate(repo, "HEAD~1...HEAD")
            self.assertEqual(exit_code, 0)
            self.assertIn("NO-UNVERIFIED-METRICS: OK", stdout)

    def test_percentage_without_verification_fails(self):
        """Test: percentage claims without verification marker fail."""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = Path(tmpdir)
            self._init_repo(repo)
            self._commit(repo, "# Test\n", "initial")
            self._commit(repo, "# Test\n\nPerformance improved by 42%.\n", "add-metric")

            exit_code, stdout, stderr = self.run_metrics_gate(repo, "HEAD~1...HEAD")
            self.assertEqual(exit_code, 1)
            self.assertTrue("42%" in stdout or "42%" in stderr)

    def test_percentage_with_verification_passes(self):
        """Test: percentage claims with verification marker pass."""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = Path(tmpdir)
            self._init_repo(repo)
            self._commit(repo, "# Test\n", "initial")
            self._commit(
                repo,
                "# Test\n\nPerformance improved by 42%.\n"
                "<!-- metrics-verified: benchmarksuitev2 -->\n",
                "add-metric",
            )

            exit_code, stdout, stderr = self.run_metrics_gate(repo, "HEAD~1...HEAD")
            # A verified claim on an adjacent line should pass the gate.
            self.assertEqual(exit_code, 0)

    def test_multiplier_without_verification_fails(self):
        """Test: multiplier claims (Nx or ×) without verification fail."""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = Path(tmpdir)
            self._init_repo(repo)
            self._commit(repo, "# Test\n", "initial")
            self._commit(repo, "# Test\n\nThis is 3x faster.\n", "add-metric")

            exit_code, stdout, stderr = self.run_metrics_gate(repo, "HEAD~1...HEAD")
            self.assertEqual(exit_code, 1)

    def test_dollar_amount_without_verification_fails(self):
        """Test: dollar amounts without verification fail."""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = Path(tmpdir)
            self._init_repo(repo)
            self._commit(repo, "# Test\n", "initial")
            self._commit(repo, "# Test\n\nCost is $15000 per year.\n", "add-metric")

            exit_code, stdout, stderr = self.run_metrics_gate(repo, "HEAD~1...HEAD")
            self.assertEqual(exit_code, 1)

    def test_version_numbers_excluded(self):
        """Test: version numbers (e.g., v1.2.3, 2.0) don't require verification."""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = Path(tmpdir)
            self._init_repo(repo)
            self._commit(repo, "# Test\n", "initial")
            self._commit(repo, "# Test\n\nVersion 2.0 released.\n", "add-version")

            exit_code, stdout, stderr = self.run_metrics_gate(repo, "HEAD~1...HEAD")
            # Version numbers are not hard claims; should pass.
            self.assertEqual(exit_code, 0)

    def test_date_numbers_excluded(self):
        """Test: dates (2024, 2025) don't require verification."""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = Path(tmpdir)
            self._init_repo(repo)
            self._commit(repo, "# Test\n", "initial")
            self._commit(repo, "# Test\n\nCreated in 2024.\n", "add-date")

            exit_code, stdout, stderr = self.run_metrics_gate(repo, "HEAD~1...HEAD")
            # Dates are not hard claims; should pass.
            self.assertEqual(exit_code, 0)

    def test_line_numbers_excluded(self):
        """Test: line numbers don't require verification."""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = Path(tmpdir)
            self._init_repo(repo)
            self._commit(repo, "# Test\n", "initial")
            self._commit(repo, "# Test\n\nSee line 42 for details.\n", "add-line")

            exit_code, stdout, stderr = self.run_metrics_gate(repo, "HEAD~1...HEAD")
            # Bare "line 42" has no %/x/$ claim; should pass.
            self.assertEqual(exit_code, 0)

    def test_git_diff_error_fails_closed(self):
        """Test: an unresolvable range BLOCKs (exit 1) and names the range.

        Regression for the fail-open defect: git exits 128 on an unknown ref, and
        the gate used to treat that as "no diff" and PASS. A gate that cannot look
        must not pass.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = Path(tmpdir)
            self._init_repo(repo)
            self._commit(repo, "# Test\n", "initial")

            exit_code, stdout, stderr = self.run_metrics_gate(repo, "nonexistent-ref...HEAD")
            combined = stdout + stderr
            self.assertEqual(exit_code, 1)
            self.assertIn(
                "METRICS-GATE: BLOCK unresolvable range nonexistent-ref...HEAD (git exit 128)",
                combined,
            )

    def test_zero_sha_range_fails_closed(self):
        """Test: a pre-push style all-zero base sha that git cannot resolve BLOCKs.

        The pre-push hook hands the gate ``<zero-sha>...<sha>`` for a branch whose
        base does not exist on the remote. An unresolvable base must block.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = Path(tmpdir)
            self._init_repo(repo)
            self._commit(repo, "# Test\n", "initial")
            head = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=str(repo), capture_output=True, text=True, encoding="utf-8",
            ).stdout.strip()
            zero_range = "0" * 40 + "..." + head

            exit_code, stdout, stderr = self.run_metrics_gate(repo, zero_range)
            combined = stdout + stderr
            self.assertEqual(exit_code, 1)
            self.assertIn("unresolvable", combined)
            self.assertIn("BLOCK unresolvable range " + zero_range, combined)

    def test_normal_diff_with_no_claims_passes(self):
        """Test: regression - normal diff with no unverified claims still passes (exit 0)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = Path(tmpdir)
            self._init_repo(repo)
            self._commit(repo, "# Test\n", "initial")
            self._commit(repo, "# Test\n\nAdding some clean text without any metrics.\n", "add-clean")

            exit_code, stdout, stderr = self.run_metrics_gate(repo, "HEAD~1...HEAD")
            # Clean diff should pass
            self.assertEqual(exit_code, 0)

    def test_unverified_metric_still_fails(self):
        """Test: regression - unverified hard number claims still fail (exit 1)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = Path(tmpdir)
            self._init_repo(repo)
            self._commit(repo, "# Test\n", "initial")
            self._commit(repo, "# Test\n\nOur system is 5x faster.\n", "add-unverified")

            exit_code, stdout, stderr = self.run_metrics_gate(repo, "HEAD~1...HEAD")
            # Unverified multiplier claim should fail
            self.assertEqual(exit_code, 1)
            self.assertTrue("5x" in stdout or "5x" in stderr or "faster" in stdout or "faster" in stderr)

    def test_awk_field_refs_in_html_comment_not_flagged(self):
        """Test: awk field refs ($1 $2 $7) inside an HTML comment are not dollar metrics.

        Row f-91214b7d: a command transcript in a comment is not a claim, so only the
        genuine unverified '40%' line may be flagged.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = Path(tmpdir)
            self._init_repo(repo)
            self._commit(repo, "# Test\n", "initial")
            self._commit(
                repo,
                "# Test\n\n"
                "<!-- CMD: awk '{print $1, $2, $7}' file.log -->\n"
                "latency improved 40% after the change\n",
                "add-awk-and-metric",
            )

            exit_code, stdout, stderr = self.run_metrics_gate(repo, "HEAD~1...HEAD")
            combined = stdout + stderr
            hits = [line for line in combined.splitlines() if "unverified metric" in line]
            self.assertEqual(exit_code, 1, combined)
            self.assertEqual(len(hits), 1, combined)
            self.assertIn("'40%'", hits[0])
            for ref in ("$1", "$2", "$7"):
                self.assertNotIn("'" + ref + "'", combined)


CLAIM_ENTRY = "- 2026-10-20 12:47Z CI-QUEUE: 292 runs, 83% on superseded heads.\n"
RETRACTION = (
    "<!-- metrics-retracted: 2026-10-20 12:47Z '83%' "
    "reason: figure inconsistent with 1718/1884 = 91% -->\n"
)


class TestMetricsGateRetraction(unittest.TestCase):
    """Append-only log retraction: a LATER appended line may retract a historical
    unverified metric in BUILDLOG.md. Every other file stays under the strict gate."""

    def run_metrics_gate(self, repo, *args):
        result = subprocess.run(
            [sys.executable, str(METRICS_GATE)] + list(args),
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            cwd=str(repo),
        )
        return result.returncode, result.stdout, result.stderr

    @staticmethod
    def _init_repo(repo):
        subprocess.run(["git", "init"], cwd=str(repo), capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=str(repo), capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=str(repo), capture_output=True)

    @staticmethod
    def _append_commit(repo, name, text, message):
        path = repo.joinpath(name)
        path.write_text(path.read_text() + text if path.exists() else text)
        subprocess.run(["git", "add", name], cwd=str(repo), capture_output=True)
        subprocess.run(["git", "commit", "-m", message], cwd=str(repo), capture_output=True)

    def _log_repo(self, repo, name, claim_text, later_text):
        """Base commit, then the claim commit, then the (optional) later commit."""
        self._init_repo(repo)
        self._append_commit(repo, name, "# Log\n", "initial")
        self._append_commit(repo, name, claim_text, "claim")
        if later_text is not None:
            self._append_commit(repo, name, later_text, "later")

    def test_retracted_historical_metric_in_buildlog_passes(self):
        """Test: a later appended retraction naming the same entry timestamp and metric clears it."""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = Path(tmpdir)
            self._log_repo(repo, "BUILDLOG.md", CLAIM_ENTRY, RETRACTION)
            exit_code, stdout, stderr = self.run_metrics_gate(repo, "HEAD~2...HEAD")
            self.assertEqual(exit_code, 0, stdout + stderr)
            self.assertIn("NO-UNVERIFIED-METRICS: OK", stdout)

    def test_unretracted_buildlog_metric_still_fails(self):
        """Test (a): an unverified BUILDLOG metric with no retraction marker still fails."""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = Path(tmpdir)
            self._log_repo(repo, "BUILDLOG.md", CLAIM_ENTRY, None)
            exit_code, stdout, stderr = self.run_metrics_gate(repo, "HEAD~1...HEAD")
            self.assertEqual(exit_code, 1)
            self.assertIn("unverified metric '83%'", stdout + stderr)

    def test_retraction_with_different_timestamp_does_not_clear(self):
        """Test (b): a retraction naming another entry's timestamp does not clear the claim."""
        wrong_ts = RETRACTION.replace("12:47Z", "12:48Z")
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = Path(tmpdir)
            self._log_repo(repo, "BUILDLOG.md", CLAIM_ENTRY, wrong_ts)
            exit_code, stdout, stderr = self.run_metrics_gate(repo, "HEAD~2...HEAD")
            self.assertEqual(exit_code, 1)
            self.assertIn("unverified metric '83%'", stdout + stderr)

    def test_retraction_with_different_metric_does_not_clear(self):
        """Test (b): a retraction naming a different metric does not clear the claim."""
        wrong_metric = RETRACTION.replace("'83%'", "'84%'")
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = Path(tmpdir)
            self._log_repo(repo, "BUILDLOG.md", CLAIM_ENTRY, wrong_metric)
            exit_code, stdout, stderr = self.run_metrics_gate(repo, "HEAD~2...HEAD")
            self.assertEqual(exit_code, 1)
            self.assertIn("unverified metric '83%'", stdout + stderr)

    def test_retraction_before_claim_does_not_clear(self):
        """Test (b): only a LATER appended line retracts; an earlier marker does not."""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = Path(tmpdir)
            self._init_repo(repo)
            self._append_commit(repo, "BUILDLOG.md", "# Log\n", "initial")
            self._append_commit(repo, "BUILDLOG.md", RETRACTION + CLAIM_ENTRY, "retraction-first")
            exit_code, stdout, stderr = self.run_metrics_gate(repo, "HEAD~1...HEAD")
            self.assertEqual(exit_code, 1)
            self.assertIn("unverified metric '83%'", stdout + stderr)

    def test_retraction_does_not_apply_outside_log_files(self):
        """Test (c): the same claim plus retraction in a non-log .md file still fails."""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = Path(tmpdir)
            self._log_repo(repo, "notes.md", CLAIM_ENTRY, RETRACTION)
            exit_code, stdout, stderr = self.run_metrics_gate(repo, "HEAD~2...HEAD")
            self.assertEqual(exit_code, 1)
            self.assertIn("unverified metric '83%'", stdout + stderr)


if __name__ == "__main__":
    unittest.main()
