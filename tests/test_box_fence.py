"""Tests for tools/box_fence.py -- the BOX_PROTOCOL fence that stops a stale box.

Scenario: a bare origin and two clones (two boxes). Box A bumps BOX_PROTOCOL on origin;
box B is then behind. Contract under test:
  equal -> PASS; behind+clean -> fast-forward then PASS; behind+dirty -> FAIL and the tree
  is untouched; diverged -> FAIL; offline -> WARN, allow, marker written (and --push-check
  then fails closed while still offline); origin without BOX_PROTOCOL -> PASS;
  local missing but origin has it -> treated as behind.

Run: python -m unittest tests.test_box_fence
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
TOOL = REPO / "tools" / "box_fence.py"
ENV = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
       "GIT_COMMITTER_EMAIL": "t@t", "GIT_TERMINAL_PROMPT": "0"}


def git(cwd, *args):
    r = subprocess.run(["git", *args], cwd=str(cwd), env=ENV, capture_output=True, text=True, encoding="utf-8", errors="replace",
                       stdin=subprocess.DEVNULL)
    assert r.returncode == 0, f"git {args} failed: {r.stderr}"
    return r.stdout


def fence(repo, *extra):
    r = subprocess.run([sys.executable, str(TOOL), "--repo", str(repo), "--json", *extra],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", stdin=subprocess.DEVNULL, env=ENV, timeout=60)
    try:
        data = json.loads(r.stdout)
    except ValueError:
        data = {}
    return r.returncode, data, r.stdout + r.stderr


def make_origin_and_boxes(root, with_protocol=True):
    origin = root / "origin.git"
    subprocess.run(["git", "init", "--bare", "-b", "main", str(origin)], check=True,
                   capture_output=True, env=ENV)
    a, b = root / "boxA", root / "boxB"
    subprocess.run(["git", "clone", str(origin), str(a)], check=True, capture_output=True, env=ENV)
    git(a, "checkout", "-B", "main")
    if with_protocol:
        (a / "BOX_PROTOCOL").write_text("1\n")
    (a / "code.txt").write_text("v1\n")
    git(a, "add", "-A")
    git(a, "commit", "-m", "init")
    git(a, "push", "origin", "main")
    subprocess.run(["git", "clone", str(origin), str(b)], check=True, capture_output=True, env=ENV)
    return origin, a, b


class BoxFenceTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.origin, self.a, self.b = make_origin_and_boxes(self.root)

    def bump_origin(self, value="2"):
        (self.a / "BOX_PROTOCOL").write_text(value + "\n")
        git(self.a, "add", "-A")
        git(self.a, "commit", "-m", "bump protocol")
        git(self.a, "push", "origin", "main")

    def test_equal_passes(self):
        rc, data, _ = fence(self.b)
        self.assertEqual(rc, 0)
        self.assertEqual(data["status"], "PASS")

    def test_behind_clean_fast_forwards(self):
        self.bump_origin()
        rc, data, _ = fence(self.b)
        self.assertEqual(rc, 0)
        self.assertEqual(data["status"], "PASS")
        self.assertTrue(data["updated"])
        self.assertEqual((self.b / "BOX_PROTOCOL").read_text().strip(), "2")

    def test_behind_dirty_fails_and_tree_untouched(self):
        self.bump_origin()
        (self.b / "code.txt").write_text("my unsaved work\n")
        (self.b / "new.txt").write_text("untracked\n")
        before_status = git(self.b, "status", "--porcelain")
        before_head = git(self.b, "rev-parse", "HEAD")
        rc, data, _ = fence(self.b)
        self.assertNotEqual(rc, 0)
        self.assertEqual(data["status"], "FAIL")
        self.assertEqual(git(self.b, "status", "--porcelain"), before_status)
        self.assertEqual(git(self.b, "rev-parse", "HEAD"), before_head)
        self.assertEqual((self.b / "code.txt").read_text(), "my unsaved work\n")
        self.assertIn("git", data["message"])

    def test_diverged_fails(self):
        self.bump_origin()
        (self.b / "local.txt").write_text("local commit\n")
        git(self.b, "add", "-A")
        git(self.b, "commit", "-m", "local only")
        before_head = git(self.b, "rev-parse", "HEAD")
        rc, data, _ = fence(self.b)
        self.assertNotEqual(rc, 0)
        self.assertEqual(data["status"], "FAIL")
        self.assertEqual(git(self.b, "rev-parse", "HEAD"), before_head)

    def test_offline_warns_allows_and_marks(self):
        git(self.b, "remote", "set-url", "origin", str(self.root / "does-not-exist.git"))
        rc, data, _ = fence(self.b)
        self.assertEqual(rc, 0)
        self.assertEqual(data["status"], "WARN")
        marker = Path(data["marker"])
        self.assertTrue(marker.is_file())
        self.assertEqual(git(self.b, "status", "--porcelain"), "")  # marker never dirties the tree
        rc2, data2, _ = fence(self.b, "--push-check")
        self.assertNotEqual(rc2, 0)  # still offline at push time: fail closed
        self.assertEqual(data2["status"], "FAIL")

    def test_marker_cleared_after_online_pass(self):
        git(self.b, "remote", "set-url", "origin", str(self.root / "nope.git"))
        _, data, _ = fence(self.b)
        marker = Path(data["marker"])
        self.assertTrue(marker.is_file())
        git(self.b, "remote", "set-url", "origin", str(self.origin))
        rc, _, _ = fence(self.b, "--push-check")
        self.assertEqual(rc, 0)
        self.assertFalse(marker.exists())

    def test_origin_without_file_passes(self):
        git(self.a, "rm", "BOX_PROTOCOL")
        git(self.a, "commit", "-m", "drop")
        git(self.a, "push", "origin", "main")
        rc, data, _ = fence(self.b)
        self.assertEqual(rc, 0)
        self.assertEqual(data["status"], "PASS")


class MissingLocalTests(unittest.TestCase):
    def test_missing_locally_origin_has_it_is_behind(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _, a, b = make_origin_and_boxes(root, with_protocol=False)
            (a / "BOX_PROTOCOL").write_text("1\n")
            git(a, "add", "-A")
            git(a, "commit", "-m", "adopt protocol")
            git(a, "push", "origin", "main")
            # dirty variant first: behind, must FAIL, not pass
            (b / "code.txt").write_text("wip\n")
            rc, data, _ = fence(b)
            self.assertNotEqual(rc, 0)
            self.assertEqual(data["status"], "FAIL")
            git(b, "checkout", "--", "code.txt")
            rc, data, _ = fence(b)
            self.assertEqual(rc, 0)
            self.assertTrue(data["updated"])
            self.assertEqual((b / "BOX_PROTOCOL").read_text().strip(), "1")


if __name__ == "__main__":
    unittest.main()
