#!/usr/bin/env python3
"""
Regression (R-AUDIT-TCI-4, 2026-10-26): a fresh scaffold ships no test files.

Scaffolds do not ship tests, the same as tests/. The hooks/ copy step in
bin/cli.js must skip *.test.mjs at any depth, because a scaffold has no
package.json and no test:node runner, so shipped hook tests are orphans that
block the adopter's pre-push gate.
"""
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


class TestScaffoldShipsNoTests(unittest.TestCase):
    """A fresh scaffold's hooks/ tree holds no *.test.mjs."""

    def setUp(self):
        self.repo_root = Path(__file__).resolve().parent.parent
        self.cli = self.repo_root / "bin" / "cli.js"

    def test_fresh_scaffold_hooks_hold_no_node_tests(self):
        """Scaffolding a fresh target copies hooks/ without any *.test.mjs."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            home = tmp / "home"
            home.mkdir()
            env = dict(os.environ, HOME=str(home), USERPROFILE=str(home), GIT_TERMINAL_PROMPT="0")
            result = subprocess.run(
                ["node", str(self.cli), "fresh-fleet", "--no-skills"],
                cwd=str(tmp),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=env,
                timeout=240,
            )
            self.assertEqual(
                result.returncode, 0,
                f"scaffold failed:\n{result.stdout}\n{result.stderr}",
            )
            hooks = tmp / "fresh-fleet" / "hooks"
            self.assertTrue(hooks.is_dir(), "scaffold must still ship hooks/")
            shipped = sorted(
                str(p.relative_to(tmp / "fresh-fleet")).replace("\\", "/")
                for p in hooks.rglob("*.test.mjs")
            )
            self.assertEqual(
                shipped, [],
                f"fresh scaffold must not ship hook test files: {shipped}",
            )


if __name__ == "__main__":
    unittest.main()
