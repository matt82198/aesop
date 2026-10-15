#!/usr/bin/env python3
"""
tests.test_stateapi_lint_tracker_write — F-01 guardrail for tools/stateapi_lint.py.

driver/ is now scanned, and any raw tracker.json write there fails closed, even in a
file that also imports the WriteAPI facade (the facade-import skip must not exempt a
direct write). Each test writes a real module into a temp repo and drives
find_direct_opens() end to end.
"""
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.stateapi_lint import find_direct_opens, SCAN_DIRS  # noqa: E402

RAW_WRITE_MODULE = """
import os
import tempfile
from pathlib import Path
from state_store.write_api import WriteAPI

def revert(tracker_path, data):
    p = Path(tracker_path)
    fd, tmp = tempfile.mkstemp(dir=p.parent, suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(data)
    os.replace(tmp, p)
"""

FACADE_ONLY_MODULE = """
from pathlib import Path
from state_store.write_api import WriteAPI

def mark(tracker_path, item_id):
    api = WriteAPI(Path(tracker_path).parent)
    api.tracker_update_item(item_id, {"status": "in_progress"})
"""

READ_ONLY_MODULE = """
import json
from pathlib import Path

def load(state_dir):
    return json.loads((Path(state_dir) / "tracker.json").read_text(encoding="utf-8"))
"""


class TrackerRawWriteGateTest(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.repo_root = Path(self.tmp)
        (self.repo_root / "driver").mkdir(parents=True)
        (self.repo_root / "tools").mkdir(parents=True)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_driver_is_scanned(self):
        """driver/ is in SCAN_DIRS so the ratchet sees it."""
        self.assertIn("driver", SCAN_DIRS)

    def test_raw_tracker_write_in_driver_is_flagged_despite_facade_import(self):
        """Direct os.replace onto tracker_path fails the gate even with a WriteAPI import."""
        (self.repo_root / "driver" / "bad_writer.py").write_text(RAW_WRITE_MODULE, encoding="utf-8")
        violations = find_direct_opens(str(self.repo_root))
        self.assertIn("driver/bad_writer.py@tracker-json-raw-write", violations)

    def test_facade_only_driver_module_is_clean(self):
        """A driver module that writes only through WriteAPI produces no violation."""
        (self.repo_root / "driver" / "good_writer.py").write_text(FACADE_ONLY_MODULE, encoding="utf-8")
        violations = find_direct_opens(str(self.repo_root))
        self.assertFalse([v for v in violations if v.startswith("driver/good_writer.py")], violations)

    def test_raw_write_outside_gate_dirs_is_not_flagged_by_write_rule(self):
        """The write rule is scoped to driver/; tools/ keeps the existing read-pattern ratchet only."""
        (self.repo_root / "tools" / "other_writer.py").write_text(RAW_WRITE_MODULE, encoding="utf-8")
        violations = find_direct_opens(str(self.repo_root))
        self.assertNotIn("tools/other_writer.py@tracker-json-raw-write", violations)

    def test_driver_read_of_tracker_json_still_flagged(self):
        """Literal-path reads in driver/ are still caught by the read patterns."""
        (self.repo_root / "driver" / "reader.py").write_text(READ_ONLY_MODULE, encoding="utf-8")
        violations = find_direct_opens(str(self.repo_root))
        self.assertIn("driver/reader.py@tracker-json", violations)


if __name__ == "__main__":
    unittest.main()
