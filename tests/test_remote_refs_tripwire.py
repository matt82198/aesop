#!/usr/bin/env python3
"""Behavioral proof for tools/remote_refs_tripwire.py.

Standalone replacement for PR #831's tools/test_isolation_tripwire.py (not yet
merged at the time this was written -- see tests/CLAUDE.md). Where that tripwire
watches the developer's local ~/.claude profile, this one watches the REAL git
remote (`git ls-remote --heads origin`) and REAL open PR list (`gh pr list`)
before/after a wrapped test command, so a future escape past
tools/test_network_isolation.py's harness-level rewrite still gets caught.

Every test here drives the tripwire's own CLI as a real subprocess against a
local fixture "origin" (a bare repo) and a stub "gh" command (an injectable argv
prefix, never the literal PATH-resolved name -- see tools/test_network_isolation.py's
module docstring for why a real PATH shim for `gh` is not reliable on Windows).
Nothing here ever calls the real `gh` or touches the real origin.
"""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
TRIPWIRE = REPO_ROOT / "tools" / "remote_refs_tripwire.py"


def _git(args, cwd, check=True):
    result = subprocess.run(["git"] + args, cwd=str(cwd), capture_output=True, text=True, timeout=30)
    if check and result.returncode != 0:
        raise AssertionError(f"git {args} failed: {result.stdout}\n{result.stderr}")
    return result


def _make_repo_with_bare_origin(root: Path):
    bare = root / "origin.git"
    bare.mkdir()
    _git(["init", "--quiet", "--bare", str(bare)], cwd=root)
    work = root / "work"
    work.mkdir()
    _git(["init", "-q"], cwd=work)
    _git(["config", "user.email", "t@example.invalid"], cwd=work)
    _git(["config", "user.name", "T"], cwd=work)
    (work / "f.txt").write_text("hi\n", encoding="utf-8")
    _git(["add", "."], cwd=work)
    _git(["commit", "-q", "-m", "init"], cwd=work)
    _git(["remote", "add", "origin", str(bare)], cwd=work)
    _git(["push", "origin", "HEAD:refs/heads/main"], cwd=work)
    return work, bare


class TestTripwireDetectsNewRemoteBranch(unittest.TestCase):
    """The literal incident: a wrapped command pushes a new branch to the
    (fixture-standing-in-for-)real remote. The tripwire must fail and name it."""

    def test_fails_and_names_new_branch_pushed_during_wrapped_command(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            work, bare = _make_repo_with_bare_origin(tmp)

            # The wrapped "test command" simulates the incident: it pushes a new
            # branch straight to the fixture origin, with no isolation at all.
            leak_script = tmp / "leak.py"
            leak_script.write_text(
                "import subprocess, sys\n"
                f"subprocess.run(['git','push','origin','HEAD:refs/heads/integrate/leaked'],"
                f" cwd=r'{work}', check=True)\n"
                "sys.exit(0)\n",
                encoding="utf-8",
            )

            proc = subprocess.run(
                [sys.executable, str(TRIPWIRE), "--repo", str(work), "--",
                 sys.executable, str(leak_script)],
                capture_output=True, text=True, timeout=60,
            )
            self.assertNotEqual(proc.returncode, 0, proc.stderr)
            self.assertIn("integrate/leaked", proc.stderr)
            self.assertIn("FAIL", proc.stderr)

    def test_clean_run_passes_and_propagates_wrapped_exit_code(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            work, bare = _make_repo_with_bare_origin(tmp)

            proc = subprocess.run(
                [sys.executable, str(TRIPWIRE), "--repo", str(work), "--",
                 sys.executable, "-c", "import sys; sys.exit(0)"],
                capture_output=True, text=True, timeout=60,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertNotIn("FAIL", proc.stderr)
            self.assertIn("BEFORE", proc.stderr)
            self.assertIn("AFTER", proc.stderr)

    def test_wrapped_command_red_exit_code_is_never_swallowed(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            work, bare = _make_repo_with_bare_origin(tmp)

            proc = subprocess.run(
                [sys.executable, str(TRIPWIRE), "--repo", str(work), "--",
                 sys.executable, "-c", "import sys; sys.exit(7)"],
                capture_output=True, text=True, timeout=60,
            )
            self.assertEqual(proc.returncode, 7, proc.stderr)


def _env_without_ci():
    """A deterministic 'developer box' environment for the CLI subprocess, even when
    this test itself happens to be running inside real GitHub Actions (which already
    sets GITHUB_ACTIONS=true for the whole job and would otherwise leak into the
    child via plain env inheritance)."""
    env = dict(os.environ)
    for k in ("GITHUB_ACTIONS", "GITHUB_HEAD_REF", "GITHUB_SHA", "GITHUB_WORKFLOW"):
        env.pop(k, None)
    return env


def _env_with_ci():
    """A deterministic 'CI' environment: GITHUB_ACTIONS=true, nothing else -- proves
    the implicit (no --strict/--attributed flag) mode resolution, not just the flag."""
    env = _env_without_ci()
    env["GITHUB_ACTIONS"] = "true"
    return env


class TestAttributedVerdictMode(unittest.TestCase):
    """Red-first proof for the PR #829/#837 false-positive fix: CI wires the tripwire
    around the test shards, and #829's run failed only because an UNRELATED branch
    moved while other lanes in this fleet pushed concurrently -- nothing in the
    wrapped shard ever touched the remote. Attributed mode (CI default) must not fail
    on that; strict mode (developer-box default) must keep failing on it exactly as
    before."""

    def _push_second_branch(self, work, name):
        _git(["push", "origin", f"HEAD:refs/heads/{name}"], cwd=work)

    def _write_mover_script(self, tmp, work, branch):
        script = tmp / "mover.py"
        script.write_text(
            "import subprocess\n"
            f"subprocess.run(['git', 'commit', '--allow-empty', '-q', '-m', 'unrelated'],"
            f" cwd=r'{work}', check=True)\n"
            f"subprocess.run(['git', 'push', '--force', 'origin', 'HEAD:refs/heads/{branch}'],"
            f" cwd=r'{work}', check=True)\n",
            encoding="utf-8",
        )
        return script

    def test_a_moved_unrelated_branch_in_ci_mode_exits_0_and_is_observed(self):
        """(a) moved unrelated branch in CI mode -> exit 0 with the observed line."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            work, bare = _make_repo_with_bare_origin(tmp)
            self._push_second_branch(work, "other")
            mover = self._write_mover_script(tmp, work, "other")

            proc = subprocess.run(
                [sys.executable, str(TRIPWIRE), "--repo", str(work), "--",
                 sys.executable, str(mover)],
                capture_output=True, text=True, timeout=60, env=_env_with_ci(),
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn("observed, not attributed", proc.stderr)
            self.assertIn("other", proc.stderr)
            self.assertNotIn("FAIL", proc.stderr)

    def test_b_new_integrate_batch_branch_fails_naming_it_in_both_modes(self):
        """(b) new integrate/batch-... branch -> fail naming it, in BOTH modes."""
        for env, label in (
            (_env_without_ci(), "strict (local default)"),
            (_env_with_ci(), "attributed (CI default)"),
        ):
            with tempfile.TemporaryDirectory() as tmp:
                tmp = Path(tmp)
                work, bare = _make_repo_with_bare_origin(tmp)
                leak_script = tmp / "leak.py"
                leak_script.write_text(
                    "import subprocess\n"
                    "subprocess.run(['git', 'push', 'origin',"
                    " 'HEAD:refs/heads/integrate/batch-20261005-1500'],"
                    f" cwd=r'{work}', check=True)\n",
                    encoding="utf-8",
                )
                proc = subprocess.run(
                    [sys.executable, str(TRIPWIRE), "--repo", str(work), "--",
                     sys.executable, str(leak_script)],
                    capture_output=True, text=True, timeout=60, env=env,
                )
                self.assertNotEqual(proc.returncode, 0, f"{label}: {proc.stderr}")
                self.assertIn("integrate/batch-20261005-1500", proc.stderr, label)
                self.assertIn("FAIL", proc.stderr, label)

    def test_c_local_strict_mode_still_fails_on_any_moved_branch(self):
        """(c) local strict mode still fails on any moved branch, even one with no
        leak-shaped name (the exact opposite of (a), proving the mode -- not just
        the branch name -- is what changed)."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            work, bare = _make_repo_with_bare_origin(tmp)
            self._push_second_branch(work, "other")
            mover = self._write_mover_script(tmp, work, "other")

            proc = subprocess.run(
                [sys.executable, str(TRIPWIRE), "--repo", str(work), "--",
                 sys.executable, str(mover)],
                capture_output=True, text=True, timeout=60, env=_env_without_ci(),
            )
            self.assertNotEqual(proc.returncode, 0, proc.stderr)
            self.assertIn("other", proc.stderr)
            self.assertIn("FAIL", proc.stderr)

    def test_explicit_attributed_flag_overrides_ambient_env(self):
        """--attributed forces the lenient verdict even without GITHUB_ACTIONS set."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            work, bare = _make_repo_with_bare_origin(tmp)
            self._push_second_branch(work, "other")
            mover = self._write_mover_script(tmp, work, "other")

            proc = subprocess.run(
                [sys.executable, str(TRIPWIRE), "--repo", str(work), "--attributed", "--",
                 sys.executable, str(mover)],
                capture_output=True, text=True, timeout=60, env=_env_without_ci(),
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn("observed, not attributed", proc.stderr)

    def test_strict_and_attributed_together_is_a_usage_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            work, bare = _make_repo_with_bare_origin(tmp)
            proc = subprocess.run(
                [sys.executable, str(TRIPWIRE), "--repo", str(work), "--strict", "--attributed", "--",
                 sys.executable, "-c", "pass"],
                capture_output=True, text=True, timeout=60, env=_env_without_ci(),
            )
            self.assertEqual(proc.returncode, 2, proc.stderr)


class TestTripwireDegradesLoudlyWhenNoRemote(unittest.TestCase):
    def test_inert_but_green_when_origin_unreachable(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            work = tmp / "work"
            work.mkdir()
            _git(["init", "-q"], cwd=work)
            # No "origin" remote configured at all -> ls-remote must fail fast.
            proc = subprocess.run(
                [sys.executable, str(TRIPWIRE), "--repo", str(work), "--",
                 sys.executable, "-c", "pass"],
                capture_output=True, text=True, timeout=60,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn("inert", proc.stderr)


class TestSnapshotFunctions(unittest.TestCase):
    """Unit-level proof of the comparison logic, independent of the CLI."""

    def setUp(self):
        sys.path.insert(0, str(REPO_ROOT / "tools"))
        import remote_refs_tripwire as mod
        self.mod = mod

    def test_diff_branches_detects_new_and_moved_refs(self):
        before = {"refs/heads/main": "aaa"}
        after = {"refs/heads/main": "aaa", "refs/heads/leaked": "bbb"}
        findings = self.mod.diff_branches(before, after)
        self.assertEqual(len(findings), 1)
        self.assertIn("leaked", findings[0])

        after_moved = {"refs/heads/main": "ccc"}
        findings2 = self.mod.diff_branches(before, after_moved)
        self.assertEqual(len(findings2), 1)
        self.assertIn("main", findings2[0])

    def test_diff_branches_clean_is_empty(self):
        before = {"refs/heads/main": "aaa"}
        self.assertEqual(self.mod.diff_branches(before, dict(before)), [])

    def test_diff_branches_excludes_backup_wip_ref_but_not_others(self):
        """The repo's own daemons/backup-fleet.sh force-pushes
        refs/heads/backup/wip-YYYYMMDD on an independent ~150s cycle; the
        tripwire must not flag that specific ref moving OR appearing, while
        still catching every other new-or-moved ref (the real incident
        pattern, e.g. integrate/batch-*) exactly as before."""
        before = {
            "refs/heads/main": "aaa",
            "refs/heads/backup/wip-20261005": "bbb",
        }
        # The daemon force-pushes its own ref (moved) and a real leak also
        # lands in the same window (new, unrelated ref).
        after = {
            "refs/heads/main": "aaa",
            "refs/heads/backup/wip-20261005": "ccc",
            "refs/heads/integrate/leaked": "ddd",
        }
        findings = self.mod.diff_branches(before, after)
        self.assertEqual(len(findings), 1, findings)
        self.assertIn("integrate/leaked", findings[0])
        self.assertFalse(any("backup/wip" in f for f in findings), findings)

        # A brand-new day's backup ref appearing mid-run must also be exempt.
        after_new_day = dict(before)
        after_new_day["refs/heads/backup/wip-20261006"] = "eee"
        self.assertEqual(self.mod.diff_branches(before, after_new_day), [])

        # (d) The exclusion holds in attributed mode too: the daemon's own ref is
        # never flagged (as new-day or moved), while integrate/leaked -- a plausible
        # test-created name -- still fails even here.
        findings_attributed = self.mod.diff_branches(before, after, mode="attributed")
        self.assertEqual(len(findings_attributed), 1, findings_attributed)
        self.assertIn("integrate/leaked", findings_attributed[0])
        self.assertFalse(any("backup/wip" in f for f in findings_attributed), findings_attributed)
        self.assertEqual(
            self.mod.diff_branches(before, after_new_day, mode="attributed"), []
        )

    def test_diff_prs_detects_new_pr(self):
        findings = self.mod.diff_prs([1, 2], [1, 2, 3])
        self.assertEqual(len(findings), 1)
        self.assertIn("3", findings[0])

    def test_diff_is_noop_when_either_side_is_inert(self):
        self.assertEqual(self.mod.diff_branches(None, {"refs/heads/main": "a"}), [])
        self.assertEqual(self.mod.diff_prs(None, [1]), [])


if __name__ == "__main__":
    unittest.main()
