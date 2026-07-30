#!/usr/bin/env python3
"""TDD tests for cost economics metrics.

Tests cover:
- cost-per-LOC: total tokens / lines of code
- cost-per-merged-PR: total tokens / merged PRs
- cost-per-wave: total tokens / wave count
- Unit cost economics: cost-per-backlog-item, cost-per-passing-test

Metrics are derived from:
- LOC from git (self_stats.py)
- Tokens from ledger (OUTCOMES-LEDGER.md)
- Pricing from config (ui/cost.py shared pricing model)

Run: python -m unittest tests.test_cost_econ -v
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from datetime import datetime, timezone

# Add tools directory to path
TOOLS_DIR = Path(__file__).parent.parent / "tools"
UI_DIR = Path(__file__).parent.parent / "ui"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))
if str(UI_DIR) not in sys.path:
    sys.path.insert(0, str(UI_DIR))


class CostEconTestCase(unittest.TestCase):
    """Base fixture: tiny repo with git stats, ledger, and config."""

    def setUp(self):
        self.fixture_root = Path(tempfile.mkdtemp(prefix="aesop-costecon-test-"))
        self.repo_root = self.fixture_root / "testrepo"
        self.repo_root.mkdir(parents=True)

        # Create state directory with ledger
        self.state_dir = self.repo_root / "state" / "ledger"
        self.state_dir.mkdir(parents=True)

        # Create config directory
        (self.repo_root / ".claude").mkdir(parents=True, exist_ok=True)

        # Initialize git repo
        subprocess.run(["git", "init"], cwd=str(self.repo_root), capture_output=True, check=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=str(self.repo_root), capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test User"], cwd=str(self.repo_root), capture_output=True)
        # Set default branch to main
        subprocess.run(["git", "checkout", "-b", "main"], cwd=str(self.repo_root), capture_output=True)

        self._saved_cwd = os.getcwd()

    def tearDown(self):
        os.chdir(self._saved_cwd)
        shutil.rmtree(self.fixture_root, ignore_errors=True)

    def make_commit_with_loc(self, msg, num_lines=100, coauthor=None):
        """Create a commit with specified number of lines."""
        import uuid
        # Use unique filename to avoid conflicts
        unique_id = uuid.uuid4().hex[:8]
        test_file = self.repo_root / f"file_{msg.replace(' ', '_').replace(':', '')}_{unique_id}.py"
        content = "\n".join([f"# Line {i}" for i in range(num_lines)])
        test_file.write_text(content)

        subprocess.run(["git", "add", "."], cwd=str(self.repo_root), capture_output=True, check=True)

        commit_msg = msg
        if coauthor:
            commit_msg += f"\n\nCo-Authored-By: {coauthor}"

        result = subprocess.run(
            ["git", "commit", "-m", commit_msg],
            cwd=str(self.repo_root),
            capture_output=True,
            text=True
        )
        if result.returncode != 0:
            # If commit failed, it might be because nothing was staged
            # Try to add and commit again
            subprocess.run(["git", "add", str(test_file)], cwd=str(self.repo_root), capture_output=True)
            subprocess.run(
                ["git", "commit", "-m", commit_msg],
                cwd=str(self.repo_root),
                capture_output=True,
                check=True
            )

    def make_ledger_entry(self, timestamp, model, tokens_in, tokens_out, verdict="OK"):
        """Add entry to OUTCOMES-LEDGER.md."""
        ledger_file = self.state_dir / "OUTCOMES-LEDGER.md"

        # Create header if doesn't exist
        if not ledger_file.exists():
            ledger_file.write_text(
                "| timestamp | agent_type | model | duration | tokens_in | tokens_out | verdict |\n"
                "|---|---|---|---|---|---|---|\n"
            )

        # Append entry
        entry = f"| {timestamp} | Agent | {model} | 0 | {tokens_in} | {tokens_out} | {verdict} |\n"
        with open(ledger_file, "a", encoding="utf-8") as f:
            f.write(entry)

    def set_config_pricing(self, pricing_map):
        """Set pricing config in aesop.config.json."""
        config_file = self.repo_root / "aesop.config.json"
        config_data = {
            "state_root": str(self.state_dir.parent),
            "pricing": pricing_map
        }
        config_file.write_text(json.dumps(config_data, indent=2))


class TestCostPerLOC(CostEconTestCase):
    """Test cost-per-LOC metric: total tokens / lines of code."""

    def test_cost_per_loc_calculation(self):
        """Calculate cost-per-LOC from git stats and tokens."""
        # Create repo with known LOC
        self.make_commit_with_loc("initial", num_lines=100)
        self.make_commit_with_loc("feature", num_lines=50)

        # Create ledger with known tokens
        self.make_ledger_entry("2026-07-21T00:00:00", "claude-haiku-4-5-20251001", 1000, 2000)
        self.make_ledger_entry("2026-07-21T01:00:00", "claude-haiku-4-5-20251001", 500, 1000)

        # Expected: LOC = 150 (from first 2 commits)
        # Expected: total_tokens = (1000 + 2000) + (500 + 1000) = 4500
        # Expected: cost_per_loc = 4500 / 150 = 30 tokens per LOC

        # Import after setup to ensure proper patching
        from tools import cost_econ

        metrics = cost_econ.calculate_economics(str(self.repo_root))

        self.assertIn("cost_per_loc", metrics)
        self.assertEqual(metrics["cost_per_loc"]["total_tokens"], 4500)
        self.assertEqual(metrics["cost_per_loc"]["lines_of_code"], 150)
        self.assertAlmostEqual(metrics["cost_per_loc"]["tokens_per_loc"], 30.0, places=1)

    def test_cost_per_loc_with_empty_ledger(self):
        """No ledger data: token/cost fields must be OMITTED, never emitted as 0.0 filler."""
        self.make_commit_with_loc("initial", num_lines=100)

        from tools import cost_econ

        metrics = cost_econ.calculate_economics(str(self.repo_root))

        self.assertIs(metrics.get("token_ledger_available"), False,
                      "must explicitly mark token ledger unavailable")
        # Denominators are still honest git facts
        self.assertEqual(metrics.get("lines_of_code"), 100)
        # Token ratio fields must not exist anywhere in the output
        flat = json.dumps(metrics)
        for filler_key in ("tokens_per_loc", "tokens_per_pr", "tokens_per_wave",
                           "cost_per_backlog_item"):
            self.assertNotIn(filler_key, flat,
                             f"'{filler_key}' must be omitted when unmeasured")


class TestCostPerMergedPR(CostEconTestCase):
    """Test cost-per-merged-PR metric."""

    def test_cost_per_merged_pr(self):
        """Calculate cost per merged PR."""
        # Create merge commits (mimics GitHub)
        self.make_commit_with_loc("feat: initial", num_lines=100)

        # Create a branch and merge it
        subprocess.run(
            ["git", "checkout", "-b", "feature"],
            cwd=str(self.repo_root),
            capture_output=True,
            check=True
        )
        self.make_commit_with_loc("feat: add feature", num_lines=50)
        subprocess.run(
            ["git", "checkout", "main"],
            cwd=str(self.repo_root),
            capture_output=True,
            check=True
        )

        # Create merge commit (must name the ref being merged)
        result = subprocess.run(
            ["git", "merge", "--no-ff", "feature", "-m",
             "Merge pull request #1 from test/feature"],
            cwd=str(self.repo_root),
            capture_output=True,
            text=True
        )
        if result.returncode != 0:
            # Handle merge conflicts or issues gracefully
            subprocess.run(
                ["git", "merge", "--abort"],
                cwd=str(self.repo_root),
                capture_output=True
            )

        # Add ledger data
        self.make_ledger_entry("2026-07-21T00:00:00", "claude-haiku-4-5-20251001", 1000, 2000)

        from tools import cost_econ

        metrics = cost_econ.calculate_economics(str(self.repo_root))

        self.assertIn("cost_per_merged_pr", metrics)
        self.assertGreater(metrics["cost_per_merged_pr"]["merged_prs"], 0)
        self.assertEqual(metrics["cost_per_merged_pr"]["total_tokens"], 3000)


class TestCostPerWave(CostEconTestCase):
    """Test cost-per-wave metric."""

    def test_cost_per_wave(self):
        """Calculate cost per wave."""
        self.make_commit_with_loc("wave-1: initial", num_lines=100)
        self.make_commit_with_loc("wave-2: feature", num_lines=50)

        # Add ledger data
        self.make_ledger_entry("2026-07-21T00:00:00", "claude-haiku-4-5-20251001", 1000, 2000)
        self.make_ledger_entry("2026-07-21T01:00:00", "claude-haiku-4-5-20251001", 500, 1000)

        from tools import cost_econ

        metrics = cost_econ.calculate_economics(str(self.repo_root))

        self.assertIn("cost_per_wave", metrics)
        self.assertGreater(metrics["cost_per_wave"]["wave_count"], 0)
        self.assertEqual(metrics["cost_per_wave"]["total_tokens"], 4500)


class TestUnitCostEconomics(CostEconTestCase):
    """Test unit cost economics: cost per backlog item, cost per passing test."""

    def test_cost_per_backlog_item(self):
        """Calculate cost per backlog item (PRs as proxy)."""
        self.make_commit_with_loc("wave-1: item 1", num_lines=100)
        self.make_commit_with_loc("wave-2: item 2", num_lines=50)

        self.make_ledger_entry("2026-07-21T00:00:00", "claude-haiku-4-5-20251001", 1000, 2000)

        from tools import cost_econ

        metrics = cost_econ.calculate_economics(str(self.repo_root))

        self.assertIn("unit_economics", metrics)
        self.assertIn("cost_per_backlog_item", metrics["unit_economics"])
        # Cost per backlog item = total tokens / merged PRs or waves (fallback)
        self.assertGreater(metrics["unit_economics"]["cost_per_backlog_item"], 0)

    def test_cost_with_pricing(self):
        """Calculate cost in dollars using pricing config."""
        self.make_commit_with_loc("initial", num_lines=100)

        # Set up pricing: Haiku = $0.80 per 1M input tokens, $4 per 1M output tokens
        pricing_map = {
            "claude-haiku-4-5-20251001": {
                "input_per_mtok": 0.80,
                "output_per_mtok": 4.0
            }
        }
        self.set_config_pricing(pricing_map)

        # Add ledger: 1M input tokens, 1M output tokens
        self.make_ledger_entry("2026-07-21T00:00:00", "claude-haiku-4-5-20251001", 1000000, 1000000)

        from tools import cost_econ

        config_file_path = str(self.repo_root / "aesop.config.json")
        metrics = cost_econ.calculate_economics(str(self.repo_root), config_file=config_file_path)

        self.assertIn("cost_estimates", metrics)
        # Expected cost = (1M * $0.80/1M) + (1M * $4.0/1M) = $0.80 + $4.00 = $4.80
        total_cost = metrics["cost_estimates"].get("total_cost_dollars", 0)
        self.assertAlmostEqual(total_cost, 4.80, places=1)


class TestEconomicsMetricsInStatsJson(unittest.TestCase):
    """Test integration of economics metrics into stats.json."""

    def test_stats_json_structure_includes_economics(self):
        """calculate_economics returns the honest contract in both availability states."""
        from tools import cost_econ

        metrics = cost_econ.calculate_economics(".")

        # Availability flag and single-sourced PR provenance are always present
        self.assertIn("token_ledger_available", metrics)
        self.assertIn("merged_prs_source", metrics)

        if metrics["token_ledger_available"]:
            # Full structure with measured token data
            self.assertIn("cost_per_loc", metrics)
            self.assertIn("cost_per_merged_pr", metrics)
            self.assertIn("cost_per_wave", metrics)
            self.assertIn("unit_economics", metrics)
            self.assertIn("tokens_per_loc", metrics["cost_per_loc"])
            self.assertIn("tokens_per_pr", metrics["cost_per_merged_pr"])
            self.assertIn("cost_per_backlog_item", metrics["unit_economics"])
            self.assertGreater(metrics["cost_per_loc"]["total_tokens"], 0,
                               "ledger marked available must mean measured tokens > 0")
        else:
            # Slim honest structure: denominators only, no 0.0 token filler
            self.assertIn("merged_prs", metrics)
            self.assertIn("lines_of_code", metrics)
            self.assertIn("wave_count", metrics)
            flat = json.dumps(metrics)
            for filler_key in ("tokens_per_loc", "tokens_per_pr", "tokens_per_wave"):
                self.assertNotIn(filler_key, flat)


class TestSingleSourceMergedPrCount(CostEconTestCase):
    """Economics must consume the caller-provided merged-PR count (single source),
    and its own fallback must use the SAME definition as self_stats (distinct PR
    numbers from merge-commit AND squash-merge subjects)."""

    def test_merged_prs_override_is_used_verbatim(self):
        self.make_commit_with_loc("initial", num_lines=50)
        self.make_ledger_entry("2026-07-21T00:00:00", "claude-haiku-4-5-20251001", 1000, 2000)

        from tools import cost_econ

        metrics = cost_econ.calculate_economics(
            str(self.repo_root), merged_prs=123, merged_prs_source="gh-api"
        )

        self.assertEqual(metrics["cost_per_merged_pr"]["merged_prs"], 123)
        self.assertEqual(metrics["merged_prs_source"], "gh-api")
        # unit_economics must use the same count, not recompute
        self.assertEqual(metrics["unit_economics"]["items_count"], 123)

    def test_fallback_counts_distinct_prs_across_merge_and_squash(self):
        """Fallback definition must match self_stats: union of both patterns, deduped."""
        self.make_commit_with_loc("feat: initial", num_lines=10)

        # Merge-commit style PR #1
        subprocess.run(["git", "checkout", "-b", "feature"], cwd=str(self.repo_root),
                       capture_output=True, check=True)
        self.make_commit_with_loc("feat: branch work", num_lines=10)
        subprocess.run(["git", "checkout", "main"], cwd=str(self.repo_root),
                       capture_output=True, check=True)
        subprocess.run(
            ["git", "merge", "--no-ff", "feature", "-m", "Merge pull request #1 from test/feature"],
            cwd=str(self.repo_root), capture_output=True, check=True,
        )
        # Squash-merge style PR #2
        self.make_commit_with_loc("feat: squashed work (#2)", num_lines=10)

        self.make_ledger_entry("2026-07-21T00:00:00", "claude-haiku-4-5-20251001", 500, 500)

        from tools import cost_econ

        metrics = cost_econ.calculate_economics(str(self.repo_root))

        self.assertEqual(metrics["cost_per_merged_pr"]["merged_prs"], 2,
                         "fallback must count merge-commit AND squash PRs (union, deduped)")
        self.assertEqual(metrics["merged_prs_source"], "git-log")


class TestHonestyDocumentation(unittest.TestCase):
    """Test honesty caveats for metrics."""

    def test_cost_per_loc_honesty_document(self):
        """Verify honesty documentation about what cost-per-LOC does/doesn't capture."""
        from tools import cost_econ

        honesty = cost_econ.get_metric_honesty_caveats()

        self.assertIn("cost_per_loc", honesty)

        # Should document limitations
        caveat = honesty["cost_per_loc"]
        self.assertIn("does not include", caveat.lower())
        self.assertIn("fable orchestrator", caveat.lower())
        self.assertIn("ledger", caveat.lower())

    def test_unit_economics_honesty_document(self):
        """Verify honesty documentation about unit cost economics."""
        from tools import cost_econ

        honesty = cost_econ.get_metric_honesty_caveats()

        self.assertIn("unit_economics", honesty)
        self.assertIn("cost per backlog", honesty["unit_economics"].lower())


if __name__ == "__main__":
    unittest.main()
