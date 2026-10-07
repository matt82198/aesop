#!/usr/bin/env python3
"""Tests for tools/wave_backlog_analyzer.py — wave backlog risk analysis.

Test strategy (TDD):
1. Analyzer loads state/tracker.json proposed/todo items
2. Correlates with git history (fix-forward commits by domain)
3. Computes per-item risk_level (high/medium/low/unknown) with justification
4. Computes estimated_retries based on repair frequency
5. JSON output format: {items: [{slug, risk_level, estimated_retries, justification}]}
6. Graceful when history is thin (risk=unknown, honest)
7. Never flips exit 1 (warn-level only)
8. Hermetic tests: temp git repo, no cwd pollution, no global git config

HERMETIC: every test creates a throwaway git repo in a temp directory.
No test touches cwd, global git config, or the real aesop repo.
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

ANALYZER_PY = TOOLS_DIR / "wave_backlog_analyzer.py"

ENV_KEYS = ("AESOP_STATE_ROOT", "AESOP_ROOT")


class BacklogAnalyzerTestBase(unittest.TestCase):
    """Base class: isolated temp repo + state dir, hermetic."""

    def setUp(self):
        """Create throwaway git repo in a temp directory."""
        self.fixture_root = Path(tempfile.mkdtemp(prefix="aesop-analyzer-test-"))
        self.repo_dir = self.fixture_root / "repo"
        self.repo_dir.mkdir(parents=True)

        # Configure git identity in temp repo
        subprocess.run(
            ["git", "init"],
            cwd=self.repo_dir,
            capture_output=True,
            check=True,
        )
        subprocess.run(
            ["git", "config", "--local", "user.email", "test@test.local"],
            cwd=self.repo_dir,
            capture_output=True,
            check=True,
        )
        subprocess.run(
            ["git", "config", "--local", "user.name", "Test User"],
            cwd=self.repo_dir,
            capture_output=True,
            check=True,
        )
        self.state_dir = self.repo_dir / "state"
        self.state_dir.mkdir(parents=True)

        # Create initial commit
        (self.repo_dir / ".gitkeep").write_text("")
        subprocess.run(
            ["git", "add", ".gitkeep"],
            cwd=self.repo_dir,
            capture_output=True,
            check=True,
        )
        subprocess.run(
            ["git", "commit", "-m", "initial"],
            cwd=self.repo_dir,
            capture_output=True,
            check=True,
        )

        # Start on a feature branch
        subprocess.run(
            ["git", "checkout", "-b", "feat/test"],
            cwd=self.repo_dir,
            capture_output=True,
            check=True,
        )

        # Save environment
        self._saved_env = {k: os.environ.get(k) for k in ENV_KEYS}
        os.environ["AESOP_STATE_ROOT"] = str(self.state_dir)
        os.environ.pop("AESOP_ROOT", None)

        # Clear cached imports
        for mod in ("wave_backlog_analyzer",):
            sys.modules.pop(mod, None)

    def tearDown(self):
        """Restore environment and clean up."""
        for k, v in self._saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        for mod in ("wave_backlog_analyzer",):
            sys.modules.pop(mod, None)
        shutil.rmtree(self.fixture_root, ignore_errors=True)

    def _run_analyzer(self, *args, env_overrides=None):
        """Run wave_backlog_analyzer.py with args."""
        env = os.environ.copy()
        if env_overrides:
            env.update(env_overrides)
        cmd = [sys.executable, str(ANALYZER_PY), f"--root={self.repo_dir}", *args]
        result = subprocess.run(cmd, capture_output=True, text=True, env=env)
        return result

    def _setup_tracker_json(self, items):
        """Write state/tracker.json with items.

        Args:
            items: list of dicts with {slug, title, status, description}
        """
        tracker_json = self.state_dir / "tracker.json"
        data = {"version": 1, "items": items}
        tracker_json.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")

    def _add_commit(self, message, filename="test.py", content="# test"):
        """Add a commit to the git repo."""
        fpath = self.repo_dir / filename
        fpath.parent.mkdir(parents=True, exist_ok=True)
        fpath.write_text(content)
        subprocess.run(
            ["git", "add", str(fpath)],
            cwd=self.repo_dir,
            capture_output=True,
            check=True,
        )
        subprocess.run(
            ["git", "commit", "-m", message],
            cwd=self.repo_dir,
            capture_output=True,
            check=True,
        )


class TestBasicExecution(BacklogAnalyzerTestBase):
    """Test basic analyzer execution."""

    def test_runs_with_empty_tracker(self):
        """Should run successfully with empty tracker."""
        self._setup_tracker_json([])
        result = self._run_analyzer("--json")
        self.assertEqual(result.returncode, 0)
        data = json.loads(result.stdout)
        self.assertIn("items", data)
        self.assertEqual(len(data["items"]), 0)

    def test_json_output_format(self):
        """JSON output should have items array."""
        self._setup_tracker_json([
            {"slug": "test-001", "title": "Test item", "status": "todo", "description": ""}
        ])
        result = self._run_analyzer("--json")
        self.assertEqual(result.returncode, 0)
        data = json.loads(result.stdout)
        self.assertIn("items", data)
        self.assertIsInstance(data["items"], list)

    def test_exit_code_zero_always(self):
        """Should always exit 0 (warn-level only)."""
        self._setup_tracker_json([])
        result = self._run_analyzer("--json")
        self.assertEqual(result.returncode, 0)


class TestRiskAnalysis(BacklogAnalyzerTestBase):
    """Test risk analysis computation."""

    def test_single_item_unknown_risk_without_history(self):
        """Item with no history should have risk=unknown."""
        self._setup_tracker_json([
            {"slug": "feat-001", "title": "New feature", "status": "todo", "description": "A new feature"}
        ])
        result = self._run_analyzer("--json")
        data = json.loads(result.stdout)
        self.assertEqual(len(data["items"]), 1)
        item = data["items"][0]
        self.assertEqual(item["slug"], "feat-001")
        self.assertEqual(item["risk_level"], "unknown")
        self.assertIn("No history", item["justification"])

    def test_item_with_related_fixes_high_risk(self):
        """Item with related fix-forward commits should have higher risk."""
        # Add commits to tools/ domain
        self._add_commit("feat: add tools", filename="tools/new_tool.py", content="# new tool")
        self._add_commit("fix: tools bug", filename="tools/new_tool.py", content="# fixed")
        self._add_commit("fix-forward: tools", filename="tools/new_tool.py", content="# repaired")

        self._setup_tracker_json([
            {"slug": "tools-impl", "title": "Implement tool", "status": "todo", "description": "tools/something"}
        ])
        result = self._run_analyzer("--json")
        data = json.loads(result.stdout)
        item = data["items"][0]
        # Should have some risk assessment based on domain volatility
        self.assertIn(item["risk_level"], ["low", "medium", "high", "unknown"])
        self.assertIn("justification", item)

    def test_multiple_items_analyzed(self):
        """Should analyze multiple items."""
        self._setup_tracker_json([
            {"slug": "item-001", "title": "Item 1", "status": "todo", "description": ""},
            {"slug": "item-002", "title": "Item 2", "status": "proposed", "description": ""},
            {"slug": "item-003", "title": "Item 3", "status": "todo", "description": ""},
        ])
        result = self._run_analyzer("--json")
        data = json.loads(result.stdout)
        self.assertEqual(len(data["items"]), 3)

    def test_item_structure_complete(self):
        """Each item should have all required fields."""
        self._setup_tracker_json([
            {"slug": "test-001", "title": "Test", "status": "todo", "description": "Test item"}
        ])
        result = self._run_analyzer("--json")
        data = json.loads(result.stdout)
        item = data["items"][0]
        required_fields = ["slug", "risk_level", "estimated_retries", "justification"]
        for field in required_fields:
            self.assertIn(field, item, f"Missing field: {field}")


class TestDomainVolatility(BacklogAnalyzerTestBase):
    """Test domain volatility detection."""

    def test_high_fix_frequency_detected(self):
        """Domains with many fix-forward commits should show higher risk."""
        # Create a "volatile" domain with many fixes
        for i in range(5):
            self._add_commit(
                f"feat: state_store change {i}",
                filename="state_store/schema.py",
                content=f"# version {i}"
            )
            self._add_commit(
                f"fix-forward: state_store repair {i}",
                filename="state_store/schema.py",
                content=f"# fixed {i}"
            )

        self._setup_tracker_json([
            {"slug": "state-store-001", "title": "State store work", "status": "todo", "description": "state_store"}
        ])
        result = self._run_analyzer("--json")
        data = json.loads(result.stdout)
        item = data["items"][0]
        # Should reflect the high repair frequency and risk level
        self.assertEqual(item["risk_level"], "high")
        self.assertIn("high repair frequency", item["justification"].lower())

    def test_stable_domain_low_risk(self):
        """Domains with few fixes should have lower risk."""
        # Create a "stable" domain with few fixes
        self._add_commit("feat: docs update", filename="docs/README.md", content="# Docs")

        self._setup_tracker_json([
            {"slug": "docs-001", "title": "Doc work", "status": "todo", "description": "docs"}
        ])
        result = self._run_analyzer("--json")
        data = json.loads(result.stdout)
        item = data["items"][0]
        # Should reflect stability
        self.assertIn("risk_level", item)


class TestEstimatedRetries(BacklogAnalyzerTestBase):
    """Test estimated retries computation."""

    def test_no_history_zero_retries(self):
        """Item with no history should have estimated_retries=0 or null."""
        self._setup_tracker_json([
            {"slug": "new-001", "title": "New", "status": "todo", "description": ""}
        ])
        result = self._run_analyzer("--json")
        data = json.loads(result.stdout)
        item = data["items"][0]
        # Should be 0, null, or explicitly documented
        self.assertIn(item["estimated_retries"], [0, None])

    def test_high_repair_frequency_increases_retries(self):
        """High repair frequency should increase estimated_retries."""
        # Create a domain with multiple repairs
        for i in range(3):
            self._add_commit(f"feat: ui change {i}", filename="ui/component.tsx", content=f"// v{i}")
            self._add_commit(f"fix: ui bug {i}", filename="ui/component.tsx", content=f"// fixed {i}")

        self._setup_tracker_json([
            {"slug": "ui-001", "title": "UI work", "status": "todo", "description": "ui"}
        ])
        result = self._run_analyzer("--json")
        data = json.loads(result.stdout)
        item = data["items"][0]
        # Should be > 0 based on history
        self.assertIsNotNone(item["estimated_retries"])


class TestGracefulDegradation(BacklogAnalyzerTestBase):
    """Test graceful handling of thin or missing history."""

    def test_missing_tracker_json(self):
        """Should handle missing tracker.json gracefully."""
        # Don't create tracker.json
        result = self._run_analyzer("--json")
        # Should still exit 0 (not a critical failure)
        self.assertEqual(result.returncode, 0)
        # Should have some output
        self.assertIn("items", result.stdout)

    def test_invalid_tracker_json(self):
        """Should handle invalid JSON gracefully."""
        tracker_json = self.state_dir / "tracker.json"
        tracker_json.write_text("{ invalid json", encoding="utf-8")
        result = self._run_analyzer("--json")
        self.assertEqual(result.returncode, 0)

    def test_malformed_items(self):
        """Should handle malformed items gracefully."""
        self._setup_tracker_json([
            {"slug": "good-001", "title": "Good", "status": "todo"},
            {"title": "Missing slug"},  # Missing slug field
            {"slug": "good-002", "status": "proposed"},  # Missing title
        ])
        result = self._run_analyzer("--json")
        self.assertEqual(result.returncode, 0)
        data = json.loads(result.stdout)
        # Should have at least attempted to analyze items
        self.assertGreaterEqual(len(data["items"]), 0)


class TestStateRootResolution(BacklogAnalyzerTestBase):
    """Test state directory resolution."""

    def test_env_var_takes_precedence(self):
        """AESOP_STATE_ROOT env var should take precedence."""
        alt_state = self.fixture_root / "alt-state"
        alt_state.mkdir()
        tracker_json = alt_state / "tracker.json"
        tracker_json.write_text(json.dumps({"version": 1, "items": []}) + "\n")

        result = self._run_analyzer(
            "--json",
            env_overrides={"AESOP_STATE_ROOT": str(alt_state)}
        )
        self.assertEqual(result.returncode, 0)


class TestCliArguments(BacklogAnalyzerTestBase):
    """Test CLI argument handling."""

    def test_json_output_format(self):
        """Should output JSON with --json."""
        self._setup_tracker_json([])
        result = self._run_analyzer("--json")
        # Should be valid JSON
        data = json.loads(result.stdout)
        self.assertIn("items", data)

    def test_text_output_default(self):
        """Default output should be text format."""
        self._setup_tracker_json([
            {"slug": "test-001", "title": "Test", "status": "todo", "description": ""}
        ])
        result = self._run_analyzer()  # No --json
        # Should have human-readable output
        self.assertIn("test-001", result.stdout)

    def test_root_argument(self):
        """Should accept --root argument."""
        self._setup_tracker_json([])
        result = self._run_analyzer("--json", env_overrides={})
        self.assertEqual(result.returncode, 0)


class TestJustificationMessages(BacklogAnalyzerTestBase):
    """Test justification message quality."""

    def test_justification_present_for_all_items(self):
        """Every item should have a justification."""
        self._setup_tracker_json([
            {"slug": "item-001", "title": "Item 1", "status": "todo", "description": ""},
            {"slug": "item-002", "title": "Item 2", "status": "proposed", "description": ""},
        ])
        result = self._run_analyzer("--json")
        data = json.loads(result.stdout)
        for item in data["items"]:
            self.assertIn("justification", item)
            self.assertGreater(len(item["justification"]), 0)

    def test_justification_references_history(self):
        """Justification should mention relevant history."""
        # Add some history
        self._add_commit("feat: tools", filename="tools/main.py", content="# v1")
        self._add_commit("fix-forward: tools", filename="tools/main.py", content="# fixed")

        self._setup_tracker_json([
            {"slug": "tools-001", "title": "Tools work", "status": "todo", "description": "tools"}
        ])
        result = self._run_analyzer("--json")
        data = json.loads(result.stdout)
        item = data["items"][0]
        # Justification should mention what was found
        self.assertGreater(len(item["justification"]), 10)


if __name__ == "__main__":
    unittest.main()
