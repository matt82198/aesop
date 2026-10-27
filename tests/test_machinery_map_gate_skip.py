#!/usr/bin/env python3
"""Pins the machinery-map gate skip rule (tools/check_machinery_map_symbols.py).

Rule under test:
  - the gate SKIPS only when docs/MACHINERY-MAP.md is absent AND the tree is a scaffold;
  - the gate BLOCKS when the map is absent in the aesop repo itself (a deleted map never passes);
  - the gate RUNS the checker when the map is present, scaffold or not.

Scaffold marker: the absence of `bin/cli.js`. bin/cli.js (the scaffolder itself) exists only in
the aesop repository. bin/cli.js line 807-808 documents that docs/MACHINERY-MAP.md is aesop-only
because it cites this repository's own tree (`bin/cli.js` among others), "none of which a scaffold
ships". A scaffolded adopter tree therefore never contains bin/cli.js, while the aesop tree always does.
package.json `"name": "@matt82198/aesop"` was considered and rejected: it is a string an adopter
may rewrite, whereas the scaffold-exclusion comment names bin/cli.js explicitly.

The flag under test is `--skip-if-scaffold` (python checker). Against the current tree the flag is
unknown, so argparse exits 2 with "unrecognized arguments" on stderr. Case (b) asserts the
fail-closed message "machinery map not found" on stderr, so it stays red until the flag exists and
the aesop-tree path falls through to the existing fail-closed exit 2 rather than exiting 0.
"""

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).parent.parent
CHECK = ROOT / "tools" / "check_machinery_map_symbols.py"

FIXTURE_TOOL = (
    "def check_thing():\n"
    "    return 0\n"
)

SKIP_EVENT = "machinery_map_symbols_skipped_scaffold"


def _make_tree(tmp: Path, *, aesop: bool, map_text=None) -> Path:
    """Build a throwaway tree. aesop=True adds bin/cli.js (aesop-only); map_text=None omits the map."""
    (tmp / "tools").mkdir()
    (tmp / "tools" / "fixture.py").write_text(FIXTURE_TOOL, encoding="utf-8", newline="\n")
    if aesop:
        (tmp / "bin").mkdir()
        (tmp / "bin" / "cli.js").write_text("// aesop scaffolder\n", encoding="utf-8", newline="\n")
    if map_text is not None:
        (tmp / "docs").mkdir()
        (tmp / "docs" / "MACHINERY-MAP.md").write_text(map_text, encoding="utf-8", newline="\n")
    return tmp


def _run(root: Path):
    """Run the checker hermetically against root with --skip-if-scaffold."""
    return subprocess.run(
        [sys.executable, str(CHECK), "--check", "--skip-if-scaffold", "--root", str(root)],
        cwd=str(root),
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
    )


class TestMapAbsentScaffoldSkips(unittest.TestCase):
    """(a) map absent + scaffold tree (no bin/cli.js) -> skip, exit 0, skip event logged."""

    def test_map_absent_in_scaffold_tree_skips_with_exit_zero(self):
        with tempfile.TemporaryDirectory() as d:
            root = _make_tree(Path(d), aesop=False, map_text=None)
            r = _run(root)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn(SKIP_EVENT, r.stdout + r.stderr)


class TestMapAbsentAesopTreeBlocks(unittest.TestCase):
    """(b) map absent + aesop repo tree (bin/cli.js present) -> block, non-zero, never a skip."""

    def test_map_absent_in_aesop_tree_blocks_and_never_skips(self):
        with tempfile.TemporaryDirectory() as d:
            root = _make_tree(Path(d), aesop=True, map_text=None)
            r = _run(root)
            self.assertNotEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("machinery map not found", r.stderr)
            self.assertNotIn(SKIP_EVENT, r.stdout + r.stderr)


class TestMapPresentScaffoldRunsChecker(unittest.TestCase):
    """(c) map present + scaffold tree -> the checker runs and its result decides."""

    def test_map_present_in_scaffold_tree_runs_checker_and_fails_on_drift(self):
        # A bare :123 line citation must be reported by the checker, proving it ran (not skipped).
        with tempfile.TemporaryDirectory() as d:
            root = _make_tree(Path(d), aesop=False,
                              map_text="| Gate | tools/fixture.py:123 | Runs. |\n")
            r = _run(root)
            self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
            self.assertIn("line citation :123", r.stdout)
            self.assertNotIn(SKIP_EVENT, r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
