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
        # repo starts with generated artifacts correct and committed. Note: suite counts
        # were removed by PR #830; only gen_tool_index.py remains as a regenerator.
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

    def tool_index_text(self):
        return (self.root / "tools" / "INDEX.md").read_text(encoding="utf-8")


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
        # tool must refuse before any regenerator tries to run.
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


if __name__ == "__main__":
    unittest.main()
