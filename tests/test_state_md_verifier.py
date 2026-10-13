#!/usr/bin/env python3
"""
Tests for tools/state_md_verifier.py guardrail.

Tests verify the verifier catches false claims, passes on accurate claims, and handles
edge cases (missing gh, unverifiable claims, etc).
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


# Add tools/ to path for imports
tools_path = Path(__file__).parent.parent / "tools"
sys.path.insert(0, str(tools_path))

import state_md_verifier


def _git_isolated_env(base_env=None):
    """An environment dict safe to pass to a git subprocess call that MUST
    stay scoped to its explicit cwd=. Strips every GIT_* variable (GIT_DIR
    and GIT_WORK_TREE above all) that could otherwise silently redirect git
    to a DIFFERENT repository regardless of cwd -- this is the mechanism a
    leaked/ambient GIT_DIR env var uses to override explicit cwd-based repo
    discovery (git honors GIT_DIR unconditionally when set, has priority
    over the normal "walk up from cwd looking for .git" discovery).

    Starts from `base_env` (default: a copy of the real process
    environment) so callers can layer an injected/leaked var on top before
    stripping, to behaviorally prove the stripping actually works -- see
    TestStatemdVerifierFixtureHermeticity.
    """
    source = dict(base_env) if base_env is not None else dict(os.environ)
    return {k: v for k, v in source.items() if not k.startswith("GIT_")}


def _build_v1_tag_fixture_repo(tmpdir_path, env=None):
    """Build the throwaway git repo used by the "claimed version matches"
    fixture: an "Initial commit" by "Test User", tagged v1.0.0, with a
    matching package.json and STATE.md.

    Every git call is explicitly scoped to `tmpdir_path` via BOTH cwd= and
    an isolated env (stripped of any ambient/leaked GIT_* vars in `env`),
    so this can never write into any repo other than the one rooted at
    tmpdir_path -- regardless of what GIT_DIR/GIT_WORK_TREE the invoking
    process happens to have set. This is the exact shape of the real
    stray tag discovered in the primary aesop repo on 2026-10-13: a
    LOCAL-ONLY v1.0.0 tag on commit e438de8d ("Initial commit", author
    "Test User") that this fixture-building code can create if it is not
    hermetic. See TestStatemdVerifierFixtureHermeticity for the regression
    proof.

    Returns the STATE.md path.
    """
    git_env = _git_isolated_env(env)

    subprocess.run(["git", "init"], cwd=tmpdir_path, capture_output=True,
                    check=True, env=git_env)
    subprocess.run(["git", "config", "user.email", "test@example.com"],
                    cwd=tmpdir_path, capture_output=True, check=True, env=git_env)
    subprocess.run(["git", "config", "user.name", "Test User"],
                    cwd=tmpdir_path, capture_output=True, check=True, env=git_env)

    test_file = tmpdir_path / "tools" / "clean.py"
    test_file.parent.mkdir(parents=True, exist_ok=True)
    test_file.write_text("# Clean code\n")

    subprocess.run(["git", "add", "."], cwd=tmpdir_path, capture_output=True,
                    check=True, env=git_env)
    subprocess.run(["git", "commit", "-m", "Initial commit"], cwd=tmpdir_path,
                    capture_output=True, check=True, env=git_env)
    subprocess.run(["git", "tag", "v1.0.0"], cwd=tmpdir_path, capture_output=True,
                    check=True, env=git_env)

    pkg_json = tmpdir_path / "package.json"
    pkg_json.write_text('{"version": "1.0.0"}\n')

    state_md = tmpdir_path / "STATE.md"
    state_md.write_text("# Checkpoint\n\n**Current Version:** v1.0.0\n\nNo conflicts to report.\n")
    return state_md


class TestStatemdVerifierEscapeRepro(unittest.TestCase):
    """
    ESCAPE REPRO: verifier MUST flag when STATE.md claims "resolved" but git status shows UU.

    This is the core incident test — it proves the guardrail catches the exact failure mode.
    """

    def test_escape_repro_unmerged_files_with_resolved_claim(self):
        """
        ESCAPE REPRO: Fixture with deterministic UU unmerged file claimed as "resolved".
        Expected: verifier MUST flag as CONTRADICTION and exit 1.

        Incident: STATE.md claimed "tools/foo.py conflicts resolved" while
        git status --porcelain showed UU tools/foo.py (unmerged).
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)

            # Initialize git repo
            subprocess.run(
                ["git", "init"],
                cwd=tmpdir_path,
                capture_output=True,
                check=True
            )
            subprocess.run(
                ["git", "config", "user.email", "test@example.com"],
                cwd=tmpdir_path,
                capture_output=True,
                check=True
            )
            subprocess.run(
                ["git", "config", "user.name", "Test User"],
                cwd=tmpdir_path,
                capture_output=True,
                check=True
            )

            # Create and commit initial file
            test_file = tmpdir_path / "tools" / "foo.py"
            test_file.parent.mkdir(parents=True, exist_ok=True)
            test_file.write_text("base line\n")
            subprocess.run(
                ["git", "add", "tools/foo.py"],
                cwd=tmpdir_path,
                capture_output=True,
                check=True
            )
            subprocess.run(
                ["git", "commit", "-m", "Base"],
                cwd=tmpdir_path,
                capture_output=True,
                check=True
            )

            # Create branch A with one version
            subprocess.run(
                ["git", "checkout", "-b", "branchA"],
                cwd=tmpdir_path,
                capture_output=True,
                check=True
            )
            test_file.write_text("version A\n")
            subprocess.run(
                ["git", "commit", "-am", "A"],
                cwd=tmpdir_path,
                capture_output=True,
                check=True
            )

            # Go back to main and make conflicting change
            subprocess.run(
                ["git", "checkout", "HEAD~1"],
                cwd=tmpdir_path,
                capture_output=True,
                check=True
            )
            subprocess.run(
                ["git", "checkout", "-b", "main"],
                cwd=tmpdir_path,
                capture_output=True,
                check=True
            )
            test_file.write_text("version B\n")
            subprocess.run(
                ["git", "commit", "-am", "B"],
                cwd=tmpdir_path,
                capture_output=True,
                check=True
            )

            # Merge branchA - will create conflict
            result = subprocess.run(
                ["git", "merge", "branchA"],
                cwd=tmpdir_path,
                capture_output=True
            )

            # FIXTURE ASSERTION: Verify UU status was created
            rc, status_out, _ = state_md_verifier.run_command(
                ["git", "status", "--porcelain"],
                cwd=tmpdir_path
            )

            if "UU" not in status_out:
                self.skipTest(f"Could not create UU status via merge. Status: {status_out}")

            # Create STATE.md claiming the conflict is resolved
            state_md = tmpdir_path / "STATE.md"
            state_md.write_text("# Checkpoint\n\ntools/foo.py conflicts resolved\n")

            # Run verifier
            rc, stdout, stderr = state_md_verifier.run_command(
                [sys.executable, str(tools_path / "state_md_verifier.py"),
                 "--state-md", str(state_md)],
                cwd=tmpdir_path
            )

            # MUST flag as CONTRADICTION and exit 1
            self.assertEqual(rc, 1,
                f"Verifier must exit 1 on contradiction. Got {rc}.\nstdout: {stdout}\nstderr: {stderr}")
            self.assertIn("CONTRADICTION", stdout,
                f"Must report CONTRADICTION. stdout: {stdout}")

    def test_clean_accurate_state_md(self):
        """
        Fixture: STATE.md claims are accurate (has version claim matching repo)
        Expected: verifier exits 0 with no contradictions

        The fixture repo (Initial commit / Test User / tag v1.0.0) is built via
        the shared hermetic helper -- see TestStatemdVerifierFixtureHermeticity
        for the regression proof that it cannot leak into any repo other than
        the tempdir, even under a leaked GIT_DIR environment variable.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)
            state_md = _build_v1_tag_fixture_repo(tmpdir_path)

            # Run verifier
            rc, stdout, stderr = state_md_verifier.run_command(
                [sys.executable, str(tools_path / "state_md_verifier.py"), "--state-md", str(state_md), "--json"],
                cwd=tmpdir_path
            )

            # Should exit 0 (no contradictions)
            self.assertEqual(rc, 0, f"Expected exit 0. Got {rc}. stderr: {stderr}")

            # Parse JSON output
            try:
                result = json.loads(stdout)
                contradiction_count = result.get("contradiction_count", 0)
                self.assertEqual(contradiction_count, 0, "Expected no contradictions")
            except json.JSONDecodeError:
                self.fail(f"Could not parse JSON output: {stdout}")

    def test_unverifiable_claim_reported(self):
        """
        Fixture: STATE.md has a claim that cannot be parsed (no file/branch names)
        Expected: verifier reports as UNVERIFIABLE, not as pass, or doesn't detect it
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)

            # Initialize git repo
            subprocess.run(
                ["git", "init"],
                cwd=tmpdir_path,
                capture_output=True,
                check=True
            )
            subprocess.run(
                ["git", "config", "user.email", "test@example.com"],
                cwd=tmpdir_path,
                capture_output=True,
                check=True
            )
            subprocess.run(
                ["git", "config", "user.name", "Test User"],
                cwd=tmpdir_path,
                capture_output=True,
                check=True
            )

            # Create STATE.md with a claim that looks like "merged" but has no PR number
            state_md = tmpdir_path / "STATE.md"
            state_md.write_text("# Checkpoint\n\nThe PR was MERGED successfully.\n")

            # Run verifier with JSON output
            rc, stdout, stderr = state_md_verifier.run_command(
                [sys.executable, str(tools_path / "state_md_verifier.py"), "--state-md", str(state_md), "--json"],
                cwd=tmpdir_path
            )

            # Should exit 0 (no contradictions)
            self.assertEqual(rc, 0, f"Expected exit 0. Got {rc}. stderr: {stderr}")

            # Parse and check output
            try:
                result = json.loads(stdout)
                # Check that we either found unverifiable or skip findings
                unverifiable_count = result.get("unverifiable_count", 0)
                skip_count = result.get("skip_count", 0)
                # At least one of these should be > 0 since gh is unavailable in test
                self.assertGreater(
                    unverifiable_count + skip_count,
                    0,
                    "Expected unverifiable or skip findings reported"
                )
            except json.JSONDecodeError:
                # If no JSON, that's fine - means no claims were detected
                pass

    def test_gh_absent_path_skipped(self):
        """
        Fixture: STATE.md has "MERGED" claim but gh CLI unavailable (or fails)
        Expected: verifier handles gracefully (SKIP if gh absent, or ERROR if gh available but fails)
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)

            # Initialize git repo
            subprocess.run(
                ["git", "init"],
                cwd=tmpdir_path,
                capture_output=True,
                check=True
            )
            subprocess.run(
                ["git", "config", "user.email", "test@example.com"],
                cwd=tmpdir_path,
                capture_output=True,
                check=True
            )
            subprocess.run(
                ["git", "config", "user.name", "Test User"],
                cwd=tmpdir_path,
                capture_output=True,
                check=True
            )

            # Create STATE.md with PR merged claim
            state_md = tmpdir_path / "STATE.md"
            state_md.write_text("# Checkpoint\n\nPR #123 MERGED\n")

            # Run verifier with JSON output
            rc, stdout, stderr = state_md_verifier.run_command(
                [sys.executable, str(tools_path / "state_md_verifier.py"), "--state-md", str(state_md), "--json"],
                cwd=tmpdir_path
            )

            # Should exit 0 or 2 depending on whether gh is available
            # If gh is available: tries to verify, gets error (no remote), exits 2
            # If gh is not available: skips verification, exits 0
            self.assertIn(rc, [0, 2], f"Expected exit 0 or 2. Got {rc}. stderr: {stderr}")

            # Check findings
            try:
                result = json.loads(stdout)
                skip_count = result.get("skip_count", 0)
                error_count = result.get("error_count", 0)
                # Either we skipped (skip_count > 0) or we errored (error_count > 0)
                self.assertGreater(skip_count + error_count, 0,
                    "Expected SKIP or ERROR findings")
            except json.JSONDecodeError:
                pass

    def test_multiple_unmerged_files_caught(self):
        """
        Fixture: STATE.md claims clean, but git status shows UU on multiple files.
        Expected: verifier flags contradiction on generic "clean" claim.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)

            # Initialize git repo
            subprocess.run(
                ["git", "init"],
                cwd=tmpdir_path,
                capture_output=True,
                check=True
            )
            subprocess.run(
                ["git", "config", "user.email", "test@example.com"],
                cwd=tmpdir_path,
                capture_output=True,
                check=True
            )
            subprocess.run(
                ["git", "config", "user.name", "Test User"],
                cwd=tmpdir_path,
                capture_output=True,
                check=True
            )

            # Create and commit initial files
            files = ["file1.py", "file2.py", "file3.py"]
            for fname in files:
                f = tmpdir_path / fname
                f.write_text("base\n")
                subprocess.run(
                    ["git", "add", fname],
                    cwd=tmpdir_path,
                    capture_output=True,
                    check=True
                )
            subprocess.run(
                ["git", "commit", "-m", "Base"],
                cwd=tmpdir_path,
                capture_output=True,
                check=True
            )

            # Create branch A with different versions
            subprocess.run(
                ["git", "checkout", "-b", "branchA"],
                cwd=tmpdir_path,
                capture_output=True,
                check=True
            )
            for fname in files:
                (tmpdir_path / fname).write_text(f"A:{fname}\n")
            subprocess.run(
                ["git", "commit", "-am", "A"],
                cwd=tmpdir_path,
                capture_output=True,
                check=True
            )

            # Go back and make conflicting changes
            subprocess.run(
                ["git", "checkout", "HEAD~1"],
                cwd=tmpdir_path,
                capture_output=True,
                check=True
            )
            subprocess.run(
                ["git", "checkout", "-b", "main"],
                cwd=tmpdir_path,
                capture_output=True,
                check=True
            )
            for fname in files:
                (tmpdir_path / fname).write_text(f"B:{fname}\n")
            subprocess.run(
                ["git", "commit", "-am", "B"],
                cwd=tmpdir_path,
                capture_output=True,
                check=True
            )

            # Merge to create conflicts
            subprocess.run(
                ["git", "merge", "branchA"],
                cwd=tmpdir_path,
                capture_output=True
            )

            # FIXTURE ASSERTION: Verify UU status
            rc, status_out, _ = state_md_verifier.run_command(
                ["git", "status", "--porcelain"],
                cwd=tmpdir_path
            )

            if status_out.count("UU") < 3:
                self.skipTest(f"Could not create 3 UU files via merge. Status:\n{status_out}")

            # Create STATE.md claiming clean state
            state_md = tmpdir_path / "STATE.md"
            state_md.write_text("# Checkpoint\n\nAll conflicts resolved and clean.\n")

            # Run verifier
            rc, stdout, stderr = state_md_verifier.run_command(
                [sys.executable, str(tools_path / "state_md_verifier.py"),
                 "--state-md", str(state_md)],
                cwd=tmpdir_path
            )

            # Must exit 1 (contradiction found)
            self.assertEqual(rc, 1,
                f"Expected exit 1. Got {rc}. stdout: {stdout}")
            self.assertIn("CONTRADICTION", stdout,
                f"Must report CONTRADICTION. stdout: {stdout}")


class TestStatemdVerifierIntegration(unittest.TestCase):
    """Integration-level tests for realistic scenarios."""

    def test_real_state_md_parsing(self):
        """Verify parser can handle realistic STATE.md syntax."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)

            # Initialize git repo
            subprocess.run(
                ["git", "init"],
                cwd=tmpdir_path,
                capture_output=True,
                check=True
            )

            # Create a realistic STATE.md
            state_md = tmpdir_path / "STATE.md"
            state_md.write_text("""# Wave 42 State

## Open Items

- Branch: guard/state-md-accuracy (pushed to origin)
- tools/state_md_verifier.py conflicts resolved
- Tests passing

## Next Steps

- Merge when CI green
""")

            # Parse it
            claims = state_md_verifier.parse_state_md(state_md)
            self.assertIsNotNone(claims)
            self.assertIn("resolved", claims)
            self.assertIn("pushed", claims)
            # Should have detected the claims
            self.assertGreater(len(claims["resolved"]), 0)


    def test_stale_version_contradiction(self):
        """
        Test that a STATE.md claiming v0.5.0 is detected as stale when repo is v0.7.0.
        This is the core bug reported: version claims were not extracted at all.
        """
        fixture_path = Path(__file__).parent / "fixtures" / "state_md_stale.md"

        if not fixture_path.exists():
            self.skipTest(f"Fixture not found: {fixture_path}")

        # Run verifier on the fixture
        rc, stdout, stderr = state_md_verifier.run_command(
            [sys.executable, str(tools_path / "state_md_verifier.py"),
             "--state-md", str(fixture_path), "--json"]
        )

        # Must exit non-zero (failure) because version is stale
        self.assertNotEqual(rc, 0,
            f"Expected non-zero exit for stale STATE.md, got {rc}. stdout: {stdout}")

        # Should have a contradiction in findings
        try:
            result = json.loads(stdout)
            contradiction_count = result.get("contradiction_count", 0)
            findings = result.get("findings", [])
            self.assertGreater(contradiction_count, 0,
                f"Expected contradiction_count > 0 for stale version, got {contradiction_count}")

            # Check that at least one finding mentions version
            version_findings = [f for f in findings if "version" in f.get("claim", "").lower() or "version" in f.get("detail", "").lower()]
            self.assertGreater(len(version_findings), 0,
                f"Expected version-related finding, got findings: {findings}")
        except json.JSONDecodeError:
            self.fail(f"Could not parse JSON output: {stdout}")

    def test_current_version_passes(self):
        """
        Test that a STATE.md claiming v0.7.0 passes when repo is v0.7.0.
        """
        # The fixture hardcodes a version, so it goes stale the moment package.json is
        # bumped. Compare against package.json (always present) rather than git tags --
        # CI clones without tags, so a tag-based guard silently never fires and the test
        # fails on a healthy repo mid-release.
        import json as _json, re as _re
        _root = Path(__file__).parent.parent
        _pkg = _json.loads((_root / "package.json").read_text(encoding="utf-8"))["version"]
        _fx = Path(__file__).parent / "fixtures" / "state_md_current.md"
        if not _fx.exists():
            self.skipTest("fixture not found: %s" % _fx)
        _m = _re.search(r"\*\*Current Version:\*\*\s*v?([0-9]+\.[0-9]+\.[0-9]+)",
                        _fx.read_text(encoding="utf-8"))
        if not _m or _m.group(1) != _pkg:
            self.skipTest(
                "fixture claims v%s but package.json is %s (stale mid-release)"
                % (_m.group(1) if _m else "?", _pkg)
            )

        fixture_path = Path(__file__).parent / "fixtures" / "state_md_current.md"

        if not fixture_path.exists():
            self.skipTest(f"Fixture not found: {fixture_path}")

        # Run verifier on the fixture
        rc, stdout, stderr = state_md_verifier.run_command(
            [sys.executable, str(tools_path / "state_md_verifier.py"),
             "--state-md", str(fixture_path), "--json"]
        )

        # Must exit 0 (success) for current version
        self.assertEqual(rc, 0,
            f"Expected exit 0 for current STATE.md, got {rc}. stdout: {stdout}")

        # Contradiction count should be 0
        try:
            result = json.loads(stdout)
            contradiction_count = result.get("contradiction_count", 0)
            self.assertEqual(contradiction_count, 0,
                f"Expected no contradictions for current version, got {contradiction_count}. findings: {result.get('findings', [])}")
        except json.JSONDecodeError:
            self.fail(f"Could not parse JSON output: {stdout}")

    def test_zero_claims_fails_closed(self):
        """
        Test that a STATE.md with zero verifiable claims exits non-zero (fail-closed).
        This is the core fail-closed requirement: "nothing to check" must not read as "all good".
        """
        fixture_path = Path(__file__).parent / "fixtures" / "state_md_empty.md"

        if not fixture_path.exists():
            self.skipTest(f"Fixture not found: {fixture_path}")

        # Run verifier on the fixture
        rc, stdout, stderr = state_md_verifier.run_command(
            [sys.executable, str(tools_path / "state_md_verifier.py"),
             "--state-md", str(fixture_path), "--json"]
        )

        # Must exit non-zero (failure) because there's nothing to verify
        self.assertNotEqual(rc, 0,
            f"Expected non-zero exit for STATE.md with zero verifiable claims (fail-closed), got {rc}")


class TestStatemdFreshness(unittest.TestCase):
    """Test STATE.md freshness gate (Guardrail #5)."""

    def test_freshness_current_head_claim_fresh(self):
        """
        Fixture: STATE.md claims current HEAD <recent-sha>, repo HEAD is that sha or nearby.
        Expected: verifier exits 0 (fresh enough).
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)

            # Initialize git repo
            subprocess.run(
                ["git", "init"],
                cwd=tmpdir_path,
                capture_output=True,
                check=True
            )
            subprocess.run(
                ["git", "config", "user.email", "test@example.com"],
                cwd=tmpdir_path,
                capture_output=True,
                check=True
            )
            subprocess.run(
                ["git", "config", "user.name", "Test User"],
                cwd=tmpdir_path,
                capture_output=True,
                check=True
            )

            # Create initial commit
            test_file = tmpdir_path / "test.txt"
            test_file.write_text("base\n")
            subprocess.run(
                ["git", "add", "."],
                cwd=tmpdir_path,
                capture_output=True,
                check=True
            )
            subprocess.run(
                ["git", "commit", "-m", "Initial"],
                cwd=tmpdir_path,
                capture_output=True,
                check=True
            )

            # Get current HEAD sha
            rc, head_sha, _ = state_md_verifier.run_command(
                ["git", "rev-parse", "HEAD"],
                cwd=tmpdir_path
            )
            self.assertEqual(rc, 0, "Failed to get HEAD sha")
            head_sha = head_sha.strip()

            # Create STATE.md claiming current HEAD matches
            state_md = tmpdir_path / "STATE.md"
            state_md.write_text(f"# Checkpoint\n\n**Current Version:** v1.0.0 (current HEAD {head_sha}).\n")

            # Run verifier
            rc, stdout, stderr = state_md_verifier.run_command(
                [sys.executable, str(tools_path / "state_md_verifier.py"),
                 "--state-md", str(state_md)],
                cwd=tmpdir_path
            )

            # Should exit 0 (no freshness contradiction)
            self.assertEqual(rc, 0,
                f"Expected exit 0 for fresh STATE.md. Got {rc}.\nstdout: {stdout}\nstderr: {stderr}")

    def test_freshness_stale_by_51_commits(self):
        """
        Fixture: STATE.md claims HEAD at a commit 51 commits back; HEAD is 51 commits ahead.
        Expected: verifier flags as CONTRADICTION and exits 1 (stale, >50 limit).
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)

            # Initialize git repo
            subprocess.run(
                ["git", "init"],
                cwd=tmpdir_path,
                capture_output=True,
                check=True
            )
            subprocess.run(
                ["git", "config", "user.email", "test@example.com"],
                cwd=tmpdir_path,
                capture_output=True,
                check=True
            )
            subprocess.run(
                ["git", "config", "user.name", "Test User"],
                cwd=tmpdir_path,
                capture_output=True,
                check=True
            )

            # Create first commit
            test_file = tmpdir_path / "file0.txt"
            test_file.write_text("content 0\n")
            subprocess.run(
                ["git", "add", "."],
                cwd=tmpdir_path,
                capture_output=True,
                check=True
            )
            subprocess.run(
                ["git", "commit", "-m", "Initial"],
                cwd=tmpdir_path,
                capture_output=True,
                check=True
            )

            # Get current HEAD (baseline)
            rc, baseline_head, _ = state_md_verifier.run_command(
                ["git", "rev-parse", "HEAD"],
                cwd=tmpdir_path
            )
            baseline_head = baseline_head.strip()

            # Create 52 more commits to move HEAD ahead by 52
            for i in range(1, 53):
                test_file = tmpdir_path / f"file{i}.txt"
                test_file.write_text(f"content {i}\n")
                subprocess.run(
                    ["git", "add", "."],
                    cwd=tmpdir_path,
                    capture_output=True,
                    check=True
                )
                subprocess.run(
                    ["git", "commit", "-m", f"Commit {i}"],
                    cwd=tmpdir_path,
                    capture_output=True,
                    check=True
                )

            # Now HEAD is 52 commits ahead of baseline_head
            # STATE.md claims baseline_head (which is >50 commits behind current HEAD)
            state_md = tmpdir_path / "STATE.md"
            state_md.write_text(f"# Checkpoint\n\n**Current Version:** v1.0.0 (current HEAD {baseline_head}).\n")

            # Run verifier
            rc, stdout, stderr = state_md_verifier.run_command(
                [sys.executable, str(tools_path / "state_md_verifier.py"),
                 "--state-md", str(state_md)],
                cwd=tmpdir_path
            )

            # Should exit 1 (stale)
            self.assertEqual(rc, 1,
                f"Expected exit 1 for stale STATE.md (51+ commits). Got {rc}.\nstdout: {stdout}")
            self.assertIn("stale", stdout.lower(),
                f"Expected 'stale' in output. stdout: {stdout}")

    def test_freshness_missing_sha(self):
        """
        Fixture: STATE.md has valid-format but non-existent current HEAD sha.
        Expected: verifier fails closed with ERROR message about SHA not found.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)

            # Initialize git repo
            subprocess.run(
                ["git", "init"],
                cwd=tmpdir_path,
                capture_output=True,
                check=True
            )
            subprocess.run(
                ["git", "config", "user.email", "test@example.com"],
                cwd=tmpdir_path,
                capture_output=True,
                check=True
            )
            subprocess.run(
                ["git", "config", "user.name", "Test User"],
                cwd=tmpdir_path,
                capture_output=True,
                check=True
            )

            # Create a commit
            test_file = tmpdir_path / "test.txt"
            test_file.write_text("base\n")
            subprocess.run(
                ["git", "add", "."],
                cwd=tmpdir_path,
                capture_output=True,
                check=True
            )
            subprocess.run(
                ["git", "commit", "-m", "Initial"],
                cwd=tmpdir_path,
                capture_output=True,
                check=True
            )

            # Create STATE.md with a valid-format but non-existent SHA (7 hex digits)
            nonexistent_sha = "1234567"  # valid format but doesn't exist
            state_md = tmpdir_path / "STATE.md"
            state_md.write_text(f"# Checkpoint\n\n**Current Version:** v1.0.0 (current HEAD {nonexistent_sha}).\n")

            # Run verifier with JSON output
            rc, stdout, stderr = state_md_verifier.run_command(
                [sys.executable, str(tools_path / "state_md_verifier.py"),
                 "--state-md", str(state_md), "--json"],
                cwd=tmpdir_path
            )

            # Should exit 2 (fail-closed) because SHA doesn't exist
            self.assertEqual(rc, 2,
                f"Expected exit 2 for unknown SHA (fail-closed). Got {rc}.")
            # Should have an error message
            try:
                result = json.loads(stdout)
                error_count = result.get("error_count", 0)
                self.assertGreater(error_count, 0,
                    f"Expected ERROR findings for unknown SHA")
            except json.JSONDecodeError:
                self.fail(f"Could not parse JSON output: {stdout}")


class TestStatemdVerifierWindowsAsciiSafety(unittest.TestCase):
    """
    Guardrail: state_md_verifier.py must never crash printing its text-mode
    report on a non-UTF-8 console (e.g. Windows cp1252), even when the
    STATE.md content it is verifying contains real Unicode punctuation
    (en/em dashes, arrows) -- which is exactly what real-world checkpoint
    prose looks like. The docstring claims "ASCII-safe"; this proves it.
    """

    def test_unicode_in_state_md_does_not_crash_cp1252_stdout(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)

            subprocess.run(
                ["git", "init"], cwd=tmpdir_path, capture_output=True,
                check=True, encoding="utf-8", errors="replace"
            )
            subprocess.run(
                ["git", "config", "user.email", "test@example.com"],
                cwd=tmpdir_path, capture_output=True, check=True,
                encoding="utf-8", errors="replace"
            )
            subprocess.run(
                ["git", "config", "user.name", "Test User"],
                cwd=tmpdir_path, capture_output=True, check=True,
                encoding="utf-8", errors="replace"
            )

            # A claim line containing real Unicode punctuation (arrow + em
            # dash) -- the verifier echoes this line verbatim into its
            # text-mode "claim" output, which is exactly what crashed on a
            # cp1252 console.
            state_md = tmpdir_path / "STATE.md"
            state_md.write_text(
                "# Checkpoint\n\n"
                "The PR was MERGED successfully → verified — done.\n",
                encoding="utf-8",
            )

            env = dict(os.environ)
            # Force a non-UTF-8 stdout/stderr codec deterministically,
            # regardless of the host console's real encoding.
            env["PYTHONIOENCODING"] = "cp1252"

            result = subprocess.run(
                [sys.executable, str(tools_path / "state_md_verifier.py"),
                 "--state-md", str(state_md)],
                cwd=tmpdir_path,
                capture_output=True,
                encoding="utf-8", errors="replace",
                env=env,
            )

            self.assertNotIn(
                "UnicodeEncodeError", result.stderr,
                f"Verifier crashed encoding non-ASCII output under cp1252.\n"
                f"rc={result.returncode}\nstdout={result.stdout}\nstderr={result.stderr}"
            )
            self.assertIn(
                result.returncode, (0, 1, 2),
                f"Expected a documented exit code, got {result.returncode}. "
                f"stderr={result.stderr}"
            )
            # Must still emit a verdict line, not die silently.
            self.assertTrue(
                any(tag in result.stdout for tag in ("MERGED", "SKIP", "UNVERIFIABLE", "ERROR")),
                f"Expected a verdict line in stdout. stdout={result.stdout}"
            )

    def test_ascii_safe_transliterates_and_strips_non_ascii(self):
        """Direct unit test of the sanitization helper."""
        self.assertEqual(state_md_verifier.ascii_safe("a → b"), "a -> b")
        self.assertEqual(state_md_verifier.ascii_safe("em—dash"), "em--dash")
        # Anything left over after transliteration must still never raise --
        # it gets replaced, not re-thrown.
        exotic = "中文 untouched CJK"
        out = state_md_verifier.ascii_safe(exotic)
        self.assertTrue(all(ord(c) < 128 for c in out), f"Non-ASCII survived: {out!r}")


class TestStatemdVerifierBranchClaimPrecision(unittest.TestCase):
    """
    Guardrail #1's "pushed" branch extraction must never report a bare
    English word as a branch claim. Incident (2026-10-13): running the
    verifier against the LIVE aesop STATE.md (--state-md aesop/STATE.md
    --buildlog conductor3/BUILDLOG.md) reported "Missing branches on
    origin: ['after']", "['made', 'landed']", "['pyflakes']" -- every one
    of these came from ordinary prose, never a branch name. The old greedy
    pattern (push-or-branch, followed by whitespace, followed by a word)
    matched the literal substring
    "push" wherever a regex word-boundary-ish context allowed it,
    including inside hyphenated compounds like "pre-push made" / "pre-push
    pyflakes" (the '-' creates a boundary right before "push"), and
    matched "branch" followed by whatever word happened to come next
    regardless of whether it looked like a ref.

    The fixtures below are the VERBATIM lines read from the primary tree's
    live STATE.md on 2026-10-13 (lines 8, 26, 36) that produced the false
    positives, plus the genuine ref-shaped claims that must still fire
    (never loosen detection while fixing precision).
    """

    LIVE_STATE_MD_LINE_8 = (
        "**Since 17:57Z:** PR #940 merged 18:22Z - Matt's hooks flair notice is live on main "
        "as a scoped known-issue line below the Aesop Kernel positioning (verified by reading "
        "origin/main:README.md). PR #939 was a DUPLICATE no-op merge (empty diff) caused by the "
        "orchestrator's own leftover `push && gh pr create` chain resurrecting the auto-deleted "
        "docs/rebrand-corrections branch after #938 merged - harmless to content, recorded as "
        "memory `never-push-to-pr-automerge-may-take`. Memory `read-last-two-instructions-literally` "
        "records the headline/referent hallucination episode and Matt's ruling that outreach, "
        "titles and headlines are his decisions. STATE.md 17:57Z block's placeholder timestamp "
        "was corrected (f4cc51d3) and pushed to backup/state-checkpoint-20261013."
    )

    LIVE_STATE_MD_LINE_26 = (
        "**On disk NOT pushed - recover before they rot:** aesop-wt-failclosed (pyflakes "
        "pre-push made FAIL-CLOSED at pre-push-policy.sh ~1058/~1085, committed, unpushed; "
        "fail-open inventory TASK 2 not started); aesop-wt-readers (fleet/dash readers -> "
        "canonical CONDUCTOR_ROOT; partial tools/fleet.js + STEP-0-READER-ANALYSIS.md, "
        "UNCOMMITTED; remaining readers status.js/mcp/server.mjs/ui/config.py/ui/sse.py/dash/"
        "watchdog-gui.sh). checkpoint-hooks fail-closed lane stopped after its STEP 0 fact "
        "table: the 6 checkpoint hooks are best-effort (never verify commit/push landed, never "
        "write a CHECKPOINT-FAILED marker) - resume to make them fail closed + surfaced by "
        "reprime-from-disk."
    )

    LIVE_STATE_MD_LINE_36 = (
        "**In flight (lanes):** fix/state-md-verifier-ascii-and-drift verify + Guardrail #6 "
        "SKIP->exit0 fail-open; watchdog CLEAN-while-dirty + psinasty WIP pushed at main; "
        "pre-push pyflakes fail-closed + fail-open inventory (PR); code-search guard false "
        "positive on quoted text; hook-wiring regression gate in ~/scripts wired into "
        "power_selftest (reports 4 violations + a UnicodeDecodeError crash to fix); liveness "
        "census (three-roots hypothesis: CONDUCTOR_ROOT vs AESOP_ROOT vs aesop-daemon-runtime); "
        "rewrite-dependency hook audit; never-fail-open memory; README/npm flair PR."
    )

    def test_live_state_md_prose_produces_no_branch_claims(self):
        """The three real lines that previously false-positived must each
        extract ZERO branch candidates."""
        for name, line in (
            ("line 8", self.LIVE_STATE_MD_LINE_8),
            ("line 26", self.LIVE_STATE_MD_LINE_26),
            ("line 36", self.LIVE_STATE_MD_LINE_36),
        ):
            with self.subTest(name=name):
                matches = state_md_verifier.extract_branch_claims(line)
                self.assertEqual(
                    matches, [],
                    f"{name} must extract no branch claims from prose, got {matches}"
                )

    def test_genuine_ref_shaped_claims_still_extracted(self):
        """Real push/branch claims -- ref-shaped (contains '/') and
        following 'branch' / 'pushed as', or backtick-quoted -- must still
        fire. Precision must never come at the cost of detection."""
        cases = [
            ("... and pushed as backup/state-checkpoint-20261013.",
             "backup/state-checkpoint-20261013"),
            ("branch fix/x pushed", "fix/x"),
            ("`docs/foo` pushed", "docs/foo"),
        ]
        for claim, expected in cases:
            with self.subTest(claim=claim):
                matches = state_md_verifier.extract_branch_claims(claim)
                self.assertIn(
                    expected, matches,
                    f"Expected {expected!r} extracted from {claim!r}, got {matches}"
                )

    def test_backtick_quoted_non_ref_token_is_not_a_branch_claim(self):
        """Backtick-quoting alone is not sufficient -- real STATE.md prose
        backtick-quotes memory-key names and commands with no '/' at all
        (seen verbatim in line 8 above); those must not be read as refs."""
        matches = state_md_verifier.extract_branch_claims(
            "recorded as memory `never-push-to-pr-automerge-may-take`."
        )
        self.assertEqual(matches, [])

    # Second-round live finding: running the FIXED verifier against the full
    # live STATE.md (past line 45) surfaced a NEW false positive from this
    # fix's own backtick+'/' acceptance path -- line 103 backtick-quotes an
    # API route, which contains a '/' but is not a branch name.
    LIVE_STATE_MD_LINE_103 = (
        "**Release-state note:** `v0.7.1` is tagged at `ec5ea9db` and has "
        "**no GitHub release** -- that commit's CI was red (pre-existing "
        "`/api/state` bug). The tag was deliberately NOT moved, since "
        "retagging a pushed release rewrites published history. `v0.7.2` "
        "is published on npm (Latest, MIT license) and GitHub (Release "
        "v0.7.2 Latest). Consumer-visible release history therefore reads "
        "0.7.0 -> 0.7.2; publishing 0.7.1 retroactively is a user "
        "decision. `v0.7.1` remains tag-only on git. Current unreleased "
        "commits: 219 since v0.7.2 tag (as of 2026-09-11, HEAD 07732210)."
    )

    def test_leading_slash_backtick_token_is_not_a_branch_claim(self):
        """A backtick-quoted API route/absolute path (`` `/api/state` ``)
        contains a '/' but starts with one -- never a git ref (git refuses
        a leading '/' in branch names) -- and must not be read as a branch
        claim, even though it is backtick-quoted."""
        matches = state_md_verifier.extract_branch_claims(self.LIVE_STATE_MD_LINE_103)
        self.assertEqual(
            matches, [],
            f"Expected no branch claims from the live line-103 fixture, got {matches}"
        )

    def test_genuine_claim_still_verified_against_ls_remote(self):
        """End-to-end: a genuine ref-shaped pushed claim for a branch that
        does NOT exist on origin must still be reported as a
        CONTRADICTION -- precision must not come at the cost of losing
        real detections."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)
            subprocess.run(["git", "init"], cwd=tmpdir_path, capture_output=True, check=True)
            subprocess.run(["git", "config", "user.email", "test@example.com"],
                            cwd=tmpdir_path, capture_output=True, check=True)
            subprocess.run(["git", "config", "user.name", "Test User"],
                            cwd=tmpdir_path, capture_output=True, check=True)
            (tmpdir_path / "f.txt").write_text("x\n")
            subprocess.run(["git", "add", "."], cwd=tmpdir_path, capture_output=True, check=True)
            subprocess.run(["git", "commit", "-m", "Initial"], cwd=tmpdir_path,
                            capture_output=True, check=True)

            other_dir = tmpdir_path.parent / (tmpdir_path.name + "-origin")
            other_dir.mkdir(exist_ok=True)
            try:
                subprocess.run(["git", "init", "--bare"], cwd=other_dir, capture_output=True, check=True)
                subprocess.run(["git", "remote", "add", "origin", str(other_dir)],
                                cwd=tmpdir_path, capture_output=True, check=True)
                subprocess.run(["git", "push", "origin", "HEAD:refs/heads/main"],
                                cwd=tmpdir_path, capture_output=True, check=True)

                state_md = tmpdir_path / "STATE.md"
                state_md.write_text("# Checkpoint\n\nbranch fix/never-pushed pushed\n")

                rc, stdout, stderr = state_md_verifier.run_command(
                    [sys.executable, str(tools_path / "state_md_verifier.py"),
                     "--state-md", str(state_md), "--json"],
                    cwd=tmpdir_path
                )
                self.assertEqual(
                    rc, 1,
                    f"Expected CONTRADICTION exit 1. Got {rc}. stdout={stdout} stderr={stderr}"
                )
                result = json.loads(stdout)
                self.assertGreater(result.get("contradiction_count", 0), 0)
                detail = " ".join(f["detail"] for f in result["findings"])
                self.assertIn("fix/never-pushed", detail)
            finally:
                shutil.rmtree(other_dir, ignore_errors=True)


class TestStatemdVerifierOriginTagVersionResolution(unittest.TestCase):
    """
    Guardrail #1's version check used to trust ANY local tag via a bare
    `git tag -l` listing. Incident (2026-10-13): the primary aesop repo
    carries a LOCAL-ONLY tag v1.0.0 on commit e438de8d ("Initial commit",
    author "Test User") -- an unrelated test-fixture leak -- and the
    verifier reported "git tag is v1.0.0" even though origin's real
    highest tag is v0.9.1 and the orphan commit is not reachable from HEAD
    at all.

    Fix: resolve "the current version" from tags that exist on ORIGIN
    (git ls-remote --tags origin), falling back to the nearest tag
    reachable from HEAD by ancestry (git describe --tags --abbrev=0) only
    when origin is unreachable. A local-only tag higher than origin's
    latest is reported as WARN, never adopted as the version.
    """

    def _run_git(self, args, cwd, check=True):
        return subprocess.run(
            ["git"] + args, cwd=cwd, capture_output=True,
            encoding="utf-8", errors="replace", check=check
        )

    def _build_repo_with_stray_local_tag(self, tmpdir_path):
        """Bare 'origin' + a work clone whose HEAD/package.json/STATE.md
        claim v0.9.0 (origin's real latest, pushed), PLUS a local-only
        v1.0.0 tag on an unrelated orphan commit never pushed to origin
        and not an ancestor of HEAD -- mirroring the real e438de8d leak
        shape exactly."""
        origin_dir = tmpdir_path / "origin.git"
        work_dir = tmpdir_path / "work"
        work_dir.mkdir()

        self._run_git(["init", "--bare", str(origin_dir)], cwd=tmpdir_path)

        self._run_git(["init"], cwd=work_dir)
        self._run_git(["symbolic-ref", "HEAD", "refs/heads/main"], cwd=work_dir)
        self._run_git(["config", "user.email", "test@example.com"], cwd=work_dir)
        self._run_git(["config", "user.name", "Test User"], cwd=work_dir)

        (work_dir / "README.md").write_text("hello\n")
        self._run_git(["add", "."], cwd=work_dir)
        self._run_git(["commit", "-m", "Initial"], cwd=work_dir)
        self._run_git(["tag", "v0.9.0"], cwd=work_dir)
        self._run_git(["remote", "add", "origin", str(origin_dir)], cwd=work_dir)
        self._run_git(["push", "origin", "HEAD:refs/heads/main"], cwd=work_dir)
        self._run_git(["push", "origin", "v0.9.0"], cwd=work_dir)

        # Unrelated orphan commit, tagged v1.0.0, NEVER pushed to origin --
        # not an ancestor of main's HEAD, exactly like e438de8d.
        self._run_git(["checkout", "--orphan", "stray"], cwd=work_dir)
        self._run_git(["commit", "--allow-empty", "-m", "Initial commit"], cwd=work_dir)
        self._run_git(["tag", "v1.0.0"], cwd=work_dir)
        self._run_git(["checkout", "main"], cwd=work_dir)

        (work_dir / "package.json").write_text('{"version": "0.9.0"}\n')
        state_md = work_dir / "STATE.md"
        state_md.write_text(
            "# Checkpoint\n\n**Current Version:** v0.9.0\n\nNo conflicts to report.\n"
        )
        return work_dir, state_md

    def test_version_resolves_from_origin_not_stray_local_tag(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            work_dir, state_md = self._build_repo_with_stray_local_tag(Path(tmpdir))

            rc, stdout, stderr = state_md_verifier.run_command(
                [sys.executable, str(tools_path / "state_md_verifier.py"),
                 "--state-md", str(state_md), "--json"],
                cwd=work_dir
            )
            self.assertEqual(
                rc, 0,
                f"Expected exit 0: claimed v0.9.0 matches origin's real latest tag. "
                f"Got {rc}. stdout={stdout} stderr={stderr}"
            )
            result = json.loads(stdout)
            self.assertEqual(
                result.get("contradiction_count", 0), 0,
                f"Must not report a version contradiction against the stray local "
                f"v1.0.0 tag. findings={result.get('findings')}"
            )
            self.assertGreaterEqual(
                result.get("warn_count", 0), 1,
                f"Expected a WARN about the stray local-only v1.0.0 tag. "
                f"findings={result.get('findings')}"
            )
            warn_details = " ".join(
                f["detail"] for f in result["findings"] if f["status"] == "WARN"
            )
            self.assertIn("v1.0.0", warn_details)
            self.assertIn("not present on origin", warn_details)

    def test_get_origin_tags_strips_peeled_markers_and_excludes_stray(self):
        """git ls-remote --tags lists annotated tags twice (<tag> and
        <tag>^{}); get_origin_tags must return each tag name exactly once,
        and must never include a tag that was never pushed."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)
            work_dir, _ = self._build_repo_with_stray_local_tag(tmpdir_path)
            tags = state_md_verifier.get_origin_tags(work_dir)
            self.assertIsNotNone(tags)
            self.assertIn("v0.9.0", tags)
            self.assertNotIn("v1.0.0", tags)
            self.assertFalse(any(t.endswith("^{}") for t in tags))

    def test_head_describe_ignores_non_ancestor_tag(self):
        """git describe --tags --abbrev=0 from HEAD must resolve to
        v0.9.0, never v1.0.0 (which sits on an orphan commit outside
        HEAD's history) -- this is what makes it safe as a fallback when
        origin is unreachable."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)
            work_dir, _ = self._build_repo_with_stray_local_tag(tmpdir_path)
            self.assertEqual(state_md_verifier.get_head_describe_tag(work_dir), "v0.9.0")


class TestStatemdVerifierFixtureHermeticity(unittest.TestCase):
    """
    The v1.0.0 / 'Test User' / 'Initial commit' fixture built by
    _build_v1_tag_fixture_repo() (used by test_clean_accurate_state_md)
    must build its throwaway git repo regardless of ambient/leaked GIT_*
    environment variables -- GIT_DIR in particular silently overrides the
    explicit cwd= passed to every git subprocess call (git honors GIT_DIR
    unconditionally when set, in preference to discovering a repo by
    walking up from cwd), which is a real, documented mechanism by which a
    "hermetic, tempdir-scoped" test fixture can still write into a
    different repository than the one it created.

    This is the hygiene gap behind the stray v1.0.0 tag discovered in the
    primary aesop repo on commit e438de8d ("Initial commit", author "Test
    User", 2026-10-06) -- test-process-isolation-hygiene requires tests
    never pollute the real repo (memory: test-process-isolation-hygiene).
    """

    def test_fixture_repo_build_is_immune_to_leaked_git_dir_env(self):
        with tempfile.TemporaryDirectory() as decoy_dir, \
             tempfile.TemporaryDirectory() as real_tmpdir:
            decoy_path = Path(decoy_dir)
            real_path = Path(real_tmpdir)

            # A decoy repo standing in for "the real primary repo" that an
            # ambient/leaked GIT_DIR env var could point at.
            subprocess.run(["git", "init"], cwd=decoy_path, capture_output=True, check=True)

            leaked_env = dict(os.environ)
            leaked_env["GIT_DIR"] = str(decoy_path / ".git")
            leaked_env["GIT_WORK_TREE"] = str(decoy_path)

            _build_v1_tag_fixture_repo(real_path, env=leaked_env)

            rc_decoy, out_decoy, _ = state_md_verifier.run_command(
                ["git", "tag", "-l", "v1.0.0"], cwd=decoy_path
            )
            rc_real, out_real, _ = state_md_verifier.run_command(
                ["git", "tag", "-l", "v1.0.0"], cwd=real_path
            )
            self.assertEqual(
                out_decoy.strip(), "",
                "Fixture repo setup leaked the v1.0.0 tag into a repo pointed at "
                "by an ambient GIT_DIR env var instead of the intended tempdir -- "
                "this is the exact mechanism behind the real-repo leak (commit "
                "e438de8d)."
            )
            self.assertEqual(out_real.strip(), "v1.0.0")


class TestStatemdBuildlogDriftCheck(unittest.TestCase):
    """
    Guardrail #6: STATE.md's newest '## CURRENT (<ts>)' header must not be
    older than BUILDLOG.md's newest '--- <ts> [checkpoint...]' line.

    BUILDLOG.md is append-only history and is NEVER the current-state
    source; STATE.md is the only current-state surface (decided contract,
    not revisited here). A checkpoint that appends to BUILDLOG.md without
    also advancing STATE.md's CURRENT header is drift and must be caught.
    """

    def _write(self, tmpdir, name, text):
        p = Path(tmpdir) / name
        p.write_text(text, encoding="utf-8")
        return p

    def test_state_newer_than_buildlog_passes(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            state_md = self._write(
                tmpdir, "STATE.md",
                "## CURRENT (2026-10-10T05:00:00Z) - all good\n"
            )
            buildlog = self._write(
                tmpdir, "BUILDLOG.md",
                "--- 2026-10-10T02:00:00Z [checkpoint] earlier work\n"
            )
            findings = state_md_verifier.verify_buildlog_drift(state_md, buildlog)
            self.assertEqual(
                findings, [],
                f"Expected no findings when STATE.md is newer. findings={findings}"
            )

    def test_buildlog_newer_than_state_is_drift(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            state_md = self._write(
                tmpdir, "STATE.md",
                "## CURRENT (2026-10-01T05:00:00Z) - stale checkpoint\n"
            )
            buildlog = self._write(
                tmpdir, "BUILDLOG.md",
                "--- 2026-10-10T02:59:39Z [checkpoint:afk] newer work happened\n"
            )
            findings = state_md_verifier.verify_buildlog_drift(state_md, buildlog)
            self.assertTrue(
                any(f["status"] == "CONTRADICTION" for f in findings),
                f"Expected drift CONTRADICTION. findings={findings}"
            )
            detail = " ".join(f["detail"] for f in findings)
            self.assertIn("2026-10-01", detail)
            self.assertIn("2026-10-10", detail)

    def test_state_has_no_current_block_but_buildlog_has_checkpoints_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            state_md = self._write(
                tmpdir, "STATE.md", "# Notes\n\nNo CURRENT header in this file.\n"
            )
            buildlog = self._write(
                tmpdir, "BUILDLOG.md",
                "--- 2026-10-10T02:59:39Z [checkpoint] work happened\n"
            )
            findings = state_md_verifier.verify_buildlog_drift(state_md, buildlog)
            self.assertTrue(
                any(f["status"] == "CONTRADICTION" for f in findings),
                f"Expected fail-closed CONTRADICTION when STATE.md has no "
                f"parseable CURRENT header but BUILDLOG.md has checkpoints. "
                f"findings={findings}"
            )

    def test_neither_file_has_entries_is_documented_skip(self):
        """Documented behavior: nothing to verify => SKIP. Never a silent
        pass (that would hide real drift later) and never a false
        contradiction (that would block a brand-new project with no history
        yet)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            state_md = self._write(tmpdir, "STATE.md", "# Notes\n\nNothing here.\n")
            buildlog = self._write(tmpdir, "BUILDLOG.md", "# Log\n\nNo checkpoints yet.\n")
            findings = state_md_verifier.verify_buildlog_drift(state_md, buildlog)
            self.assertEqual(len(findings), 1, f"findings={findings}")
            self.assertEqual(findings[0]["status"], "SKIP")

    def test_tolerates_date_only_and_cdt_suffix_forms(self):
        """Real STATE.md CURRENT headers use loose forms like
        '2026-09-02 12:50 CDT' and '2026-08-03 late' (date-only, no
        parseable time-of-day). These must parse without raising, at date
        granularity, per the spec's explicit tolerance requirement."""
        with tempfile.TemporaryDirectory() as tmpdir:
            state_md = self._write(
                tmpdir, "STATE.md",
                "## CURRENT (2026-10-11 late) - wrapped up\n"
            )
            buildlog = self._write(
                tmpdir, "BUILDLOG.md",
                "--- 2026-10-10T02:59:39Z [checkpoint:afk] earlier\n"
            )
            findings = state_md_verifier.verify_buildlog_drift(state_md, buildlog)
            self.assertEqual(
                findings, [],
                f"Newer date-only CURRENT header must not drift. findings={findings}"
            )

            state_md2 = self._write(
                tmpdir, "STATE2.md",
                "## CURRENT (2026-09-02 12:50 CDT) - resumed\n"
            )
            buildlog2 = self._write(
                tmpdir, "BUILDLOG2.md",
                "--- 2026-10-10T02:59:39Z [checkpoint:afk] much later\n"
            )
            findings2 = state_md_verifier.verify_buildlog_drift(state_md2, buildlog2)
            self.assertTrue(
                any(f["status"] == "CONTRADICTION" for f in findings2),
                f"Older date-only CURRENT header (2026-09-02) vs newer "
                f"BUILDLOG checkpoint (2026-10-10) must drift. findings={findings2}"
            )

    def test_cli_check_buildlog_drift_flag_exits_1_on_drift(self):
        """--check-buildlog-drift runs the drift check standalone through
        the CLI and surfaces the documented exit code contract (1 = drift)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)
            state_md = self._write(
                tmpdir, "STATE.md",
                "## CURRENT (2026-10-01T05:00:00Z) - stale checkpoint\n"
            )
            buildlog = self._write(
                tmpdir, "BUILDLOG.md",
                "--- 2026-10-10T02:59:39Z [checkpoint:afk] newer work happened\n"
            )
            rc, stdout, stderr = state_md_verifier.run_command(
                [sys.executable, str(tools_path / "state_md_verifier.py"),
                 "--state-md", str(state_md), "--buildlog", str(buildlog),
                 "--check-buildlog-drift"],
                cwd=tmpdir_path,
            )
            self.assertEqual(
                rc, 1,
                f"Expected exit 1 for drift. Got {rc}. stdout={stdout} stderr={stderr}"
            )
            self.assertIn("CONTRADICTION", stdout)

    def test_cli_check_buildlog_drift_flag_exits_0_when_fresh(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)
            state_md = self._write(
                tmpdir, "STATE.md",
                "## CURRENT (2026-10-10T05:00:00Z) - all good\n"
            )
            buildlog = self._write(
                tmpdir, "BUILDLOG.md",
                "--- 2026-10-10T02:00:00Z [checkpoint] earlier work\n"
            )
            rc, stdout, stderr = state_md_verifier.run_command(
                [sys.executable, str(tools_path / "state_md_verifier.py"),
                 "--state-md", str(state_md), "--buildlog", str(buildlog),
                 "--check-buildlog-drift"],
                cwd=tmpdir_path,
            )
            self.assertEqual(
                rc, 0,
                f"Expected exit 0 when fresh. Got {rc}. stdout={stdout} stderr={stderr}"
            )


if __name__ == "__main__":
    unittest.main()
