"""Behavioural tests for tools/state_root.py: one resolver for the live event-store root.

Precedence under test: --state-dir > AESOP_STATE_ROOT > aesop.config.json state_root >
FAIL CLOSED (no silent <repo>/state default). Every test isolates the config directory
with a temp AESOP_ROOT so the live aesop.config.json is never read.
"""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from state_store.api import StateAPI  # noqa: E402
from state_store.paths import resolve_db_path  # noqa: E402
from tools import state_root  # noqa: E402

TOOL = REPO_ROOT / "tools" / "state_root.py"
BOOT_TOOL = REPO_ROOT / "tools" / "boot_state.py"


def _write_config(config_dir: Path, payload: str) -> None:
    (config_dir / "aesop.config.json").write_text(payload, encoding="utf-8")


class ResolveStateRootTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(self._tmp.name)
        self.config_dir = self.base / "config"
        self.config_dir.mkdir()
        self.flag_dir = self.base / "flag-store"
        self.env_dir = self.base / "env-store"
        self.cfg_dir = self.base / "cfg-store"
        self._env = mock.patch.dict(os.environ, {"AESOP_ROOT": str(self.config_dir)}, clear=False)
        self._env.start()
        os.environ.pop("AESOP_STATE_ROOT", None)

    def tearDown(self):
        self._env.stop()
        self._tmp.cleanup()

    def _cfg(self, value):
        _write_config(self.config_dir, '{"state_root": %s}' % _json_str(value))

    def test_flag_wins_over_env_and_config(self):
        self._cfg(str(self.cfg_dir))
        os.environ["AESOP_STATE_ROOT"] = str(self.env_dir)
        got = state_root.resolve_state_root(str(self.flag_dir))
        self.assertEqual(got, self.flag_dir)

    def test_env_wins_over_config(self):
        self._cfg(str(self.cfg_dir))
        os.environ["AESOP_STATE_ROOT"] = str(self.env_dir)
        self.assertEqual(state_root.resolve_state_root(None), self.env_dir)

    def test_config_used_when_flag_and_env_absent(self):
        self._cfg(str(self.cfg_dir))
        self.assertEqual(state_root.resolve_state_root(None), self.cfg_dir)

    def test_empty_env_is_treated_as_unset(self):
        self._cfg(str(self.cfg_dir))
        os.environ["AESOP_STATE_ROOT"] = ""
        self.assertEqual(state_root.resolve_state_root(None), self.cfg_dir)

    def test_config_tilde_expands_against_home(self):
        home = self.base / "home"
        home.mkdir()
        self._cfg("~/aesop-state")
        with mock.patch.dict(os.environ, {"HOME": str(home), "USERPROFILE": str(home)}):
            self.assertEqual(state_root.resolve_state_root(None), home / "aesop-state")

    def test_no_source_fails_closed_naming_the_key(self):
        with self.assertRaises(state_root.StateRootError) as ctx:
            state_root.resolve_state_root(None)
        self.assertIn("state_root", str(ctx.exception))

    def test_no_source_does_not_fall_back_to_repo_state(self):
        with self.assertRaises(state_root.StateRootError):
            state_root.resolve_state_root(None)
        self.assertFalse((REPO_ROOT / "state" / "__state_root_probe__").exists())

    def test_config_file_without_key_fails_closed(self):
        _write_config(self.config_dir, '{"brain_root": "~/.claude"}')
        with self.assertRaises(state_root.StateRootError) as ctx:
            state_root.resolve_state_root(None)
        self.assertIn("state_root", str(ctx.exception))

    def test_blank_config_value_fails_closed(self):
        self._cfg("   ")
        with self.assertRaises(state_root.StateRootError):
            state_root.resolve_state_root(None)

    def test_relative_config_value_is_rejected(self):
        self._cfg("./state")
        with self.assertRaises(state_root.StateRootError) as ctx:
            state_root.resolve_state_root(None)
        self.assertIn("absolute", str(ctx.exception))

    def test_invalid_json_config_fails_closed(self):
        _write_config(self.config_dir, "{not json")
        with self.assertRaises(state_root.StateRootError) as ctx:
            state_root.resolve_state_root(None)
        self.assertIn("unreadable", str(ctx.exception))

    def test_config_only_missing_file_prints_nothing_and_exits_0(self):
        rc = state_root.main(["--config-only"])
        self.assertEqual(rc, 0)

    def test_config_only_reports_configured_value(self):
        self._cfg(str(self.cfg_dir))
        self.assertEqual(state_root.configured_state_root(), self.cfg_dir)


class StateRootCliTests(unittest.TestCase):
    """Drive tools/state_root.py and tools/boot_state.py as real subprocesses."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(self._tmp.name)
        self.config_dir = self.base / "config"
        self.config_dir.mkdir()
        self.repo = self.base / "repo"
        self.repo.mkdir()
        (self.repo / "STATE.md").write_text("# STATE\n", encoding="utf-8")
        (self.repo / "BUILDLOG.md").write_text("# BUILDLOG\n", encoding="utf-8")

    def tearDown(self):
        self._tmp.cleanup()

    def _env(self, state_env=None):
        env = {k: v for k, v in os.environ.items() if k != "AESOP_STATE_ROOT"}
        env["AESOP_ROOT"] = str(self.config_dir)
        env["AESOP_TASK_EVENTS_JSONL"] = str(self.base / "task-events.jsonl")
        if state_env is not None:
            env["AESOP_STATE_ROOT"] = state_env
        return env

    def _run(self, tool, *args, state_env=None):
        return subprocess.run(
            [sys.executable, str(tool), *args],
            capture_output=True, encoding="utf-8", errors="replace",
            env=self._env(state_env), timeout=60,
        )

    def test_resolver_cli_fails_closed_with_one_stderr_line(self):
        r = self._run(TOOL)
        self.assertEqual(r.returncode, 2)
        self.assertEqual(r.stdout, "")
        lines = [ln for ln in r.stderr.splitlines() if ln.strip()]
        self.assertEqual(len(lines), 1, r.stderr)
        self.assertIn("state_root", lines[0])

    def test_resolver_cli_prints_flag_value(self):
        target = self.base / "flag"
        r = self._run(TOOL, "--state-dir", str(target))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), str(target))

    def test_boot_brief_without_any_source_exits_2_and_creates_no_repo_state(self):
        r = self._run(BOOT_TOOL, "--repo", str(self.repo))
        self.assertEqual(r.returncode, 2)
        self.assertEqual(r.stdout, "")
        self.assertIn("state_root", r.stderr)
        self.assertFalse((self.repo / "state").exists())

    def test_boot_brief_reads_configured_store(self):
        store = self.base / "cfg-store"
        store.mkdir()
        with StateAPI(resolve_db_path(store, "tracker")):
            pass
        _write_config(self.config_dir, '{"state_root": %s}' % _json_str(str(store)))
        r = self._run(BOOT_TOOL, "--repo", str(self.repo))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(r.stdout.startswith("STATE.md "), r.stdout[:200])
        self.assertIn("## NEXT STEPS", r.stdout)


def _json_str(value: str) -> str:
    import json
    return json.dumps(value)


if __name__ == "__main__":
    unittest.main()
