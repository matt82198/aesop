#!/usr/bin/env python3
"""Tests for tools/regen_all.py -- the single entry point `regen-on-main.yml` (and a
human) calls to detect and repair drift in registered generated artifacts.

Hermetic: every fixture is a throwaway temp git repo with its OWN copies of the real
`tools/gen_suite_counts.py` and `tools/gen_tool_index.py` (the two scripts
`tools/merge_queue.py::REGENERATORS` names), a per-invocation git identity (never the
global config), and the process cwd is never changed. `tools/regen_all.py` itself is
invoked as a real subprocess against `--repo <fixture>` exactly as `regen-on-main.yml`
and a human shell would.
"""

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
TOOLS = REPO_ROOT / "tools"
REGEN_ALL = TOOLS / "regen_all.py"
GEN_SUITE_COUNTS = TOOLS / "gen_suite_counts.py"
GEN_TOOL_INDEX = TOOLS / "gen_tool_index.py"


def _run(argv, cwd):
    """Run a python tool with an explicit cwd and utf-8 decoding."""
    return subprocess.run(
        [sys.executable] + [str(a) for a in argv],
        cwd=str(cwd), capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=120)


def _git(args, cwd):
    """git with identity forced per-invocation: never touches global config."""
    return subprocess.run(
        ["git", "-c", "user.email=t@example.invalid", "-c", "user.name=t"] + args,
        cwd=str(cwd), capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=120)


class RegenAllFixture(unittest.TestCase):
    """A throwaway git repo with real copies of both registered regenerators and a
    minimal tests/ tree carrying one file per suite family (Node/Shell/Python), so
    `gen_suite_counts.py`'s fail-closed vacuous-zero guard never fires."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="aesop-regenall-")
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

        (self.root / "tools").mkdir()
        (self.root / "tests").mkdir()

        (self.root / "tools" / "gen_suite_counts.py").write_text(
            GEN_SUITE_COUNTS.read_text(encoding="utf-8"), encoding="utf-8", newline="\n")
        (self.root / "tools" / "gen_tool_index.py").write_text(
            GEN_TOOL_INDEX.read_text(encoding="utf-8"), encoding="utf-8", newline="\n")

        (self.root / "tests" / "dummy.test.mjs").write_text(
            "// dummy node suite\n", encoding="utf-8", newline="\n")
        (self.root / "tests" / "test_dummy.sh").write_text(
            "#!/bin/sh\n# dummy shell suite\n", encoding="utf-8", newline="\n")
        (self.root / "tests" / "test_dummy.py").write_text(
            '"""dummy python suite."""\n', encoding="utf-8", newline="\n")

        self.assertEqual(_git(["init", "-q"], self.root).returncode, 0)
        self.assertEqual(_git(["add", "-A"], self.root).returncode, 0)

        # Baseline regeneration (direct, not through regen_all.py under test) so the
        # repo starts with BOTH generated artifacts correct and committed.
        self.assertEqual(self.regenerate_suite_counts().returncode, 0)
        self.assertEqual(self.regenerate_tool_index().returncode, 0)
        self.assertEqual(_git(["add", "-A"], self.root).returncode, 0)
        self.assertEqual(
            _git(["commit", "-m", "baseline"], self.root).returncode, 0)

    # -- helpers ------------------------------------------------------------

    def add_python_test(self, name="test_extra.py", body='"""extra."""\n'):
        """Add and commit ONE more tracked Python test file WITHOUT touching
        tests/SUITE-COUNTS.json -- the exact shape of the PR #706/#709 incident:
        a change that is individually correct lands without the whole-tree
        artifact being re-derived afterward."""
        (self.root / "tests" / name).write_text(body, encoding="utf-8", newline="\n")
        self.assertEqual(_git(["add", "--", "tests/%s" % name], self.root).returncode, 0)
        self.assertEqual(
            _git(["commit", "-m", "add %s" % name], self.root).returncode, 0)

    def regenerate_suite_counts(self):
        return _run([self.root / "tools" / "gen_suite_counts.py", "--regenerate"],
                    self.root)

    def regenerate_tool_index(self):
        return _run([self.root / "tools" / "gen_tool_index.py", "--regenerate",
                     "--root", self.root],
                    self.root)

    def regen_all(self, *flags):
        return _run([REGEN_ALL, "--repo", self.root] + list(flags), self.root)

    def git_status(self):
        proc = _git(["status", "--porcelain"], self.root)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        return proc.stdout

    def suite_counts_text(self):
        return (self.root / "tests" / "SUITE-COUNTS.json").read_text(encoding="utf-8")


class TestCleanTreeExitsZero(RegenAllFixture):
    """(b) Nothing drifted: both --check and --fix are no-ops and exit 0."""

    def test_check_on_clean_tree_exits_zero(self):
        self.assertEqual(self.git_status().strip(), "", "fixture must start clean")
        proc = self.regen_all("--check")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("in sync", proc.stdout)
        self.assertEqual(self.git_status().strip(), "", "a clean check must not write")

    def test_fix_on_clean_tree_exits_zero_and_writes_nothing(self):
        proc = self.regen_all("--fix")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(self.git_status().strip(), "")

    def test_json_mode_on_clean_tree(self):
        proc = self.regen_all("--check", "--json")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        import json
        payload = json.loads(proc.stdout)
        self.assertEqual(payload["exit_code"], 0)
        self.assertNotIn("drifted", payload)


class TestDriftedSuiteCountsDetectedAndFixed(RegenAllFixture):
    """(a) The real incident shape: a committed test file add with no matching
    SUITE-COUNTS.json bump. --check must report it and leave the tree clean;
    --fix must repair it and leave the fix uncommitted for the caller."""

    def setUp(self):
        super().setUp()
        self.add_python_test()
        self.assertEqual(self.git_status().strip(), "",
                          "both commits must leave a clean tree; drift is committed, "
                          "not working-tree-dirty -- exactly like a clean merge")

    def test_check_reports_drift_and_restores_the_tree(self):
        proc = self.regen_all("--check")
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        self.assertIn("tests/SUITE-COUNTS.json", proc.stdout + proc.stderr)
        self.assertEqual(self.git_status().strip(), "",
                          "--check is read-only: it must restore what it wrote")

    def test_fix_repairs_the_drift_and_leaves_it_uncommitted(self):
        before = self.suite_counts_text()
        proc = self.regen_all("--fix")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("tests/SUITE-COUNTS.json", proc.stdout)
        after = self.suite_counts_text()
        self.assertNotEqual(before, after, "the file must actually be rewritten")
        self.assertIn('"Python": 2', after)
        status = self.git_status()
        self.assertIn("tests/SUITE-COUNTS.json", status,
                       "--fix leaves the regenerated bytes UNCOMMITTED for the caller")

    def test_a_second_check_after_fix_is_clean(self):
        """Watched-to-fail, the other direction: fix, commit, re-check is clean."""
        fix_proc = self.regen_all("--fix")
        self.assertEqual(fix_proc.returncode, 0)
        self.assertEqual(
            _git(["commit", "-am", "chore(regen): heal"], self.root).returncode, 0)
        check_proc = self.regen_all("--check")
        self.assertEqual(check_proc.returncode, 0, check_proc.stdout + check_proc.stderr)
        self.assertEqual(self.git_status().strip(), "")


class TestRefusesUnregisteredPaths(RegenAllFixture):
    """(c) A dirty path outside the GENERATED_PATHS registry must stop the tool
    before it runs a single regenerator -- it must never be touched, restored,
    or silently discarded."""

    def test_refuses_when_an_unregistered_path_is_already_dirty(self):
        # Placed at repo ROOT, outside both tools/ and tests/, so neither
        # registered regenerator's own scan is perturbed by it -- this test
        # targets ONLY the preexisting-dirty refusal, not an incidental
        # "a regenerator failed" exit via gen_tool_index.py tripping over an
        # undocumented tools/*.py file.
        rogue = self.root / "ROGUE.md"
        rogue.write_text("not a generated path\n", encoding="utf-8", newline="\n")
        self.assertEqual(
            _git(["add", "--", "ROGUE.md"], self.root).returncode, 0)
        # Staged-but-uncommitted dirt (a WIP add), not a committed file -- the
        # tool must refuse before the (uncommitted) tests/SUITE-COUNTS.json drift
        # it also carries from setUp's baseline is ever touched.
        before_status = self.git_status()
        self.assertIn("ROGUE.md", before_status)

        proc = self.regen_all("--check")
        self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
        self.assertIn("ROGUE.md", (proc.stdout + proc.stderr))

        # Nothing moved: the rogue file is untouched and still present.
        self.assertTrue(rogue.exists())
        self.assertEqual(rogue.read_text(encoding="utf-8"), "not a generated path\n")
        self.assertEqual(self.git_status(), before_status,
                          "a refusal must leave the working tree byte-for-byte as found")

    def test_fix_also_refuses_on_an_unregistered_dirty_path(self):
        (self.root / "ROGUE2.md").write_text(
            "also not generated\n", encoding="utf-8", newline="\n")
        proc = self.regen_all("--fix")
        self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)


class TestOverreachIsRestored(RegenAllFixture):
    """A regenerator that writes a path outside its registry entry must never
    have that write committed or left behind -- same invariant
    merge_queue.regenerate_on_batch enforces via `regenerator_overreach`."""

    def test_a_generator_writing_outside_the_registry_is_restored_and_refused(self):
        # Simulate overreach by swapping in a "generator" that writes an
        # unregistered file in addition to doing nothing useful. This exercises
        # the real overreach path in tools/regen_all.py, not a mock of it.
        script = self.root / "tools" / "gen_suite_counts.py"
        original = script.read_text(encoding="utf-8")
        poisoned = original.replace(
            "def main():",
            "def _poison():\n"
            "    from pathlib import Path\n"
            "    Path('UNREGISTERED-SIDE-EFFECT.txt').write_text('x')\n\n"
            "def main():\n"
            "    _poison()",
            1,
        )
        self.assertNotEqual(original, poisoned, "patch point not found")
        script.write_text(poisoned, encoding="utf-8", newline="\n")
        # Commit the poison so the tree is clean BEFORE regen_all runs -- the
        # overreach this test targets is a regenerator writing an unregistered
        # path DURING the run, not a pre-existing dirty source file (that is
        # TestRefusesUnregisteredPaths, a different refusal path).
        self.assertEqual(
            _git(["commit", "-am", "poison"], self.root).returncode, 0)
        self.assertEqual(self.git_status().strip(), "")

        proc = self.regen_all("--check")
        self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
        self.assertIn("overreach", (proc.stdout + proc.stderr).lower())
        self.assertFalse((self.root / "UNREGISTERED-SIDE-EFFECT.txt").exists(),
                          "the overreach file must be removed, never left behind")


if __name__ == "__main__":
    unittest.main()
