"""Tests for tools/cost_ceiling.py — the fleet cost ceiling / spend guard.

Test strategy (TDD):
1. Under/at/over ceiling classification (pure function, no side effects)
2. Over-ceiling trips tools/halt.py's .HALT sentinel (in a temp state dir)
3. Under-ceiling never touches the sentinel
4. No ceiling configured (null/absent) -> never trips, always "ok"
5. Spend read from the ledger file when --spent / spent= isn't supplied
6. CLI --check --spent N: exit codes and output

HERMETIC: every test points AESOP_STATE_ROOT at a throwaway tempfile.mkdtemp()
fixture. No test ever writes a real .HALT into the real project state dir.
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

COST_CEILING_PY = TOOLS_DIR / "cost_ceiling.py"

ENV_KEYS = ("AESOP_STATE_ROOT", "AESOP_ROOT")


class CostCeilingTestCase(unittest.TestCase):
    def setUp(self):
        self.fixture_root = Path(tempfile.mkdtemp(prefix="aesop-cost-ceiling-test-"))
        self.state_dir = self.fixture_root / "state"
        self.state_dir.mkdir(parents=True)

        self._saved_env = {k: os.environ.get(k) for k in ENV_KEYS}
        os.environ["AESOP_STATE_ROOT"] = str(self.state_dir)
        os.environ.pop("AESOP_ROOT", None)

        for mod in ("cost_ceiling", "halt", "common"):
            sys.modules.pop(mod, None)
        import cost_ceiling
        import halt
        self.cost_ceiling = cost_ceiling
        self.halt = halt

    def tearDown(self):
        for k, v in self._saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        sys.modules.pop("cost_ceiling", None)
        sys.modules.pop("halt", None)
        shutil.rmtree(self.fixture_root, ignore_errors=True)

    def _write_ledger(self, rows):
        """rows: list of (tokens_in, tokens_out) tuples."""
        ledger_dir = self.state_dir / "ledger"
        ledger_dir.mkdir(parents=True, exist_ok=True)
        ledger_file = ledger_dir / "OUTCOMES-LEDGER.md"
        header = (
            "| ISO ts | agent_type | model | duration_sec | tokens_in | tokens_out | verdict | phase | wave |\n"
            "|--------|------------|-------|--------------|-----------|------------|--------|-------|------|\n"
        )
        lines = [header]
        for ti, to in rows:
            lines.append(
                f"| 2026-07-16T00:00:00Z | build | haiku | 10 | {ti} | {to} | OK | build | 26 |\n"
            )
        ledger_file.write_text("".join(lines), encoding="utf-8")
        return ledger_file


class TestCeilingClassification(CostCeilingTestCase):
    def test_under_ceiling_not_exceeded(self):
        result = self.cost_ceiling.check(spent=500, period="wave", config={"limits": {"max_wave_tokens": 1000}})
        self.assertFalse(result["exceeded"])
        self.assertFalse(result["tripped"])

    def test_at_ceiling_is_exceeded(self):
        result = self.cost_ceiling.check(spent=1000, period="wave", config={"limits": {"max_wave_tokens": 1000}})
        self.assertTrue(result["exceeded"])

    def test_over_ceiling_is_exceeded(self):
        result = self.cost_ceiling.check(spent=1500, period="wave", config={"limits": {"max_wave_tokens": 1000}})
        self.assertTrue(result["exceeded"])

    def test_no_ceiling_configured_never_exceeded(self):
        result = self.cost_ceiling.check(spent=10_000_000, period="wave", config={"limits": {"max_wave_tokens": None}})
        self.assertFalse(result["exceeded"])
        self.assertFalse(result["tripped"])

    def test_missing_limits_key_never_exceeded(self):
        result = self.cost_ceiling.check(spent=10_000_000, period="wave", config={})
        self.assertFalse(result["exceeded"])

    def test_daily_period_uses_max_daily_tokens(self):
        config = {"limits": {"max_wave_tokens": 100, "max_daily_tokens": 5000}}
        result = self.cost_ceiling.check(spent=200, period="daily", config=config)
        self.assertFalse(result["exceeded"])
        result2 = self.cost_ceiling.check(spent=6000, period="daily", config=config)
        self.assertTrue(result2["exceeded"])


class TestCeilingTripsHalt(CostCeilingTestCase):
    def test_over_ceiling_trips_halt(self):
        self.assertFalse(self.halt.is_halted())
        result = self.cost_ceiling.check(
            spent=2000, period="wave", config={"limits": {"max_wave_tokens": 1000}}
        )
        self.assertTrue(result["tripped"])
        self.assertTrue(self.halt.is_halted())
        info = self.halt.get_halt_info()
        self.assertIn("cost", info["reason"].lower())

    def test_under_ceiling_does_not_trip_halt(self):
        self.cost_ceiling.check(spent=100, period="wave", config={"limits": {"max_wave_tokens": 1000}})
        self.assertFalse(self.halt.is_halted())

    def test_trip_false_disables_side_effect(self):
        result = self.cost_ceiling.check(
            spent=2000, period="wave", config={"limits": {"max_wave_tokens": 1000}}, trip=False
        )
        self.assertTrue(result["exceeded"])
        self.assertFalse(result["tripped"])
        self.assertFalse(self.halt.is_halted())


class TestCeilingLedgerSpend(CostCeilingTestCase):
    def test_spend_read_from_ledger_when_not_supplied(self):
        self._write_ledger([(100, 200), (50, 150)])
        result = self.cost_ceiling.check(period="wave", config={"limits": {"max_wave_tokens": 1000}})
        self.assertEqual(result["spent"], 500)  # 100+200+50+150
        self.assertFalse(result["exceeded"])

    def test_ledger_spend_over_ceiling_trips(self):
        self._write_ledger([(600, 600)])
        result = self.cost_ceiling.check(period="wave", config={"limits": {"max_wave_tokens": 1000}})
        self.assertTrue(result["exceeded"])
        self.assertTrue(self.halt.is_halted())

    def test_missing_ledger_treated_as_zero_spend(self):
        result = self.cost_ceiling.check(period="wave", config={"limits": {"max_wave_tokens": 1000}})
        self.assertEqual(result["spent"], 0)
        self.assertFalse(result["exceeded"])


class TestCheckModeIsReadOnly(CostCeilingTestCase):
    """A --check / check() call must NEVER mutate the state tree.

    Escape this reproduces: cost_ceiling.check() -> read_ledger_total_tokens()
    -> fleet_ledger.parse_ledger_rows() -> ensure_ledger_header(), which CREATED
    <state>/ledger/OUTCOMES-LEDGER.md on a fresh tree just by asking "how much
    have we spent?". Check modes are read-only by contract; header creation
    belongs on the APPEND path only.
    """

    def _state_snapshot(self):
        return sorted(str(p.relative_to(self.state_dir)) for p in self.state_dir.rglob("*"))

    def test_check_api_creates_no_ledger_file(self):
        before = self._state_snapshot()
        result = self.cost_ceiling.check(period="wave", config={"limits": {"max_wave_tokens": 1000}})
        self.assertEqual(result["spent"], 0)
        self.assertFalse(result["exceeded"])
        self.assertFalse((self.state_dir / "ledger" / "OUTCOMES-LEDGER.md").exists(),
                         "check() created the ledger file — check mode must be read-only")
        self.assertFalse((self.state_dir / "ledger").exists(),
                         "check() created the ledger directory — check mode must be read-only")
        self.assertEqual(before, self._state_snapshot(), "check() mutated the state tree")

    def test_check_daily_period_creates_no_ledger_file(self):
        before = self._state_snapshot()
        result = self.cost_ceiling.check(period="daily", config={"limits": {"max_daily_tokens": 1000}})
        self.assertEqual(result["spent"], 0)
        self.assertEqual(before, self._state_snapshot(), "check(period='daily') mutated the state tree")

    def test_check_windowed_creates_no_ledger_file(self):
        before = self._state_snapshot()
        self.cost_ceiling.check(period="wave", window_minutes=30,
                                config={"limits": {"max_wave_tokens": 1000}})
        self.assertEqual(before, self._state_snapshot(), "windowed check() mutated the state tree")

    def test_cli_check_creates_no_ledger_file(self):
        """The exact meta-gate reproduction: `cost_ceiling.py --check` on a fresh tree."""
        config_path = self.fixture_root / "aesop.config.json"
        config_path.write_text(json.dumps({"limits": {"max_wave_tokens": 1000}}), encoding="utf-8")
        before = self._state_snapshot()
        env = os.environ.copy()
        result = subprocess.run(
            [sys.executable, str(COST_CEILING_PY), "--check"],
            capture_output=True, text=True, encoding="utf-8", env=env, cwd=str(self.fixture_root),
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse((self.state_dir / "ledger" / "OUTCOMES-LEDGER.md").exists(),
                         "CLI --check created the ledger file — check mode must be read-only")
        self.assertEqual(before, self._state_snapshot(), "CLI --check mutated the state tree")

    def test_check_still_reads_an_existing_ledger(self):
        """Read-only must not mean blind: an existing ledger is still summed."""
        self._write_ledger([(100, 200), (50, 150)])
        result = self.cost_ceiling.check(period="wave", config={"limits": {"max_wave_tokens": 1000}})
        self.assertEqual(result["spent"], 500)


class TestCeilingCLI(CostCeilingTestCase):
    def _run_cli(self, *args):
        env = os.environ.copy()
        return subprocess.run(
            [sys.executable, str(COST_CEILING_PY), *args],
            capture_output=True, text=True, env=env,
        )

    def _write_config(self, config):
        config_path = self.fixture_root / "aesop.config.json"
        config_path.write_text(json.dumps(config), encoding="utf-8")
        return config_path

    def test_cli_under_ceiling_exit_zero(self):
        self._write_config({"limits": {"max_wave_tokens": 1000}})
        env = os.environ.copy()
        result = subprocess.run(
            [sys.executable, str(COST_CEILING_PY), "--check", "--spent", "100"],
            capture_output=True, text=True, env=env, cwd=str(self.fixture_root),
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse(self.halt.is_halted())

    def test_cli_over_ceiling_exit_nonzero_and_trips(self):
        self._write_config({"limits": {"max_wave_tokens": 1000}})
        env = os.environ.copy()
        result = subprocess.run(
            [sys.executable, str(COST_CEILING_PY), "--check", "--spent", "5000"],
            capture_output=True, text=True, env=env, cwd=str(self.fixture_root),
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(self.halt.is_halted())

    def test_cli_missing_required_check_flag_exit_2(self):
        """LOAD-BEARING: CLI without --check flag must exit 2 (fail-closed, required flag).
        Mutant: changing return 2 to return 0 would cause this assertion to fail.
        """
        self._write_config({"limits": {"max_wave_tokens": 1000}})
        env = os.environ.copy()
        # Invoke cost_ceiling.py WITHOUT --check flag (it's required)
        result = subprocess.run(
            [sys.executable, str(COST_CEILING_PY), "--spent", "100"],
            capture_output=True, text=True, env=env, cwd=str(self.fixture_root),
        )
        # Must exit with 2 (argparse error/missing required flag behavior)
        self.assertEqual(result.returncode, 2,
                        msg="Missing required --check flag must exit 2 (fail-closed); "
                        "mutant changing this to 0 would be caught here")
        # Verify halt was NOT tripped (we didn't even attempt a check)
        self.assertFalse(self.halt.is_halted(),
                        msg="Missing --check should not trip halt")


class TestDailySemanticsMultipleDays(CostCeilingTestCase):
    """Test that daily period filters to TODAY only, not entire ledger."""

    def _write_ledger_multi_day(self):
        """Write a ledger spanning multiple days."""
        ledger_dir = self.state_dir / "ledger"
        ledger_dir.mkdir(parents=True, exist_ok=True)
        ledger_file = ledger_dir / "OUTCOMES-LEDGER.md"
        header = (
            "| ISO ts | agent_type | model | duration_sec | tokens_in | tokens_out | verdict | phase | wave |\n"
            "|--------|------------|-------|--------------|-----------|------------|--------|-------|------|\n"
        )
        # Dates derive from the real clock (UTC): a hardcoded "today" rots at
        # midnight UTC and bricked CI once (wave-rc.3).
        from datetime import datetime, timedelta, timezone
        today = datetime.now(timezone.utc).date()
        d1, d2 = today - timedelta(days=2), today - timedelta(days=1)
        # Day 1: 500 tokens
        day1_line = f"| {d1}T10:00:00Z | build | haiku | 10 | 200 | 300 | OK | build | 26 |\n"
        # Day 2: 600 tokens
        day2_line1 = f"| {d2}T08:00:00Z | build | haiku | 10 | 150 | 250 | OK | build | 26 |\n"
        day2_line2 = f"| {d2}T15:30:00Z | verify | haiku | 5 | 100 | 100 | OK | verify | 26 |\n"
        # Day 3 (today): 400 tokens
        day3_line = f"| {today}T09:15:00Z | build | haiku | 8 | 150 | 250 | OK | build | 27 |\n"
        lines = [header, day1_line, day2_line1, day2_line2, day3_line]
        ledger_file.write_text("".join(lines), encoding="utf-8")
        return ledger_file

    def test_daily_filters_to_today_only(self):
        """For period='daily', verify only today's rows are summed."""
        self._write_ledger_multi_day()
        # Expected: only today's rows = 150 + 250 = 400 tokens
        result = self.cost_ceiling.check(period="daily", config={"limits": {"max_daily_tokens": 1000}})
        self.assertEqual(result["spent"], 400, f"Expected 400 tokens for today, got {result['spent']}")
        self.assertFalse(result["exceeded"])

    def test_daily_exceeds_with_today_spend(self):
        """Verify daily ceiling trip uses today's spend only, not lifetime."""
        self._write_ledger_multi_day()
        # Today has 400 tokens; set ceiling to 300
        result = self.cost_ceiling.check(period="daily", config={"limits": {"max_daily_tokens": 300}})
        self.assertTrue(result["exceeded"])
        self.assertTrue(result["tripped"])

    def test_wave_period_sums_all_rows(self):
        """Verify period='wave' still sums ALL rows across all days."""
        self._write_ledger_multi_day()
        # All rows: day1=500, day2=600, day3=400 = 1500 total
        result = self.cost_ceiling.check(period="wave", config={"limits": {"max_wave_tokens": 2000}})
        self.assertEqual(result["spent"], 1500, f"Expected 1500 total tokens, got {result['spent']}")
        self.assertFalse(result["exceeded"])


class TestSharedParser(CostCeilingTestCase):
    """Test that cost_ceiling.py uses fleet_ledger.py's parser (single source of truth)."""

    def test_uses_fleet_ledger_parser(self):
        """Verify cost_ceiling imports and uses fleet_ledger's shared parser."""
        # This test ensures the refactor is in place
        self._write_ledger([(100, 200), (50, 150)])
        result = self.cost_ceiling.check(period="wave", config={"limits": {"max_wave_tokens": 1000}})
        # Should get correct total: 100+200+50+150 = 500
        self.assertEqual(result["spent"], 500)

    def test_shared_parser_no_reimplementation(self):
        """Verify cost_ceiling doesn't reimplement the ledger parsing logic."""
        # Read cost_ceiling.py source to ensure it uses fleet_ledger
        import inspect
        source = inspect.getsource(self.cost_ceiling)
        # Should NOT contain duplicate parsing logic (read_ledger_total_tokens should be gone or minimal)
        # Should import or call fleet_ledger's parser
        self.assertIn("fleet_ledger", source.lower(), "cost_ceiling.py should import/use fleet_ledger")


if __name__ == "__main__":
    unittest.main()
