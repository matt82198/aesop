"""State-root parity: config state_root must be honoured by tools.common.get_state_dir().

Before this fix tools/common.get_state_dir() ignored aesop.config.json state_root, while
ui/config.py honoured it -- so the dashboard and the tools modules could read two
different state directories. Each test isolates AESOP_ROOT / AESOP_STATE_ROOT to a fixture.

Run: python -m unittest tests.test_state_dir_parity
"""
import importlib
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
UI_PATH = REPO_ROOT / "ui"

from tools import common  # noqa: E402


class StateDirParityCase(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="aesop-state-parity-"))
        self._saved = {k: os.environ.get(k) for k in ("AESOP_ROOT", "AESOP_STATE_ROOT")}
        os.environ.pop("AESOP_STATE_ROOT", None)
        os.environ["AESOP_ROOT"] = str(self.root)

    def tearDown(self):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(self.root, ignore_errors=True)

    def _write_config(self, state_root):
        (self.root / "aesop.config.json").write_text(
            json.dumps({"state_root": state_root}), encoding="utf-8")

    def test_config_state_root_is_honoured_when_env_unset(self):
        custom = self.root / "custom-state"
        self._write_config(str(custom))
        self.assertEqual(common.get_state_dir(), custom)

    def test_relative_config_state_root_resolves_against_config_dir(self):
        self._write_config("custom-state")
        self.assertEqual(common.get_state_dir(), self.root / "custom-state")

    def test_env_still_wins_over_config(self):
        self._write_config(str(self.root / "from-config"))
        os.environ["AESOP_STATE_ROOT"] = str(self.root / "from-env")
        self.assertEqual(common.get_state_dir(), self.root / "from-env")

    def test_matches_ui_config_state_dir(self):
        custom = self.root / "custom-state"
        self._write_config(str(custom))
        sys.path.insert(0, str(UI_PATH))
        try:
            config = importlib.import_module("config")
            config.reload()
            self.assertEqual(Path(config.STATE_DIR), common.get_state_dir())
        finally:
            sys.path.remove(str(UI_PATH))


if __name__ == "__main__":
    unittest.main()
