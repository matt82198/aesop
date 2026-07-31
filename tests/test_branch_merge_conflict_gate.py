"""Tests for tools/branch_merge_conflict_gate.py -- branch merge-conflict gate.

Root cause: PR #657 had a real textual merge conflict against main, so GitHub
could not materialize a merge commit to run CI on it at all ("mergeable":
"CONFLICTING", "failingChecks": []). This gate simulates merges via
`git merge-tree` (never touches the working tree/index) to catch that class
of failure before someone opens the PR and finds CI silently never ran.

Covers: the defect is caught (real conflict -> exit 1 with conflicted file
named), clean input passes (exit 0), and zero-input exits 2 (both an
explicit-branches list that fully fails to resolve, and auto-discovery
finding no candidate branches at all).
"""

import json
import subprocess
import sys
import unittest
from pathlib import Path

TOOL = str(Path(__file__).resolve().parent.parent / "tools" / "branch_merge_conflict_gate.py")


def git(repo_path, *args, timeout=30):
    """Run a git command against repo_path. Never uses os.chdir."""
    result = subprocess.run(
        ["git", "-C", str(repo_path)] + list(args),
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=timeout,
    )
    if result.returncode != 0:
        raise RuntimeError(f"git {args} failed: {result.stderr}")
    return result.stdout


def run_gate(repo_path, extra_args=None):
    cmd = [sys.executable, TOOL, "--root", str(repo_path), "--json"]
    if extra_args:
        cmd.extend(extra_args)
    result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", timeout=60)
    parsed = None
    try:
        parsed = json.loads(result.stdout)
    except (json.JSONDecodeError, ValueError):
        pass
    return result.returncode, parsed, result.stdout, result.stderr


def make_repo(tmp_path):
    """Scaffold a fresh git repo at tmp_path, scoped identity, base commit on main."""
    git(tmp_path, "init", "-q", "-b", "main")
    git(tmp_path, "config", "user.email", "gate-test@example.com")
    git(tmp_path, "config", "user.name", "Gate Test")
    (Path(tmp_path) / "f.txt").write_text("line1\n", encoding="utf-8")
    git(tmp_path, "add", "f.txt")
    git(tmp_path, "commit", "-q", "-m", "base")
    return tmp_path


class TestBranchMergeConflictGate(unittest.TestCase):
    """CLI-level tests against real (temp, hermetic) git repos."""

    def test_real_conflict_is_caught(self):
        """Two branches editing the same line of the same file -> exit 1, finding names the file."""
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            repo = make_repo(tmp)
            git(repo, "checkout", "-q", "-b", "branchA")
            (Path(repo) / "f.txt").write_text("line1-A\n", encoding="utf-8")
            git(repo, "commit", "-q", "-am", "A")
            git(repo, "checkout", "-q", "main")
            (Path(repo) / "f.txt").write_text("line1-B\n", encoding="utf-8")
            git(repo, "commit", "-q", "-am", "B")

            code, parsed, out, err = run_gate(repo, ["--base", "main", "--branches", "branchA"])

            self.assertEqual(code, 1, msg=f"stdout={out} stderr={err}")
            self.assertIsNotNone(parsed)
            self.assertEqual(parsed["status"], "findings")
            self.assertEqual(len(parsed["findings"]), 1)
            self.assertEqual(parsed["findings"][0]["branch"], "branchA")
            self.assertIn("f.txt", parsed["findings"][0]["conflicted_files"])
            self.assertEqual(parsed["errors"], [])

    def test_clean_divergence_passes(self):
        """A branch that only adds a new file merges cleanly -> exit 0, no findings."""
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            repo = make_repo(tmp)
            git(repo, "checkout", "-q", "-b", "branchC")
            (Path(repo) / "g.txt").write_text("new file\n", encoding="utf-8")
            git(repo, "add", "g.txt")
            git(repo, "commit", "-q", "-m", "C")

            code, parsed, out, err = run_gate(repo, ["--base", "main", "--branches", "branchC"])

            self.assertEqual(code, 0, msg=f"stdout={out} stderr={err}")
            self.assertIsNotNone(parsed)
            self.assertEqual(parsed["status"], "clean")
            self.assertEqual(parsed["findings"], [])
            self.assertEqual(parsed["clean_count"], 1)

    def test_unrelated_edits_do_not_conflict(self):
        """Two branches editing different files/lines never false-positive."""
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            repo = make_repo(tmp)
            (Path(repo) / "other.txt").write_text("untouched\n", encoding="utf-8")
            git(repo, "add", "other.txt")
            git(repo, "commit", "-q", "-m", "add other.txt on main")

            git(repo, "checkout", "-q", "-b", "branchD")
            (Path(repo) / "f.txt").write_text("line1\nline2-from-D\n", encoding="utf-8")
            git(repo, "commit", "-q", "-am", "D edits end of f.txt")

            code, parsed, out, err = run_gate(repo, ["--base", "main", "--branches", "branchD"])

            self.assertEqual(code, 0, msg=f"stdout={out} stderr={err}")
            self.assertEqual(parsed["findings"], [])

    def test_zero_input_explicit_branches_exits_2(self):
        """Every named branch fails to resolve -> COULD NOT EVALUATE, exit 2 (never 0)."""
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            repo = make_repo(tmp)

            code, parsed, out, err = run_gate(repo, ["--base", "main", "--branches", "does-not-exist"])

            self.assertEqual(code, 2, msg=f"stdout={out} stderr={err}")
            self.assertIsNotNone(parsed)
            self.assertEqual(parsed["status"], "error")
            self.assertEqual(parsed["checked_count"], 0)
            self.assertEqual(parsed["findings"], [])

    def test_zero_input_auto_discovery_exits_2(self):
        """Default discovery (gh open-PR list) with no remote configured -> exit 2, never a silent 0."""
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            repo = make_repo(tmp)
            # No remote configured and no --branches/--all-remote-branches: default
            # discovery shells out to `gh pr list`, which fails against a repo with
            # no GitHub remote at all. That failure must surface as COULD NOT
            # EVALUATE, not as a silent "0 branches found, all clean".

            code, parsed, out, err = run_gate(repo, ["--base", "main"])

            self.assertEqual(code, 2, msg=f"stdout={out} stderr={err}")
            self.assertIsNotNone(parsed)
            self.assertEqual(parsed["status"], "error")
            self.assertEqual(parsed["checked_count"], 0)
            self.assertEqual(parsed["findings"], [])

    def test_all_remote_branches_mode_empty_set_exits_2(self):
        """--all-remote-branches with no remote-tracking branches at all -> exit 2."""
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            repo = make_repo(tmp)

            code, parsed, out, err = run_gate(repo, ["--base", "main", "--all-remote-branches"])

            self.assertEqual(code, 2, msg=f"stdout={out} stderr={err}")
            self.assertIsNotNone(parsed)
            self.assertEqual(parsed["status"], "error")
            self.assertEqual(parsed["checked_count"], 0)

    def test_unresolvable_base_exits_2(self):
        """An explicit --base that does not resolve is COULD NOT EVALUATE, not a clean pass."""
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            repo = make_repo(tmp)

            code, parsed, out, err = run_gate(repo, ["--base", "no-such-base", "--branches", "main"])

            self.assertEqual(code, 2, msg=f"stdout={out} stderr={err}")
            self.assertIsNone(parsed["base_ref"])

    def test_not_a_git_repo_exits_2(self):
        """Root that is not a git repository at all -> exit 2, never a silent 0."""
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            code, parsed, out, err = run_gate(tmp, [])

            self.assertEqual(code, 2, msg=f"stdout={out} stderr={err}")
            self.assertIsNotNone(parsed)
            self.assertEqual(parsed["status"], "error")

    def test_include_local_auto_discovers_conflicting_and_clean_branches(self):
        """--all-remote-branches --include-local reports both a conflict and a clean branch."""
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            repo = make_repo(tmp)
            git(repo, "checkout", "-q", "-b", "branchA")
            (Path(repo) / "f.txt").write_text("line1-A\n", encoding="utf-8")
            git(repo, "commit", "-q", "-am", "A")
            git(repo, "checkout", "-q", "main")
            (Path(repo) / "f.txt").write_text("line1-B\n", encoding="utf-8")
            git(repo, "commit", "-q", "-am", "B")
            git(repo, "checkout", "-q", "-b", "branchC")
            (Path(repo) / "g.txt").write_text("new\n", encoding="utf-8")
            git(repo, "add", "g.txt")
            git(repo, "commit", "-q", "-m", "C")
            git(repo, "checkout", "-q", "main")

            code, parsed, out, err = run_gate(repo, ["--base", "main", "--all-remote-branches", "--include-local"])

            self.assertEqual(code, 1, msg=f"stdout={out} stderr={err}")
            branches_with_findings = {f["branch"] for f in parsed["findings"]}
            self.assertIn("branchA", branches_with_findings)
            self.assertEqual(parsed["clean_count"], 1)  # branchC


if __name__ == "__main__":
    unittest.main()
