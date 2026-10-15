#!/usr/bin/env python3
"""Behavioral tests: a failing receipt matrix part keeps its output tail on disk.

Before this lane, tools/emit_receipt.py discarded every part's stdout/stderr, so a
part that exited 1 (py-shard-3 on 2026-10-14) was reported as `exit=1` with no
evidence of which test failed. A failing part must now leave
state/receipts/parts/<head_sha>/<part>.log holding the tail of its output, while the
receipt envelope keeps only the four documented per-part keys.
"""

import importlib.util
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
TOOLS = REPO_ROOT / "tools"
sys.path.insert(0, str(TOOLS))


def _load(name):
    spec = importlib.util.spec_from_file_location(name + "_under_test", TOOLS / (name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


emit = _load("emit_receipt")

_GIT_ENV = {
    "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@x", "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@x", "GIT_CONFIG_NOSYSTEM": "1",
}


def _git(cwd, *args):
    env = dict(os.environ)
    env.update(_GIT_ENV)
    env["HOME"] = str(cwd)
    return subprocess.run(["git", "-C", str(cwd)] + list(args), cwd=str(cwd), env=env,
                          capture_output=True, encoding="utf-8", errors="replace", check=True).stdout.strip()


def _mk_repo(tmp):
    repo = Path(tmp) / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    (repo / "a.txt").write_text("a\n", encoding="utf-8")
    _git(repo, "add", "a.txt")
    _git(repo, "commit", "-q", "-m", "base")
    return repo


def _py_part(code):
    """A matrix part that runs an inline python snippet (cwd = throwaway worktree)."""
    return emit.PartSpec(lambda repo: [sys.executable, "-c", code])


class TestFailingPartLog(unittest.TestCase):
    def test_failing_part_writes_log_with_stdout_and_stderr(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = _mk_repo(tmp)
            head = _git(repo, "rev-parse", "HEAD")
            registry = {"stub-fail": _py_part(
                "import sys; print('stub-stdout-marker'); "
                "print('stub-stderr-marker', file=sys.stderr); sys.exit(1)")}
            parts, _ = emit.run_matrix(repo, ["stub-fail"], registry=registry, jobs=1)
            self.assertEqual(parts[0]["exit_code"], 1)
            log = repo / "state" / "receipts" / "parts" / head / "stub-fail.log"
            self.assertTrue(log.is_file(), "failing part left no log at %s" % log)
            text = log.read_text(encoding="utf-8")
            self.assertIn("stub-stdout-marker", text)
            self.assertIn("stub-stderr-marker", text)

    def test_log_keeps_only_last_200_lines(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = _mk_repo(tmp)
            head = _git(repo, "rev-parse", "HEAD")
            registry = {"stub-long": _py_part(
                "import sys\nfor i in range(500): print('line-%03d' % i)\nsys.exit(3)")}
            emit.run_matrix(repo, ["stub-long"], registry=registry, jobs=1)
            text = (repo / "state" / "receipts" / "parts" / head / "stub-long.log").read_text(encoding="utf-8")
            self.assertIn("line-499", text)
            self.assertNotIn("line-000", text)
            self.assertLessEqual(sum(1 for ln in text.splitlines() if ln.startswith("line-")), 200)

    def test_passing_part_writes_no_log_and_envelope_keys_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = _mk_repo(tmp)
            head = _git(repo, "rev-parse", "HEAD")
            registry = {"stub-ok": _py_part("print('fine')")}
            parts, _ = emit.run_matrix(repo, ["stub-ok"], registry=registry, jobs=1)
            self.assertFalse((repo / "state" / "receipts" / "parts" / head / "stub-ok.log").exists())
            self.assertEqual(set(parts[0]), {"name", "exit_code", "test_count", "duration_s"})

    def test_failing_part_log_not_in_envelope(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = _mk_repo(tmp)
            registry = {"stub-fail": _py_part("print('secret-tail-text'); raise SystemExit(1)")}
            parts, skipped = emit.run_matrix(repo, ["stub-fail"], registry=registry, jobs=1)
            receipt = emit.build_receipt(repo, parts, skipped, slug="o/r", main_ref="HEAD")
            self.assertNotIn("secret-tail-text", repr(receipt))


if __name__ == "__main__":
    unittest.main()
