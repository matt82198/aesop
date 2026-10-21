"""Tests for tools/halt.py — the fleet kill switch.

Test strategy (TDD):
1. Sentinel set/detect/clear round-trip (halt() / is_halted() / clear_halt())
2. is_halted() reflects the .HALT sentinel file's presence exactly
3. State dir resolution: AESOP_STATE_ROOT env var, aesop.config.json state_root, default
4. CLI: `set "<reason>"`, `--status`, `--clear` exit codes and output
5. run-watchdog.sh HALT-skip path is covered separately in tests/test_run_watchdog_halt.sh
   (bash, driving the real script against a temp AESOP_ROOT — never the real state dir).

HERMETIC: every test below points AESOP_STATE_ROOT (or aesop.config.json state_root) at a
throwaway tempfile.mkdtemp() fixture. No test ever writes to the real project state dir.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

TOOLS_DIR = Path(__file__).parent.parent / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

HALT_PY = TOOLS_DIR / "halt.py"

ENV_KEYS = ("AESOP_STATE_ROOT", "AESOP_ROOT")


class HaltTestCase(unittest.TestCase):
    """Base class: isolated temp state dir, never touches real ./state."""

    def setUp(self):
        self.fixture_root = Path(tempfile.mkdtemp(prefix="aesop-halt-test-"))
        self.state_dir = self.fixture_root / "state"
        self.state_dir.mkdir(parents=True)

        self._saved_env = {k: os.environ.get(k) for k in ENV_KEYS}
        os.environ["AESOP_STATE_ROOT"] = str(self.state_dir)
        os.environ.pop("AESOP_ROOT", None)

        # Ensure a fresh module import per test picks up env changes where relevant
        for mod in ("halt", "common"):
            sys.modules.pop(mod, None)
        import halt
        self.halt = halt

    def tearDown(self):
        for k, v in self._saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        sys.modules.pop("halt", None)
        shutil.rmtree(self.fixture_root, ignore_errors=True)


class TestHaltRoundTrip(HaltTestCase):
    def test_not_halted_initially(self):
        self.assertFalse(self.halt.is_halted())

    def test_halt_creates_sentinel(self):
        sentinel = self.halt.halt("test reason")
        self.assertTrue(Path(sentinel).exists())
        self.assertTrue(self.halt.is_halted())

    def test_sentinel_contains_reason_and_timestamp(self):
        self.halt.halt("cost ceiling exceeded")
        sentinel_path = self.state_dir / ".HALT"
        data = json.loads(sentinel_path.read_text(encoding="utf-8"))
        self.assertEqual(data["reason"], "cost ceiling exceeded")
        self.assertIn("timestamp", data)
        self.assertTrue(data["timestamp"])

    def test_clear_halt_removes_sentinel(self):
        self.halt.halt("something bad")
        self.assertTrue(self.halt.is_halted())
        cleared = self.halt.clear_halt()
        self.assertTrue(cleared)
        self.assertFalse(self.halt.is_halted())

    def test_clear_halt_when_not_halted_is_noop(self):
        self.assertFalse(self.halt.is_halted())
        cleared = self.halt.clear_halt()
        self.assertFalse(cleared)
        self.assertFalse(self.halt.is_halted())

    def test_halt_is_idempotent_overwrites_reason(self):
        self.halt.halt("first reason")
        self.halt.halt("second reason")
        info = self.halt.get_halt_info()
        self.assertEqual(info["reason"], "second reason")

    def test_state_dir_created_if_missing(self):
        # halt() must create the state dir if it doesn't exist yet
        shutil.rmtree(self.state_dir)
        self.assertFalse(self.state_dir.exists())
        self.halt.halt("dir did not exist")
        self.assertTrue(self.halt.is_halted())


class TestHaltStateDirResolution(unittest.TestCase):
    """State dir resolution honors AESOP_STATE_ROOT, then config, then default."""

    def setUp(self):
        self.fixture_root = Path(tempfile.mkdtemp(prefix="aesop-halt-resolve-test-"))
        self._saved_env = {k: os.environ.get(k) for k in ("AESOP_STATE_ROOT", "AESOP_ROOT")}
        for k in self._saved_env:
            os.environ.pop(k, None)
        for mod in ("halt", "common"):
            sys.modules.pop(mod, None)
        import halt
        self.halt = halt

    def tearDown(self):
        for k, v in self._saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        sys.modules.pop("halt", None)
        shutil.rmtree(self.fixture_root, ignore_errors=True)

    def test_env_var_wins_over_config(self):
        env_state = self.fixture_root / "env-state"
        env_state.mkdir()
        os.environ["AESOP_STATE_ROOT"] = str(env_state)
        config = {"state_root": str(self.fixture_root / "config-state")}
        resolved = self.halt.resolve_state_dir(config=config)
        self.assertEqual(resolved, env_state)

    def test_config_state_root_used_when_no_env(self):
        config_state = self.fixture_root / "config-state"
        config = {"state_root": str(config_state)}
        resolved = self.halt.resolve_state_dir(config=config)
        self.assertEqual(resolved, config_state)

    def test_default_when_nothing_set(self):
        # No env var, no config state_root -> falls back to common.get_state_dir()
        resolved = self.halt.resolve_state_dir(config={})
        self.assertTrue(str(resolved).endswith("state"))


class TestHaltCLI(HaltTestCase):
    def _run_cli(self, *args):
        env = os.environ.copy()
        return subprocess.run(
            [sys.executable, str(HALT_PY), *args],
            capture_output=True, text=True, env=env,
        )

    def test_cli_set_creates_halt(self):
        result = self._run_cli("set", "manual stop for wave audit")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("HALTED", result.stdout)
        self.assertTrue((self.state_dir / ".HALT").exists())

    def test_cli_status_reports_not_halted(self):
        result = self._run_cli("--status")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("not halted", result.stdout.lower())

    def test_cli_status_reports_halted_with_reason(self):
        self.halt.halt("budget blown")
        result = self._run_cli("--status")
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("budget blown", result.stdout)

    def test_cli_clear_removes_sentinel(self):
        self.halt.halt("temp")
        result = self._run_cli("--clear")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((self.state_dir / ".HALT").exists())

    def test_cli_set_requires_reason(self):
        result = self._run_cli("set")
        self.assertNotEqual(result.returncode, 0)


class TestConductorRootFailClosed(unittest.TestCase):
    """CONDUCTOR_ROOT resolution follows daemons/conductor-root.sh exactly.

    Explicit CONDUCTOR_ROOT wins. The sibling conductor3 is derived ONLY when the
    AESOP_ROOT basename is the canonical "aesop". Anything else is unresolved and
    must fail closed: no home-directory fallback, no guessed sibling.
    """

    KEYS = ("CONDUCTOR_ROOT", "AESOP_ROOT", "AESOP_STATE_ROOT", "HOME", "USERPROFILE")

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="aesop-root-fc-"))
        self._saved = {k: os.environ.get(k) for k in self.KEYS}
        for k in self.KEYS:
            os.environ.pop(k, None)
        sys.modules.pop("halt", None)
        sys.modules.pop("common", None)
        import halt
        self.halt = halt

    def tearDown(self):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        sys.modules.pop("halt", None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_explicit_conductor_root_wins(self):
        explicit = self.tmp / "explicit-fleet"
        os.environ["CONDUCTOR_ROOT"] = str(explicit)
        os.environ["AESOP_ROOT"] = str(self.tmp / "aesop-wt-x")
        self.assertEqual(self.halt.resolve_conductor_root(), explicit)

    def test_unset_non_canonical_aesop_root_is_unresolved(self):
        os.environ["AESOP_ROOT"] = str(self.tmp / "aesop-wt-x")
        self.assertIsNone(self.halt.resolve_conductor_root())

    def test_unset_canonical_aesop_root_derives_sibling(self):
        os.environ["AESOP_ROOT"] = str(self.tmp / "aesop")
        self.assertEqual(self.halt.resolve_conductor_root(), self.tmp / "conductor3")

    def test_no_home_fallback_when_unresolved(self):
        home = self.tmp / "home"
        (home / "conductor3" / "state").mkdir(parents=True)
        (home / "conductor3" / "state" / ".HALT").write_text('{"reason": "x"}', encoding="utf-8")
        os.environ["HOME"] = str(home)
        os.environ["USERPROFILE"] = str(home)
        os.environ["AESOP_ROOT"] = str(self.tmp / "aesop-wt-x")
        self.assertIsNone(self.halt.resolve_conductor_root())
        with self.assertRaises(RuntimeError):
            self.halt.box_wide_sentinel_path()

    def test_cli_status_fails_closed_when_unresolved(self):
        home = self.tmp / "home"
        (home / "conductor3" / "state").mkdir(parents=True)
        (home / "conductor3" / "state" / ".HALT").write_text('{"reason": "x"}', encoding="utf-8")
        env = {k: v for k, v in os.environ.items() if k not in self.KEYS}
        env["HOME"] = str(home)
        env["USERPROFILE"] = str(home)
        env["AESOP_ROOT"] = str(self.tmp / "aesop-wt-x")
        result = subprocess.run(
            [sys.executable, str(HALT_PY), "--status"],
            env=env, capture_output=True, text=True, encoding="utf-8", errors="replace",
            cwd=str(self.tmp), timeout=60,
        )
        # Exit 1 would mean "halted" via the home sentinel (the old fallback). Unresolved
        # must be a distinct non-0/non-1 failure so every caller treats it as UNKNOWN.
        self.assertNotIn(result.returncode, (0, 1), result.stdout + result.stderr)
        self.assertIn("CONDUCTOR_ROOT", result.stderr)

    def test_cli_status_uses_explicit_conductor_root(self):
        fleet = self.tmp / "fleet"
        (fleet / "state").mkdir(parents=True)
        (fleet / "state" / ".HALT").write_text('{"reason": "box stop"}', encoding="utf-8")
        env = {k: v for k, v in os.environ.items() if k not in self.KEYS}
        env["CONDUCTOR_ROOT"] = str(fleet)
        env["AESOP_ROOT"] = str(self.tmp / "aesop-wt-x")
        result = subprocess.run(
            [sys.executable, str(HALT_PY), "--status"],
            env=env, capture_output=True, text=True, encoding="utf-8", errors="replace",
            cwd=str(self.tmp), timeout=60,
        )
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("box stop", result.stdout)


if __name__ == "__main__":
    unittest.main()
