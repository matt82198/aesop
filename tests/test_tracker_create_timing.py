"""
Stage-level slow-path diagnostics for ui.collectors.create_tracker_item.

A tracker create that exceeds AESOP_TRACKER_SLOW_SECONDS must emit ONE stderr
line naming total/migrate/append/render and the state dir. Below the threshold
nothing is emitted.
"""
import contextlib
import io
import os
import re
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
UI_PATH = REPO_ROOT / "ui"
for _p in (str(REPO_ROOT), str(UI_PATH)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import config  # noqa: E402
import collectors  # noqa: E402

SLOW_LINE = re.compile(
    r"^tracker-create slow: total=(\d+\.\d{3}) migrate=(\d+\.\d{3}) "
    r"append=(\d+\.\d{3}) render=(\d+\.\d{3}) state_dir=(.+)$",
    re.MULTILINE,
)
SLOW_ENV_VAR = "AESOP_TRACKER_SLOW_SECONDS"


class TrackerCreateTimingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="aesop-trk-timing-")
        self._saved_env = os.environ.get(SLOW_ENV_VAR)
        self._state_patch = mock.patch.object(config, "STATE_DIR", Path(self.tmp))
        self._state_patch.start()

    def tearDown(self):
        self._state_patch.stop()
        if self._saved_env is None:
            os.environ.pop(SLOW_ENV_VAR, None)
        else:
            os.environ[SLOW_ENV_VAR] = self._saved_env
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _create_captured(self):
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            item = collectors.create_tracker_item({"title": "timing probe"})
        return item, buf.getvalue()

    def test_migrate_stage_over_threshold_emits_one_line_with_all_fields(self):
        os.environ[SLOW_ENV_VAR] = "0.1"
        real_migrate = collectors._ensure_tracker_migrated

        def slow_migrate(write_api):
            time.sleep(0.3)
            return real_migrate(write_api)

        with mock.patch.object(collectors, "_ensure_tracker_migrated", slow_migrate):
            item, err = self._create_captured()

        self.assertEqual(item["title"], "timing probe")
        lines = [ln for ln in err.splitlines() if ln.startswith("tracker-create slow:")]
        self.assertEqual(len(lines), 1, f"expected exactly one slow line, got stderr={err!r}")
        m = SLOW_LINE.match(lines[0])
        self.assertIsNotNone(m, f"line does not match the contract: {lines[0]!r}")
        total, migrate, append, render = (float(m.group(i)) for i in range(1, 5))
        self.assertGreaterEqual(migrate, 0.3)
        self.assertGreaterEqual(total, migrate)
        self.assertGreaterEqual(append, 0.0)
        self.assertGreaterEqual(render, 0.0)
        self.assertEqual(m.group(5), str(Path(self.tmp)))

    def test_render_stage_over_threshold_is_reported_in_render_field(self):
        os.environ[SLOW_ENV_VAR] = "0.1"
        from state_store.write_api import WriteAPI

        real_render = WriteAPI._render_tracker_atomic

        def slow_render(self_, *args, **kwargs):
            time.sleep(0.3)
            return real_render(self_, *args, **kwargs)

        with mock.patch.object(WriteAPI, "_render_tracker_atomic", slow_render):
            _item, err = self._create_captured()

        lines = [ln for ln in err.splitlines() if ln.startswith("tracker-create slow:")]
        self.assertEqual(len(lines), 1, f"expected exactly one slow line, got stderr={err!r}")
        m = SLOW_LINE.match(lines[0])
        self.assertIsNotNone(m, f"line does not match the contract: {lines[0]!r}")
        render = float(m.group(4))
        self.assertGreaterEqual(render, 0.3)

    def test_nothing_emitted_under_threshold(self):
        os.environ[SLOW_ENV_VAR] = "60"
        _item, err = self._create_captured()
        self.assertNotIn("tracker-create slow", err)
        self.assertEqual(err, "")


if __name__ == "__main__":
    unittest.main()
