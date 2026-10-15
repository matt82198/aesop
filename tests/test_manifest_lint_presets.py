#!/usr/bin/env python3
"""Every shipped wave preset must instantiate and pass tools/wave_manifest_lint.py.

Regression guard for the 2026-10-14 F-11 finding: the library preset FAILed
testcmd_validity on the shell builtin ``cd``. Each preset is instantiated the
same way a user would, then linted with the real check functions. Third-party
binaries the presets name (docker-compose, dbt, sphinx-build) are stubbed on
PATH only when the host lacks them, so the verdict does not depend on which
tools this machine happens to have installed.

Controls: a builtin followed by a genuinely missing binary must still FAIL,
so recognising builtins does not turn the linter into a no-op.
"""

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
TOOLS_DIR = REPO / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import wave_manifest_lint as lint  # noqa: E402
import wave_templates  # noqa: E402

PRESET_NAMES = sorted(p.stem for p in wave_templates.PRESETS_DIR.glob("*.json"))


def _leading_binaries(preset):
    """Every leading binary of every item testCmd, after lint's own resolver."""
    names = set()
    for item in preset.get("items", []):
        cmd = (item.get("testCmd") or "").strip()
        if not cmd:
            continue
        _, binary = lint._testcmd_binary_resolves(cmd, str(REPO))
        names.add(binary)
    return names


class TestShippedPresetsLint(unittest.TestCase):
    """Each shipped preset, instantiated, must lint with zero FAIL checks."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.bin_dir = Path(self._tmp.name) / "stub-bin"
        self.bin_dir.mkdir()

    def tearDown(self):
        self._tmp.cleanup()

    def _stub_missing_binaries(self, names):
        """Put a no-op stub on PATH for each non-builtin name the host lacks."""
        for name in names:
            if not name or shutil.which(name) or name == "cd":
                continue
            posix = self.bin_dir / name
            posix.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8", newline="\n")
            os.chmod(posix, 0o755)
            (self.bin_dir / f"{name}.cmd").write_text(
                "@echo off\r\nexit /b 0\r\n", encoding="utf-8")
        return str(self.bin_dir) + os.pathsep + os.environ.get("PATH", "")

    def _lint(self, preset_name, path_value):
        preset = wave_templates.load_preset(preset_name)
        manifest = wave_templates.instantiate_template(
            preset, project_name="demo", base_dir=str(self._tmp.name))
        with mock.patch.dict(os.environ, {"PATH": path_value}):
            checks, _ = lint.run_checks(manifest, str(REPO))
        return checks

    def test_every_shipped_preset_has_no_fail_check(self):
        self.assertTrue(PRESET_NAMES, "no presets found under templates/wave-presets")
        for name in PRESET_NAMES:
            with self.subTest(preset=name):
                preset = wave_templates.load_preset(name)
                path_value = self._stub_missing_binaries(_leading_binaries(preset))
                checks = self._lint(name, path_value)
                fails = [(c.name, c.message) for c in checks if c.level == "FAIL"]
                self.assertEqual(fails, [], f"{name} preset fails lint: {fails}")

    def test_builtin_cd_is_not_reported_as_missing_binary(self):
        manifest = {"items": [{
            "slug": "docs",
            "testCmd": "cd docs && true",
            "ownsFiles": ["docs/**"],
            "prompt": "[ISOLATION: sibling worktree]",
        }]}
        checks, _ = lint.run_checks(manifest, str(REPO))
        tc = [c for c in checks if c.name == "testcmd_validity"]
        self.assertEqual([c.level for c in tc if c.level == "FAIL"], [],
                         f"builtin cd reported as missing: {[c.message for c in tc]}")

    def test_builtin_prefix_does_not_hide_missing_real_binary(self):
        manifest = {"items": [{
            "slug": "docs",
            "testCmd": "cd docs && no-such-binary-aesop-f11-control --x",
            "ownsFiles": ["docs/**"],
            "prompt": "[ISOLATION: sibling worktree]",
        }]}
        checks, exit_code = lint.run_checks(manifest, str(REPO))
        tc = [c for c in checks if c.name == "testcmd_validity" and c.level == "FAIL"]
        self.assertEqual(len(tc), 1, "a missing real binary behind cd must still FAIL")
        self.assertEqual(exit_code, 1)


if __name__ == "__main__":
    unittest.main()
