#!/usr/bin/env python3
"""
Test suite for tools/conflict_marker_check.py.

Reproduces the PR #834 incident class: a literal `<<<<<<< HEAD` conflict
block landed on main through a clean-merge, undetected by claudemd_lint,
claudemd_sync_gate, and all PR CI. These tests prove the new gate:

1. A fixture with a real marker FAILS (red-first: the gate must actually see it).
2. A clean fixture PASSES.
3. The SAME marker-containing fixture, once added to the allowlist, PASSES.
4. `--staged RANGE` catches a marker introduced in the diff range (and does
   NOT flag a marker that was already present before the range).
5. `--staged RANGE` does NOT re-flag a pre-existing marker outside the diff.
6. The inline `# conflict-marker-ok` suppression works on a single line.
7. Binary files are skipped (never opened as text).

Everything runs against a disposable temp git repo -- never the real tree --
so no marker-containing fixture is ever committed here (that would trip the
gate's own full-tree CI run on this very repo).
"""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

CHECK_SCRIPT = Path(__file__).parent.parent / "tools" / "conflict_marker_check.py"


class _ConflictMarkerFixture(unittest.TestCase):
    """Shared temp-repo fixture."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.repo_root = Path(self.tmpdir)

        subprocess.run(["git", "init"], cwd=self.repo_root, capture_output=True, check=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"],
                        cwd=self.repo_root, capture_output=True, check=True)
        subprocess.run(["git", "config", "user.name", "Test User"],
                        cwd=self.repo_root, capture_output=True, check=True)

        (self.repo_root / "tools").mkdir()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _write(self, relpath, content, binary=False):
        p = self.repo_root / relpath
        p.parent.mkdir(parents=True, exist_ok=True)
        if binary:
            p.write_bytes(content)
        else:
            p.write_text(content, encoding="utf-8", newline="\n")
        return p

    def _git(self, *args):
        return subprocess.run(["git"] + list(args), cwd=self.repo_root,
                               capture_output=True, text=True, encoding="utf-8",
                               errors="replace", check=True, timeout=15)

    def _commit_all(self, message="commit"):
        self._git("add", "-A")
        self._git("commit", "-m", message)
        return self._git("rev-parse", "HEAD").stdout.strip()

    def _write_allowlist(self, paths):
        allow_path = self.repo_root / "tools" / ".conflict-marker-allowlist.json"
        allow_path.write_text(
            json.dumps({"paths": paths}), encoding="utf-8",
        )
        return allow_path

    def _run(self, *extra_args):
        result = subprocess.run(
            [sys.executable, str(CHECK_SCRIPT), "--root", str(self.repo_root)]
            + list(extra_args),
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=30,
        )
        return result


class TestFullTreeMode(_ConflictMarkerFixture):
    def test_fixture_with_markers_fails(self):
        """Red-first: a tracked file containing a real conflict marker must
        be caught by the default (full-tree) mode."""
        self._write(
            "docs/CONFLICTED.md",
            "intro\n<<<<<<< HEAD\nours\n=======\ntheirs\n>>>>>>> feature\ntail\n",
        )
        self._commit_all()

        result = self._run("--check")
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("CONFLICTED.md", result.stderr)

    def test_clean_tree_passes(self):
        self._write("docs/CLEAN.md", "intro\nbody\ntail\n")
        self._commit_all()

        result = self._run("--check")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_fixture_allowlisted_passes(self):
        """The SAME marker-containing fixture passes once the allowlist
        names it -- proves the exemption mechanism, not just detection."""
        self._write(
            "docs/CONFLICTED.md",
            "intro\n<<<<<<< HEAD\nours\n=======\ntheirs\n>>>>>>> feature\ntail\n",
        )
        self._write_allowlist(["docs/CONFLICTED.md"])
        self._commit_all()

        result = self._run("--check")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_inline_suppression_on_marker_line(self):
        """`# conflict-marker-ok` on the marker line itself suppresses it,
        without needing a whole-file allowlist entry."""
        self._write(
            "docs/SUPPRESSED.md",
            "intro\n<<<<<<< HEAD # conflict-marker-ok\nbody\ntail\n",
        )
        self._commit_all()

        result = self._run("--check")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_diff3_base_marker_detected(self):
        self._write(
            "docs/DIFF3.md",
            "intro\n<<<<<<< HEAD\nours\n||||||| merged common ancestors\nbase\n=======\ntheirs\n>>>>>>> feature\n",
        )
        self._commit_all()

        result = self._run("--check")
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("DIFF3.md", result.stderr)

    def test_bare_equals_line_not_a_marker(self):
        """A line of exactly 7 '=' is a marker; shorter/longer runs, or a
        '=' run with trailing content, are not false positives."""
        self._write(
            "docs/ALMOST.md",
            "======\nnormal text\n========\n====== not quite\n",
        )
        self._commit_all()

        result = self._run("--check")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_binary_file_skipped(self):
        """A binary file containing marker-shaped bytes must never be opened
        as text (and must never be reported as a finding)."""
        self._write("assets/blob.png", b"\x89PNG\x00<<<<<<< HEAD\x00\xff\xfe", binary=True)
        self._commit_all()

        result = self._run("--check")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_missing_allowlist_file_is_not_an_error(self):
        """No allowlist file at all (fresh repo, nothing written) must still
        run cleanly -- empty allowlist is the fail-closed default, not a
        tool error."""
        self._write("docs/CLEAN.md", "fine\n")
        self._commit_all()

        result = self._run("--check")
        self.assertEqual(result.returncode, 0, result.stderr)


class TestStagedDiffMode(_ConflictMarkerFixture):
    def test_marker_introduced_in_range_is_caught(self):
        """A marker added by a NEW commit on top of a clean base is caught
        by `--staged <base>..<tip>`."""
        self._write("docs/FILE.md", "intro\nbody\ntail\n")
        base_sha = self._commit_all("base")

        self._write(
            "docs/FILE.md",
            "intro\n<<<<<<< HEAD\nours\n=======\ntheirs\n>>>>>>> feature\nbody\ntail\n",
        )
        tip_sha = self._commit_all("introduces marker")

        result = self._run("--staged", "%s..%s" % (base_sha, tip_sha))
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("FILE.md", result.stderr)

    def test_pre_existing_marker_outside_range_not_reflagged(self):
        """A marker already present BEFORE the range (i.e. not touched by
        this push's diff) is NOT reported by --staged -- that is the
        full-tree gate's job, and double-reporting every unrelated push
        touching the same file would be noise, not a new finding."""
        self._write(
            "docs/OLD.md",
            "intro\n<<<<<<< HEAD\nours\n=======\ntheirs\n>>>>>>> feature\ntail\n",
        )
        base_sha = self._commit_all("base already has a marker")

        # Touch an unrelated file in the range; OLD.md's marker predates it.
        self._write("docs/UNRELATED.md", "new file, no markers\n")
        tip_sha = self._commit_all("unrelated change")

        result = self._run("--staged", "%s..%s" % (base_sha, tip_sha))
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_clean_range_passes(self):
        self._write("docs/FILE.md", "intro\n")
        base_sha = self._commit_all("base")
        self._write("docs/FILE.md", "intro\nmore clean body\n")
        tip_sha = self._commit_all("clean change")

        result = self._run("--staged", "%s..%s" % (base_sha, tip_sha))
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_bad_range_fails_closed(self):
        """An unresolvable range is a git error, not 'nothing to check' --
        must exit 2, never 0."""
        self._write("docs/FILE.md", "intro\n")
        self._commit_all("base")

        result = self._run("--staged", "not-a-real-sha..also-not-real")
        self.assertEqual(result.returncode, 2, result.stderr)


class TestJsonOutput(_ConflictMarkerFixture):
    def test_json_mode_reports_findings(self):
        self._write("docs/CONFLICTED.md", "<<<<<<< HEAD\n=======\n>>>>>>> x\n")
        self._commit_all()

        result = self._run("--check", "--json")
        self.assertEqual(result.returncode, 1, result.stderr)
        payload = json.loads(result.stdout)
        self.assertFalse(payload["clean"])
        self.assertTrue(payload["findings"])
        self.assertEqual(payload["findings"][0]["file"], "docs/CONFLICTED.md")


if __name__ == "__main__":
    unittest.main()
