"""Tests that ui/config.py expands a leading ~ in aesop.config.json state_root.

Defect (SEM-1, wrong data on the dashboard, P0): a config state_root of
"~/.aesop-state" is not absolute, so the relative-path branch joins it onto
AESOP_ROOT and the dashboard reads AESOP_ROOT/~/.aesop-state, a literal "~"
directory, instead of the user's home state directory.

Contract: a state_root beginning with "~" expands to the user's home directory
(Path.home()) before the absolute/relative decision. A relative state_root that
does not start with "~" keeps its existing meaning: it resolves against AESOP_ROOT.

Run: python -m unittest tests.test_state_root_expanduser
"""
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

from tests.sys_modules_restore import snapshot_sys_modules

# Every env var that config.py resolves paths from, plus the home-directory vars.
# Home is pinned via both HOME (POSIX) and USERPROFILE (Windows) because
# Path.home() on Windows reads USERPROFILE.
_ENV_KEYS = ("HOME", "USERPROFILE", "AESOP_ROOT", "AESOP_STATE_ROOT",
             "AESOP_TRANSCRIPTS_ROOT", "AESOP_CONDUCTOR3_ROOT",
             "AESOP_WATCHDOG_HEARTBEAT", "AESOP_MONITOR_HEARTBEAT", "PORT")


class TestStateRootTildeExpansion(unittest.TestCase):
    """config.STATE_DIR must honour a ~-prefixed state_root from aesop.config.json."""

    def setUp(self):
        self.addCleanup(snapshot_sys_modules("ui.config", "ui"))
        self.fixture_root = tempfile.mkdtemp(prefix="aesop-state-root-test-")
        self.addCleanup(shutil.rmtree, self.fixture_root, True)
        self.home_dir = os.path.join(self.fixture_root, "home")
        os.makedirs(self.home_dir, exist_ok=True)
        self.ui_dir = os.path.join(self.fixture_root, "ui")
        os.makedirs(self.ui_dir, exist_ok=True)

        self._saved_env = {k: os.environ.pop(k, None) for k in _ENV_KEYS}
        self.addCleanup(self._restore_env)
        os.environ["HOME"] = self.home_dir
        os.environ["USERPROFILE"] = self.home_dir

    def _restore_env(self):
        for k, v in self._saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def _write_config(self, state_root):
        with open(os.path.join(self.fixture_root, "aesop.config.json"), "w",
                  encoding="utf-8") as f:
            json.dump({"state_root": state_root}, f)

    def _load_config_module(self):
        """Copy ui/config.py into the fixture as the derived location and import it."""
        actual = Path(__file__).resolve().parent.parent / "ui" / "config.py"
        fixture_cfg = os.path.join(self.ui_dir, "config.py")
        with open(actual, "r", encoding="utf-8") as src, \
                open(fixture_cfg, "w", encoding="utf-8") as dst:
            dst.write(src.read())

        for name in ("ui.config", "ui"):
            sys.modules.pop(name, None)
        spec = importlib.util.spec_from_file_location("ui.config", fixture_cfg)
        module = importlib.util.module_from_spec(spec)
        sys.modules["ui.config"] = module
        spec.loader.exec_module(module)
        return module

    def test_tilde_state_root_resolves_under_home(self):
        """state_root "~/.aesop-state" must resolve to Path.home()/.aesop-state."""
        self._write_config("~/.aesop-state")
        os.environ["AESOP_ROOT"] = self.fixture_root

        config = self._load_config_module()

        self.assertEqual(config.STATE_DIR, Path.home() / ".aesop-state")
        self.assertEqual(config.STATE_DIR, Path(self.home_dir) / ".aesop-state")

    def test_relative_state_root_still_joins_aesop_root(self):
        """Control: a non-~ relative state_root keeps resolving against AESOP_ROOT.

        Guards against a naive fix that resolves every relative path against home.
        """
        self._write_config("custom-state")
        os.environ["AESOP_ROOT"] = self.fixture_root

        config = self._load_config_module()

        self.assertEqual(config.STATE_DIR, Path(self.fixture_root) / "custom-state")


if __name__ == "__main__":
    unittest.main()
