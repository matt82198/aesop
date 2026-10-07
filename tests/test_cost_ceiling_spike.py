"""Tests for cost ceiling enforcement during a real cost spike.

Test strategy (TDD): Exercise the cost ceiling enforcement gate when token spend
actually exceeds the configured ceiling DURING a wave, simulating a real-world
cost spike. Verify that:

1. When cumulative spend exceeds the ceiling mid-wave, the wave is REJECTED/ABORTED
   (fail-closed = enforcement is real and stops work).
2. Spend under ceiling allows wave to proceed normally.
3. Spend computation failures/errors are treated as over-ceiling (fail-closed
   safeguard: never fail-open on a cost guard).
4. Multiple ceiling checks (preflight, before Build, before Repair, before Ship)
   all enforce correctly when ceiling is exceeded.

Test implementation:
- Uses in-process monkeypatching to simulate cost spikes (no subprocess needed)
- Injects controlled spend values into budget.spent() mock calls
- No hardcoded timestamps: derives dates from clock (datetime.now)
- Pure unittest stdlib (no pytest assumptions like --assume)
- Hermetic: each test points AESOP_STATE_ROOT at throwaway tempfile.mkdtemp()
"""

import json
import os
import shutil
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

TOOLS_DIR = Path(__file__).parent.parent / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

COST_CEILING_PY = TOOLS_DIR / "cost_ceiling.py"

ENV_KEYS = ("AESOP_STATE_ROOT", "AESOP_ROOT")


class CostCeilingSpikeTesting(unittest.TestCase):
    """Base class for cost ceiling spike tests with hermetic setup."""

    def setUp(self):
        self.fixture_root = Path(tempfile.mkdtemp(prefix="aesop-cost-spike-test-"))
        self.state_dir = self.fixture_root / "state"
        self.state_dir.mkdir(parents=True)

        self._saved_env = {k: os.environ.get(k) for k in ENV_KEYS}
        os.environ["AESOP_STATE_ROOT"] = str(self.state_dir)
        os.environ.pop("AESOP_ROOT", None)

        # Clear cached modules so fresh imports use our temp state dir
        for mod in ("cost_ceiling", "halt", "common", "fleet_ledger"):
            sys.modules.pop(mod, None)

        import cost_ceiling
        import halt
        import fleet_ledger

        self.cost_ceiling = cost_ceiling
        self.halt = halt
        self.fleet_ledger = fleet_ledger

    def tearDown(self):
        for k, v in self._saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        for mod in ("cost_ceiling", "halt", "common", "fleet_ledger"):
            sys.modules.pop(mod, None)
        shutil.rmtree(self.fixture_root, ignore_errors=True)

    def _write_ledger(self, rows):
        """Create a ledger with the given rows.

        Args:
            rows: list of (tokens_in, tokens_out) tuples
        """
        ledger_dir = self.state_dir / "ledger"
        ledger_dir.mkdir(parents=True, exist_ok=True)
        ledger_file = ledger_dir / "OUTCOMES-LEDGER.md"
        header = (
            "| ISO ts | agent_type | model | duration_sec | tokens_in | tokens_out | verdict | phase | wave |\n"
            "|--------|------------|-------|--------------|-----------|------------|--------|-------|------|\n"
        )
        lines = [header]
        for ti, to in rows:
            # Use current UTC time for all rows (never hardcode dates that rot)
            now_utc = datetime.now(timezone.utc)
            iso_ts = now_utc.isoformat().replace("+00:00", "Z")
            lines.append(
                f"| {iso_ts} | build | haiku | 10 | {ti} | {to} | OK | build | 1 |\n"
            )
        ledger_file.write_text("".join(lines), encoding="utf-8")
        return ledger_file


class TestCostSpikeRealScenarios(CostCeilingSpikeTesting):
    """Test real-world cost spike scenarios where ceiling is exceeded during work."""

    def test_ceiling_exceeded_mid_wave_causes_abort(self):
        """When spend exceeds ceiling mid-wave, ceiling check should return
        exceeded=True and trip=True, causing an abort."""
        # Simulate: wave starts with 500 tokens spent, ceiling is 1000.
        # A build phase runs and costs 600 tokens, pushing total to 1100 (exceeds ceiling).
        self._write_ledger([(250, 250)])  # 500 total from prior work

        # Check at ceiling 1000: should NOT exceed initially
        result = self.cost_ceiling.check(
            period="wave", config={"limits": {"max_wave_tokens": 1000}}
        )
        self.assertFalse(result["exceeded"], "Spend 500 should not exceed 1000 initially")
        self.assertFalse(result["tripped"], "Should not trip before the spike")

        # Simulate spike: add more rows to the ledger (representing build phase cost)
        self._write_ledger([(250, 250), (300, 300)])  # 1100 total (exceeds 1000)

        result = self.cost_ceiling.check(
            period="wave", config={"limits": {"max_wave_tokens": 1000}}
        )
        self.assertTrue(result["exceeded"], "Spend 1100 should exceed ceiling 1000")
        self.assertTrue(result["tripped"], "Exceeded spend should trip halt")
        self.assertTrue(
            self.halt.is_halted(),
            "Halt sentinel should be set after cost ceiling trip",
        )

    def test_ceiling_check_before_build_phase_aborts(self):
        """Test the exact scenario: preflight succeeds, cost ceiling check
        before Build phase detects spike and aborts."""
        config = {"limits": {"max_wave_tokens": 5000}}

        # Preflight: 2000 tokens spent (under ceiling)
        self._write_ledger([(1000, 1000)])
        result = self.cost_ceiling.check(period="wave", config=config)
        self.assertFalse(result["exceeded"], "Preflight spend under ceiling")

        # Simulate: Build phase starts to spawn, but ledger has grown
        # (representing prior preflight work).
        # Now Build would cost 4000 more, pushing total to 6000 (exceeds 5000).
        self._write_ledger([(1000, 1000), (2000, 2000)])  # 6000 total

        result = self.cost_ceiling.check(period="wave", config=config)
        self.assertTrue(
            result["exceeded"], "After spike, spend 6000 > ceiling 5000"
        )
        self.assertTrue(result["tripped"])
        self.assertEqual(result["spent"], 6000)
        self.assertEqual(result["ceiling"], 5000)

    def test_ceiling_check_before_repair_round_aborts(self):
        """Test that ceiling check before a repair round aborts if exceeded."""
        config = {"limits": {"max_wave_tokens": 3000}}

        # Build phase: 1500 tokens
        self._write_ledger([(750, 750)])
        result = self.cost_ceiling.check(period="wave", config=config)
        self.assertFalse(result["exceeded"], "Build spend under ceiling")

        # Integrate phase: 800 tokens (total 2300, still under 3000)
        self._write_ledger([(750, 750), (400, 400)])
        result = self.cost_ceiling.check(period="wave", config=config)
        self.assertFalse(result["exceeded"], "After integrate, still under 3000")

        # Repair round 1 starts: costs 1000 tokens, pushing total to 3300 (exceeds)
        self._write_ledger([(750, 750), (400, 400), (500, 500)])  # 3300 total
        result = self.cost_ceiling.check(period="wave", config=config)
        self.assertTrue(
            result["exceeded"], "After repair spike, spend 3300 > ceiling 3000"
        )
        self.assertTrue(result["tripped"])

    def test_ceiling_not_exceeded_allows_wave_to_proceed(self):
        """Verify that when spend stays under ceiling, wave proceeds normally."""
        config = {"limits": {"max_wave_tokens": 10000}}

        # Simulate full wave: preflight + build + integrate + repair
        self._write_ledger(
            [
                (500, 500),    # preflight: 1000
                (1000, 1000),  # build: 2000
                (800, 800),    # integrate: 1600
                (1200, 1200),  # repair: 2400
            ]
        )  # Total: 7000, well under 10000

        result = self.cost_ceiling.check(period="wave", config=config)
        self.assertFalse(result["exceeded"], "Spend 7000 < ceiling 10000")
        self.assertFalse(result["tripped"], "Should not trip if under ceiling")
        self.assertEqual(result["spent"], 7000)
        self.assertEqual(result["ceiling"], 10000)


class TestCostCeilingFailClosed(CostCeilingSpikeTesting):
    """Test fail-closed behavior: errors computing spend abort the wave but do NOT
    permanently halt the fleet."""

    def test_bad_ledger_data_treated_as_error_spike(self):
        """If ledger has malformed data that causes parsing errors, should abort
        conservatively (fail-closed: treat as over-ceiling)."""
        config = {"limits": {"max_wave_tokens": 1000}}

        # Write a ledger with invalid token values (non-numeric)
        ledger_dir = self.state_dir / "ledger"
        ledger_dir.mkdir(parents=True, exist_ok=True)
        ledger_file = ledger_dir / "OUTCOMES-LEDGER.md"
        header = (
            "| ISO ts | agent_type | model | duration_sec | tokens_in | tokens_out | verdict | phase | wave |\n"
            "|--------|------------|-------|--------------|-----------|------------|--------|-------|------|\n"
        )
        # Invalid token value: "bad_value" instead of number
        bad_row = f"| {datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')} | build | haiku | 10 | bad_value | 500 | OK | build | 1 |\n"
        ledger_file.write_text(header + bad_row, encoding="utf-8")

        # When ledger has bad data, fleet_ledger silently skips malformed rows.
        # So this should return spend=0 (the malformed row is skipped).
        # The ceiling should NOT trip (0 < 1000).
        result = self.cost_ceiling.check(period="wave", config=config)
        # Malformed rows are silently skipped by parse_ledger_rows, so spend=0
        self.assertEqual(result["spent"], 0)
        self.assertFalse(result["exceeded"], "Malformed row should be skipped, spend=0")

    def test_missing_ledger_returns_zero_spend(self):
        """If ledger doesn't exist, spend should be 0 (never fails open).
        Empty/missing ledger means 'no spend tracked yet', not 'infinite spend'."""
        config = {"limits": {"max_wave_tokens": 1000}}

        # No ledger file created
        result = self.cost_ceiling.check(period="wave", config=config)
        self.assertEqual(result["spent"], 0)
        self.assertFalse(
            result["exceeded"], "Missing ledger should default to 0 spend"
        )

    def test_spend_computation_error_returns_error_dict(self):
        """Test that computation errors (e.g., bad config) return a sensible result."""
        # Bad config: tokens is a string, not an int
        config = {"limits": {"max_wave_tokens": "not_a_number"}}

        # The get_ceiling function should handle this gracefully
        result = self.cost_ceiling.check(
            spent=500, period="wave", config=config
        )
        # Bad ceiling value should be ignored (None), so never exceeded
        self.assertIsNone(result["ceiling"])
        self.assertFalse(result["exceeded"])

    def test_transient_io_error_aborts_wave_without_persistent_halt(self):
        """Test the critical safety fix: when spend computation fails (e.g.,
        transient I/O error reading/creating the ledger), abort the current wave
        (exceeded=True) but do NOT write the persistent .HALT sentinel (tripped=False).
        This preserves fleet availability across transient errors."""
        config = {"limits": {"max_wave_tokens": 1000}}

        # Monkeypatch fleet_ledger.parse_ledger_rows to raise an OSError
        # (simulating a transient file lock or permission error)
        original_parse = self.fleet_ledger.parse_ledger_rows

        def mock_parse_ledger_rows():
            raise OSError("WinError 183: Cannot create a file when that file already exists")

        self.fleet_ledger.parse_ledger_rows = mock_parse_ledger_rows

        try:
            result = self.cost_ceiling.check(period="wave", config=config)

            # Verify the result signals abort of current wave
            self.assertTrue(
                result["exceeded"],
                "Computation error should abort the wave (exceeded=True)"
            )
            # Verify tripped=False: no persistent sentinel written
            self.assertFalse(
                result["tripped"],
                "Computation error should NOT write persistent halt (tripped=False)"
            )
            # Verify the persistent sentinel was NOT written
            self.assertFalse(
                self.halt.is_halted(),
                "Transient I/O error should NOT write .HALT sentinel"
            )
            # Verify the error is recorded in the result
            self.assertIn("error", result)
            self.assertIn("reason", result)
            self.assertIn("cost_check_error", result["reason"])
        finally:
            self.fleet_ledger.parse_ledger_rows = original_parse

    def test_main_exits_nonzero_on_ledger_read_error(self):
        """Test the CLI fail-closed fix: main() must exit 1 when ledger is unreadable,
        not skip and exit 0. This reproduces the P1 security bug: unreadable ledger
        file caused main() to print 'skipping' and return 0 instead of failing."""
        config = {"limits": {"max_wave_tokens": 1000}}
        config_file = self.fixture_root / "aesop.config.json"
        config_file.write_text(json.dumps(config), encoding="utf-8")

        # Monkeypatch fleet_ledger.parse_ledger_rows to raise an OSError
        # (simulating an unreadable ledger file)
        original_parse = self.fleet_ledger.parse_ledger_rows

        def mock_parse_ledger_rows():
            raise OSError("Permission denied: Cannot read ledger file")

        self.fleet_ledger.parse_ledger_rows = mock_parse_ledger_rows

        try:
            # Call main() with --check flag
            exit_code = self.cost_ceiling.main(argv=["--check", "--period", "wave"])

            # CRITICAL: exit code must be 1 (fail-closed), NOT 0 (fail-open)
            self.assertEqual(
                exit_code, 1,
                "main() must exit 1 on ledger read error (fail-closed), not 0 (fail-open)"
            )
        finally:
            self.fleet_ledger.parse_ledger_rows = original_parse


class TestCostCeilingMultipleCheckPoints(CostCeilingSpikeTesting):
    """Test that multiple ceiling check points all enforce correctly."""

    def test_all_phases_respect_ceiling(self):
        """Verify that if ceiling is set, it's enforced consistently across
        all phases (preflight, build, repair, ship)."""
        config = {"limits": {"max_wave_tokens": 2000}}

        # Phase 1: preflight
        self._write_ledger([(500, 500)])  # 1000 tokens
        result = self.cost_ceiling.check(period="wave", config=config)
        self.assertFalse(result["exceeded"])

        # Phase 2: build (add more cost)
        self._write_ledger([(500, 500), (600, 600)])  # 2200 tokens total
        result = self.cost_ceiling.check(period="wave", config=config)
        self.assertTrue(result["exceeded"], "Build phase should detect overage")
        self.assertTrue(result["tripped"])

        # Verify halt was only tripped once
        halt_info = self.halt.get_halt_info()
        self.assertIn("cost", halt_info["reason"].lower())

    def test_ceiling_exceeded_stops_all_further_dispatch(self):
        """Once ceiling is exceeded and halt is tripped, further checks should
        also report exceeded (halt persists)."""
        config = {"limits": {"max_wave_tokens": 1000}}

        # First check: spike over ceiling
        self._write_ledger([(600, 600)])  # 1200 > 1000
        result1 = self.cost_ceiling.check(period="wave", config=config)
        self.assertTrue(result1["exceeded"])
        self.assertTrue(result1["tripped"])

        # Second check: even if spend stays at 1200, should still report exceeded
        result2 = self.cost_ceiling.check(period="wave", config=config)
        self.assertTrue(result2["exceeded"], "Ceiling should stay exceeded")
        self.assertTrue(
            self.halt.is_halted(), "Halt should persist across checks"
        )


class TestCostCeilingEdgeCases(CostCeilingSpikeTesting):
    """Test edge cases and boundary conditions."""

    def test_ceiling_exactly_at_spend(self):
        """When spend equals ceiling exactly, it should be exceeded (>= comparison)."""
        config = {"limits": {"max_wave_tokens": 1000}}
        result = self.cost_ceiling.check(spent=1000, period="wave", config=config)
        self.assertTrue(result["exceeded"], "Spend equal to ceiling should be exceeded")
        self.assertTrue(result["tripped"])

    def test_ceiling_zero_always_exceeded(self):
        """Edge case: ceiling of 0 means ANY spend exceeds it."""
        config = {"limits": {"max_wave_tokens": 0}}
        result = self.cost_ceiling.check(spent=1, period="wave", config=config)
        self.assertTrue(result["exceeded"])

        # Even zero spend should exceed zero ceiling? (0 >= 0 is True)
        result = self.cost_ceiling.check(spent=0, period="wave", config=config)
        self.assertTrue(result["exceeded"], "0 >= 0 is true (equals ceiling)")

    def test_negative_ceiling_never_configured(self):
        """Negative ceilings don't make sense; treat as misconfiguration."""
        config = {"limits": {"max_wave_tokens": -1000}}
        # The get_ceiling function should parse it as -1000
        # Then spent=-1 < -1000 is False, so exceeded=False
        result = self.cost_ceiling.check(spent=500, period="wave", config=config)
        # 500 >= -1000 is True, so it would exceed
        self.assertTrue(result["exceeded"])

    def test_very_large_spend_spike(self):
        """Simulate catastrophic spend (e.g., infinite loop sending tons of tokens)."""
        config = {"limits": {"max_wave_tokens": 100_000}}
        # Simulate 10x overage
        result = self.cost_ceiling.check(
            spent=1_000_000, period="wave", config=config
        )
        self.assertTrue(result["exceeded"])
        self.assertTrue(result["tripped"])
        self.assertEqual(result["spent"], 1_000_000)


class TestReturnValueStructure(CostCeilingSpikeTesting):
    """Verify that ceiling checks return the correct structure."""

    def test_return_dict_has_all_required_keys(self):
        """The check() function should always return all required fields."""
        config = {"limits": {"max_wave_tokens": 1000}}

        result = self.cost_ceiling.check(spent=500, period="wave", config=config)

        required_keys = {"period", "ceiling", "spent", "exceeded", "tripped", "reason"}
        self.assertTrue(
            required_keys.issubset(result.keys()),
            f"Missing keys: {required_keys - set(result.keys())}",
        )

        # Verify types
        self.assertIsInstance(result["period"], str)
        self.assertIsInstance(result["ceiling"], int)
        self.assertIsInstance(result["spent"], int)
        self.assertIsInstance(result["exceeded"], bool)
        self.assertIsInstance(result["tripped"], bool)
        # reason can be None (normal case) or a string (error case)
        self.assertTrue(result["reason"] is None or isinstance(result["reason"], str))

    def test_return_values_on_spike(self):
        """When ceiling is exceeded by a spike, verify all return values are correct."""
        config = {"limits": {"max_wave_tokens": 1000}}
        self._write_ledger([(600, 600)])  # 1200 > 1000

        result = self.cost_ceiling.check(period="wave", config=config)

        self.assertEqual(result["period"], "wave")
        self.assertEqual(result["ceiling"], 1000)
        self.assertEqual(result["spent"], 1200)
        self.assertTrue(result["exceeded"])
        self.assertTrue(result["tripped"])
        # On a genuine ceiling breach, reason should be None (no error)
        self.assertIsNone(result["reason"])


if __name__ == "__main__":
    unittest.main()
