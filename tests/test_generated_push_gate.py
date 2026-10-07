#!/usr/bin/env python3
"""Pre-push generated-artifact gate (tools/generated_push_gate.py) contract tests.

The safety net under the regenerating merge driver (tools/generated_merge.py):
whatever produced the bytes -- the driver, git's union, a server-side
update-branch, a hand edit -- the COMMITTED content of every registered
generated path in the push range must equal what its registered generator
produces from that same commit's sources. The existing `check_gen_tool_index()`
runs `gen_tool_index.py --check` against the WORKING TREE, which is why a lane
that regenerated but did not commit, or whose merge commit carried a stale
union, still pushed and failed ci(0) with "generated drift" (#784, #856, #739).
This gate verifies the pushed TIP in a throwaway detached worktree, so it is
the exact CI gate, not a proxy.

Proved failing-first against fixture git repos:
  * a push range whose merge commit carries a union-stale tools/INDEX.md is
    rejected with exactly one instruction line:
      run: python tools/gen_tool_index.py --regenerate && git add tools/INDEX.md
  * a dirty-but-regenerated working tree does NOT rescue a stale commit;
  * after regenerate + commit the same range passes;
  * a range that does not touch a registered path is a no-op, unless the
    driver's `.needs-regen` stamp names one -- then the tip is verified anyway
    and the stamp is consumed on pass;
  * the hook function `check_generated_regen()` is wired: sourced from
    hooks/pre-push-policy.sh and driven with a real pre-push ref tuple, it
    blocks the stale push and passes the repaired one.
"""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
TOOLS = REPO_ROOT / "tools"
GATE = TOOLS / "generated_push_gate.py"
HOOK = REPO_ROOT / "hooks" / "pre-push-policy.sh"
INSTRUCTION = "run: python tools/gen_tool_index.py --regenerate && git add tools/INDEX.md"

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import tools.generated_merge as generated_merge  # noqa: E402

FIXTURE_TOOLS = ("gen_tool_index.py", "generated_paths.py", "generated_merge.py",
                 "generated_push_gate.py")


def _find_bash():
    """Locate a usable bash (Git Bash on Windows, never the WSL launcher)."""
    if os.name != "nt":
        return shutil.which("bash")
    git_exe = shutil.which("git")
    if git_exe:
        git_root = Path(git_exe).resolve().parent.parent
        for candidate in (git_root / "bin" / "bash.exe",
                          git_root / "usr" / "bin" / "bash.exe"):
            if candidate.exists():
                return str(candidate)
    path_bash = shutil.which("bash")
    if path_bash and "system32" not in path_bash.lower():
        return path_bash
    return None


BASH = _find_bash()


def git(repo, *args):
    return subprocess.run(  # subprocess-ok
        ["git"] + list(args), cwd=str(repo), capture_output=True,
        text=True, encoding="utf-8", errors="replace", timeout=120)


def py(repo, *args):
    return subprocess.run(  # subprocess-ok
        [sys.executable] + list(args), cwd=str(repo), capture_output=True,
        text=True, encoding="utf-8", errors="replace", timeout=180)


def add_tool(repo, name, purpose):
    # Stage it: the generator indexes `git ls-files tools/`, exactly as a lane
    # does (add the tool, then regenerate), so an unstaged tool is invisible.
    (repo / "tools" / name).write_text(
        '"""%s.\nINDEX: %s\n"""\n' % (name, purpose), encoding="utf-8", newline="\n")
    git(repo, "add", "--", "tools/%s" % name)


def regenerate(repo):
    proc = py(repo, str(repo / "tools" / "gen_tool_index.py"), "--regenerate")
    assert proc.returncode == 0, proc.stdout + proc.stderr


def stamp_path(repo):
    raw = git(repo, "rev-parse", "--git-path", generated_merge.STAMP_NAME).stdout.strip()
    path = Path(raw)
    return path if path.is_absolute() else repo / path


class GateFixture(unittest.TestCase):
    """main adds gamma; lane adds beta; lane merges main under `union` -> stale."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.repo = self.tmp / "repo"
        (self.repo / "tools").mkdir(parents=True)
        for name in FIXTURE_TOOLS:
            shutil.copy(TOOLS / name, self.repo / "tools" / name)
        git(self.repo, "init", "-q", "-b", "main")
        git(self.repo, "config", "user.email", "test@example.com")
        git(self.repo, "config", "user.name", "Test User")
        git(self.repo, "config", "core.autocrlf", "false")
        git(self.repo, "add", "-A")  # the generator indexes tracked files only
        (self.repo / ".gitattributes").write_text(
            "tools/INDEX.md merge=union\n", encoding="utf-8", newline="\n")
        add_tool(self.repo, "alpha.py", "alpha purpose")
        regenerate(self.repo)
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "base")
        self.base = git(self.repo, "rev-parse", "HEAD").stdout.strip()

        # The lane's tool must sort AFTER main's: git's union keeps ours (the
        # lane) then theirs (main) for the one conflicting hunk, so lane=gamma
        # + main=beta yields "gamma, beta" -- the unsorted, stale shape of the
        # 2026-10-06 incidents. (lane=beta + main=gamma would union into sorted
        # order by luck and prove nothing.)
        git(self.repo, "checkout", "-q", "-b", "feature/lane")
        add_tool(self.repo, "gamma.py", "gamma purpose")
        regenerate(self.repo)
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "lane adds gamma")
        self.lane_before_merge = git(self.repo, "rev-parse", "HEAD").stdout.strip()

        git(self.repo, "checkout", "-q", "main")
        add_tool(self.repo, "beta.py", "beta purpose")
        regenerate(self.repo)
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "main adds beta")
        self.main = git(self.repo, "rev-parse", "HEAD").stdout.strip()

        git(self.repo, "checkout", "-q", "feature/lane")
        merge = git(self.repo, "merge", "--no-edit", "main")
        assert merge.returncode == 0, merge.stdout + merge.stderr
        self.stale_tip = git(self.repo, "rev-parse", "HEAD").stdout.strip()
        # Precondition: the union really did leave the committed index stale.
        proc = py(self.repo, str(self.repo / "tools" / "gen_tool_index.py"), "--check")
        assert proc.returncode == 1, "fixture must reproduce the drift: " + proc.stderr

    def tearDown(self):
        git(self.repo, "worktree", "prune")
        self._tmp.cleanup()

    def gate(self, *ranges):
        args = [str(GATE)]
        for r in ranges:
            args += ["--range", r]
        return py(self.repo, *args)

    def repair(self):
        regenerate(self.repo)
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "regenerate index")
        return git(self.repo, "rev-parse", "HEAD").stdout.strip()

    def leaked_worktrees(self):
        out = git(self.repo, "worktree", "list", "--porcelain").stdout
        return [l for l in out.splitlines() if l.startswith("worktree ")][1:]


class TestGateTool(GateFixture):
    def test_stale_committed_index_in_range_is_rejected_with_one_instruction(self):
        proc = self.gate("%s..%s" % (self.main, self.stale_tip))
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        self.assertIn("tools/INDEX.md", proc.stderr)
        self.assertIn(INSTRUCTION, proc.stderr)
        self.assertEqual(self.leaked_worktrees(), [], "temp worktree must be removed")

    def test_regenerated_working_tree_does_not_rescue_a_stale_commit(self):
        # This is the exact gap in check_gen_tool_index(): tree green, commit stale.
        regenerate(self.repo)
        proc = py(self.repo, str(self.repo / "tools" / "gen_tool_index.py"), "--check")
        self.assertEqual(proc.returncode, 0, "working tree is green")
        proc = self.gate("%s..%s" % (self.main, self.stale_tip))
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        self.assertIn(INSTRUCTION, proc.stderr)

    def test_passes_after_regenerate_and_commit(self):
        tip = self.repair()
        proc = self.gate("%s..%s" % (self.main, tip))
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(self.leaked_worktrees(), [])

    def test_range_not_touching_a_registered_path_is_a_noop(self):
        proc = self.gate("%s..%s" % (self.base, self.lane_before_merge))
        # gamma.py and the regenerated index are both in this range, so it IS
        # verified -- and it is correct. The genuinely untouched range is the
        # empty one (tip..tip).
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        proc = self.gate("%s..%s" % (self.stale_tip, self.stale_tip))
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertNotIn("stale", proc.stdout + proc.stderr)

    def test_stamp_forces_verification_and_is_consumed_on_pass(self):
        stamp = stamp_path(self.repo)
        stamp.parent.mkdir(parents=True, exist_ok=True)
        stamp.write_text("tools/INDEX.md\n", encoding="utf-8", newline="\n")
        # An empty range would otherwise be a no-op; the stamp forces a check.
        proc = self.gate("%s..%s" % (self.stale_tip, self.stale_tip))
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        self.assertIn(INSTRUCTION, proc.stderr)
        self.assertTrue(stamp.exists(), "stamp survives a failed verification")
        tip = self.repair()
        proc = self.gate("%s..%s" % (tip, tip))
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertFalse(stamp.exists(), "stamp is consumed once the tip verifies")

    def test_bad_range_is_a_usage_error(self):
        proc = self.gate("not-a-range")
        self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)


@unittest.skipIf(BASH is None, "usable bash unavailable")
class TestPrePushWiring(GateFixture):
    """Source hooks/pre-push-policy.sh and drive check_generated_regen() for real."""

    def run_hook_fn(self, base, head):
        driver = self.tmp / "drive.sh"
        driver.write_text(
            '#!/usr/bin/env bash\n'
            '. "%s"\n'
            'check_generated_regen\n'
            'exit $?\n' % HOOK.as_posix(), encoding="utf-8", newline="\n")
        env = dict(os.environ)
        env["AESOP_ROOT"] = REPO_ROOT.as_posix()
        stdin_path = self.tmp / "prepush-stdin.txt"
        stdin_path.write_bytes(
            ("refs/heads/feature/lane %s refs/heads/feature/lane %s\n" % (head, base)).encode("utf-8"))
        with open(stdin_path, "rb") as handle:
            return subprocess.run(  # subprocess-ok
                [BASH, str(driver)], cwd=str(self.repo), stdin=handle,
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=180, env=env)

    def test_hook_blocks_stale_push_and_passes_repaired_one(self):
        res = self.run_hook_fn(self.main, self.stale_tip)
        self.assertEqual(res.returncode, 1, res.stdout + res.stderr)
        self.assertIn(INSTRUCTION, res.stderr)
        tip = self.repair()
        res = self.run_hook_fn(self.main, tip)
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)

    def test_hook_calls_the_gate_in_main_flow(self):
        text = HOOK.read_text(encoding="utf-8")
        self.assertIn("check_generated_regen() {", text)
        self.assertIn("if ! check_generated_regen <<< \"$prepush_stdin\"; then", text)


if __name__ == "__main__":
    unittest.main()
