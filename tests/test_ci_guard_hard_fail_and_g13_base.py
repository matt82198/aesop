#!/usr/bin/env python3
"""
Red-first tests for two fail-open holes in the verify-receipt base-trust guard and G13.

1. CI must never SKIP the base-keyring guard. tests/test_verify_receipt_base_trust.py skipped
   its keyring class whenever `cryptography` was missing, and the CI shards did not install it,
   so the guard silently never ran. Under CI (CI or GITHUB_ACTIONS set) a missing dependency
   must be a FAILURE. Locally the skip is kept so developers without cryptography are not blocked.

2. The G13 symbol-survival gate must be judged by the BASE (origin/main) copy of its tool, never
   the PR head copy. A PR that weakens tools/pr_symbol_survival_check.py to exit 0 must not pass a
   lossy merge. Driven through the real hook function check_symbol_survival against a real
   temp origin + clone; nothing is mocked.
"""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
HOOK = REPO_ROOT / "hooks" / "pre-push-policy.sh"
REAL_G13_TOOL = REPO_ROOT / "tools" / "pr_symbol_survival_check.py"

_CI_VARS = ("CI", "GITHUB_ACTIONS")
_GIT_BASH_WIN = r"C:\Program Files\Git\bin\bash.exe"


def _bash():
    """Return a POSIX bash for the hook. On Windows, PATH 'bash' can be the WSL launcher
    (C:\\Windows\\System32\\bash.exe), which prints 'Windows Subsystem for Linux has no
    installed distributions' and never runs the hook. Prefer Git Bash; fail closed if absent."""
    if sys.platform == "win32":
        found = shutil.which("bash")
        if found and "system32" not in found.lower():
            return found
        if os.path.exists(_GIT_BASH_WIN):
            return _GIT_BASH_WIN
        raise AssertionError("no Git Bash found for the pre-push hook (looked on PATH and %s)" % _GIT_BASH_WIN)
    return shutil.which("bash") or "bash"


def _child_env(extra=None, drop=()):
    env = dict(os.environ)
    for key in _CI_VARS + tuple(drop):
        env.pop(key, None)
    if extra:
        env.update(extra)
    return env


class TestCryptoGuardFailsUnderCI(unittest.TestCase):
    """Run the real base-trust test module in a child with cryptography blocked."""

    _CHILD = (
        "import sys, unittest\n"
        "sys.modules['cryptography'] = None\n"
        "sys.path.insert(0, sys.argv[1])\n"
        "suite = unittest.defaultTestLoader.loadTestsFromName('tests.test_verify_receipt_base_trust')\n"
        "r = unittest.TextTestRunner(verbosity=0, stream=sys.stdout).run(suite)\n"
        "print('COUNTS run=%d skipped=%d failures=%d errors=%d' % (r.testsRun, len(r.skipped), len(r.failures), len(r.errors)))\n"
        "sys.exit(0 if r.wasSuccessful() else 1)\n"
    )

    def _run_child(self, env):
        return subprocess.run(
            [sys.executable, "-c", self._CHILD, str(REPO_ROOT)],
            cwd=str(REPO_ROOT), env=env, capture_output=True,
            encoding="utf-8", errors="replace", timeout=300,
        )

    def test_missing_cryptography_under_ci_is_failure_not_skip(self):
        res = self._run_child(_child_env(extra={"CI": "true", "GITHUB_ACTIONS": "true"}))
        out = res.stdout + res.stderr
        self.assertNotEqual(res.returncode, 0, out[-2000:])
        self.assertIn("skipped=0", out, out[-2000:])
        self.assertIn("failures=4", out, out[-2000:])

    def test_missing_cryptography_locally_still_skips(self):
        res = self._run_child(_child_env(drop=_CI_VARS))
        out = res.stdout + res.stderr
        self.assertEqual(res.returncode, 0, out[-2000:])
        self.assertIn("skipped=4", out, out[-2000:])


def _git(cwd, *args):
    env = dict(os.environ)
    env.update({
        "GIT_AUTHOR_NAME": "g13", "GIT_AUTHOR_EMAIL": "g13@x.invalid",
        "GIT_COMMITTER_NAME": "g13", "GIT_COMMITTER_EMAIL": "g13@x.invalid",
        "GIT_CONFIG_NOSYSTEM": "1", "HOME": str(Path(cwd).parent / "home"),
    })
    return subprocess.run(["git", "-C", str(cwd)] + list(args), env=env,
                          capture_output=True, encoding="utf-8", errors="replace",
                          check=True).stdout.strip()


def _write(repo, rel, text):
    p = Path(repo) / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8", newline="\n")


class _HookResult:
    def __init__(self, returncode, stdout, stderr):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class TestG13JudgedByBaseTool(unittest.TestCase):
    """Real check_symbol_survival over a real origin + clone. Base = origin/main."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="g13base_"))
        (self.tmp / "home").mkdir()
        (self.tmp / "tmpdir").mkdir()
        self.origin = self.tmp / "origin.git"
        self.work = self.tmp / "work"
        _git(self.tmp, "init", "-q", "--bare", "-b", "main", str(self.origin))
        self.work.mkdir()
        _git(self.work, "init", "-q", "-b", "main")
        _git(self.work, "remote", "add", "origin", str(self.origin))
        (self.work / "tools").mkdir()
        shutil.copy(str(REAL_G13_TOOL), str(self.work / "tools" / "pr_symbol_survival_check.py"))
        _write(self.work, "mod.py", "def base_fn():\n    return 1\n")
        _git(self.work, "add", "-A")
        _git(self.work, "commit", "-q", "-m", "base")
        _git(self.work, "push", "-q", "origin", "main")
        _git(self.work, "checkout", "-q", "-b", "feature/x")
        _write(self.work, "mod.py", "def base_fn():\n    return 1\n\ndef alpha():\n    return 2\n")
        _git(self.work, "add", "-A")
        _git(self.work, "commit", "-q", "-m", "A adds alpha")
        self.a_sha = _git(self.work, "rev-parse", "HEAD")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _merge(self, tree_sha, subject):
        # Merge commit with main as the second parent; its tree is chosen by the caller.
        return _git(self.work, "commit-tree", tree_sha, "-p", self.a_sha,
                    "-p", "origin/main", "-m", subject)

    def _set_head(self, sha):
        _git(self.work, "checkout", "-q", "-B", "feature/x", sha)

    def _run_hook_check(self, head_sha):
        base_sha = _git(self.work, "rev-parse", "origin/main")
        stdin = "refs/heads/feature/x %s refs/heads/feature/x %s\n" % (head_sha, base_sha)
        env = _child_env(extra={
            "AESOP_ROOT": str(self.work),
            "TMPDIR": str(self.tmp / "tmpdir"),
            "HOME": str(self.tmp / "home"),
            "GIT_CONFIG_NOSYSTEM": "1",
        })
        script = 'source "$1"; check_symbol_survival'
        # Bytes in: text-mode pipes on Windows rewrite "\n" as "\r\n", which corrupts the sha field.
        res = subprocess.run(
            [_bash(), "-c", script, "bash", HOOK.as_posix()],
            cwd=str(self.work), input=stdin.encode("utf-8"), env=env,
            capture_output=True, timeout=300,
        )
        return _HookResult(res.returncode, res.stdout.decode("utf-8", "replace"),
                           res.stderr.decode("utf-8", "replace"))

    def test_clean_merge_passes(self):
        # Control: the merge keeps alpha, so nothing was dropped and the push must pass.
        tree = _git(self.work, "rev-parse", self.a_sha + "^{tree}")
        head = self._merge(tree, "clean merge keeps alpha")
        self._set_head(head)
        res = self._run_hook_check(head)
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)

    def test_lossy_merge_blocked_by_real_tool(self):
        # Control: the merge drops alpha (taken from origin/main). The real tool must block it.
        tree = _git(self.work, "rev-parse", "origin/main^{tree}")
        head = self._merge(tree, "lossy merge drops alpha")
        self._set_head(head)
        res = self._run_hook_check(head)
        self.assertNotEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertIn("MISSING", res.stdout + res.stderr)

    def test_weakened_pr_tool_cannot_pass_lossy_merge(self):
        # RED before the fix: the PR head weakens the tool to exit 0 and the hook runs the
        # PR-tree copy, so the lossy merge passes. Fixed: the BASE copy judges it and blocks.
        tree = _git(self.work, "rev-parse", "origin/main^{tree}")
        lossy = self._merge(tree, "lossy merge drops alpha")
        self._set_head(lossy)
        _write(self.work, "tools/pr_symbol_survival_check.py", "import sys\nsys.exit(0)\n")
        _git(self.work, "add", "-A")
        _git(self.work, "commit", "-q", "-m", "weaken G13 tool to exit 0")
        head = _git(self.work, "rev-parse", "HEAD")
        res = self._run_hook_check(head)
        self.assertNotEqual(res.returncode, 0, res.stdout + res.stderr)


if __name__ == "__main__":
    unittest.main()
