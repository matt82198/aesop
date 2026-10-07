"""Health score tool tests — deterministic readiness scoring for primed projects.

Test strategy (TDD):
1. Create isolated temp fixture repos with varying readiness states
2. Test per-check scoring (weighted): config, hooks, CLAUDE.md, state-writable, heartbeats, git identity, secret-scan
3. Test score calculation (0-100 weighted roll-up)
4. Test JSON + human output formats
5. Test edge cases: missing files, broken JSON, unreadable paths, identity not set
6. Test integration with existing doctor/healthcheck logic
"""
import json
import os
import subprocess
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from time import time

UI_DIR = Path(__file__).parent.parent / "ui"
if str(UI_DIR) not in sys.path:
    sys.path.insert(0, str(UI_DIR))

TOOLS_DIR = Path(__file__).parent.parent / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

ENV_KEYS = (
    "AESOP_ROOT",
    "AESOP_STATE_ROOT",
    "AESOP_TRANSCRIPTS_ROOT",
    "AESOP_UI_COLLECT_INTERVAL",
    "GIT_AUTHOR_NAME",
    "GIT_AUTHOR_EMAIL",
    "GIT_COMMITTER_NAME",
    "GIT_COMMITTER_EMAIL",
)


class HealthScoreTestCase(unittest.TestCase):
    """Base class for health-score tests with isolated temp fixture."""

    def setUp(self):
        """Set up isolated temp fixture repo for testing."""
        self.fixture_root = Path(tempfile.mkdtemp(prefix="aesop-health-score-test-"))
        self.state_dir = self.fixture_root / "state"
        self.state_dir.mkdir(parents=True)
        (self.fixture_root / "transcripts").mkdir()

        # Save original cwd BEFORE any chdir (restoration must return to the
        # caller's cwd, never the fixture's — poisoned-cwd hygiene rule)
        self._saved_cwd = os.getcwd()

        # Initialize a git repo in fixture — all git identity mutation scoped
        # to the temp repo via cwd=, never ambient (hygiene-scanner enforced)
        subprocess.run(["git", "init", "-q"], cwd=str(self.fixture_root), check=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"],
                       cwd=str(self.fixture_root), check=True)
        subprocess.run(["git", "config", "user.name", "Test User"],
                       cwd=str(self.fixture_root), check=True)
        os.chdir(self.fixture_root)

        # Save original env
        self._saved_env = {k: os.environ.get(k) for k in ENV_KEYS}

        # Set isolated environment
        os.environ["AESOP_ROOT"] = str(self.fixture_root)
        os.environ["AESOP_STATE_ROOT"] = str(self.state_dir)
        os.environ["AESOP_TRANSCRIPTS_ROOT"] = str(self.fixture_root / "transcripts")
        os.environ["AESOP_UI_COLLECT_INTERVAL"] = "0.2"

    def tearDown(self):
        """Restore original env and clean up temp files."""
        try:
            os.chdir(self._saved_cwd)
        except Exception:
            pass

        for k, v in self._saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

        shutil.rmtree(self.fixture_root, ignore_errors=True)

    def _write_config(self, valid=True):
        """Write aesop.config.json."""
        config_path = self.fixture_root / "aesop.config.json"
        if valid:
            config = {
                "version": 1,
                "fleet_name": "test-fleet",
                "repos": [str(self.fixture_root)],
                "orchestrator_root": str(self.fixture_root)
            }
            config_path.write_text(json.dumps(config, indent=2), encoding="utf-8")
        else:
            config_path.write_text("{invalid json", encoding="utf-8")
        return config_path

    def _create_directory(self, name):
        """Create a required directory."""
        d = self.fixture_root / name
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _write_pre_push_hook(self):
        """Create git pre-push hook."""
        hooks_dir = self.fixture_root / ".git" / "hooks"
        hooks_dir.mkdir(parents=True, exist_ok=True)
        hook_path = hooks_dir / "pre-push"
        hook_path.write_text("#!/bin/bash\nexit 0\n", encoding="utf-8")
        hook_path.chmod(0o755)
        return hook_path

    def _write_claude_md(self, valid=True):
        """Write CLAUDE.md file."""
        claude_path = self.fixture_root / "CLAUDE.md"
        if valid:
            claude_path.write_text("# Test Project\n\nThis is a test.\n", encoding="utf-8")
        else:
            claude_path.write_text("", encoding="utf-8")
        return claude_path

    def _write_heartbeat(self, name, age_seconds=0):
        """Write a heartbeat file with given age."""
        heartbeat_file = self.state_dir / f".{name}-heartbeat"
        epoch = int(time()) - age_seconds
        heartbeat_file.write_text(str(epoch), encoding="utf-8")
        return heartbeat_file


class TestHealthScorePerfect(HealthScoreTestCase):
    """Tests for perfect/high health scores."""

    def test_perfect_score_all_checks_pass(self):
        """All checks passing yields score near 100."""
        import sys
        if "health_score" in sys.modules:
            del sys.modules["health_score"]
        import health_score

        # Set up perfect fixture
        self._write_config(valid=True)
        self._create_directory("daemons")
        self._create_directory("dash")
        self._create_directory("monitor")
        self._create_directory("tools")
        self._create_directory("ui")
        self._write_pre_push_hook()
        self._write_claude_md(valid=True)
        self._write_heartbeat("watchdog", age_seconds=10)
        self._write_heartbeat("monitor", age_seconds=20)

        # Git identity already scoped to the fixture repo in setUp()
        result = health_score.calculate_score(cwd=str(self.fixture_root))

        # Should be dict with score and checks
        self.assertIsInstance(result, dict)
        self.assertIn("score", result)
        self.assertGreater(result["score"], 80)  # At least 80/100
        self.assertIn("checks", result)
        self.assertIsInstance(result["checks"], list)

    def test_score_json_output_format(self):
        """Score output in JSON format is well-formed."""
        import sys
        if "health_score" in sys.modules:
            del sys.modules["health_score"]
        import health_score

        self._write_config(valid=True)
        self._create_directory("daemons")
        self._create_directory("dash")
        self._create_directory("monitor")
        self._create_directory("tools")
        self._create_directory("ui")
        self._write_pre_push_hook()
        self._write_claude_md(valid=True)
        self._write_heartbeat("watchdog", age_seconds=10)

        result = health_score.calculate_score(cwd=str(self.fixture_root))
        json_output = health_score.format_json(result)

        # Ensure it's valid JSON
        try:
            data = json.loads(json_output)
            self.assertIn("score", data)
            self.assertIn("checks", data)
        except json.JSONDecodeError as e:
            self.fail(f"JSON output not valid: {e}\n{json_output}")

    def test_score_human_output_format(self):
        """Score output in human format is readable."""
        import sys
        if "health_score" in sys.modules:
            del sys.modules["health_score"]
        import health_score

        self._write_config(valid=True)
        self._create_directory("daemons")
        self._create_directory("dash")
        self._create_directory("monitor")
        self._create_directory("tools")
        self._create_directory("ui")
        self._write_pre_push_hook()
        self._write_claude_md(valid=True)

        result = health_score.calculate_score(cwd=str(self.fixture_root))
        human_output = health_score.format_human(result)

        # Should contain score and check results
        self.assertIn("Health Score", human_output)
        self.assertIn("/100", human_output)
        self.assertIn("[PASS]", human_output)  # At least one passing check


class TestHealthScorePartial(HealthScoreTestCase):
    """Tests for partial health scores."""

    def test_missing_config_reduces_score(self):
        """Missing or invalid config reduces score."""
        import sys
        if "health_score" in sys.modules:
            del sys.modules["health_score"]
        import health_score

        # No config file
        self._create_directory("daemons")
        self._create_directory("dash")
        self._create_directory("monitor")
        self._create_directory("tools")
        self._create_directory("ui")
        self._write_pre_push_hook()
        self._write_claude_md(valid=True)

        result = health_score.calculate_score(cwd=str(self.fixture_root))

        # Score should be lower due to missing config
        self.assertIn("score", result)
        self.assertLess(result["score"], 100)

        # Check should show config as failing
        config_check = [c for c in result.get("checks", []) if "config" in c.get("name", "").lower()]
        if config_check:
            self.assertFalse(config_check[0].get("passed", True))

    def test_missing_directories_reduces_score(self):
        """Missing required directories reduce score."""
        import sys
        if "health_score" in sys.modules:
            del sys.modules["health_score"]
        import health_score

        self._write_config(valid=True)
        # Create only some directories (not all required)
        self._create_directory("daemons")
        self._create_directory("dash")

        result = health_score.calculate_score(cwd=str(self.fixture_root))

        self.assertIn("score", result)
        self.assertLess(result["score"], 100)

    def test_missing_hook_reduces_score(self):
        """Missing pre-push hook reduces score."""
        import sys
        if "health_score" in sys.modules:
            del sys.modules["health_score"]
        import health_score

        self._write_config(valid=True)
        self._create_directory("daemons")
        self._create_directory("dash")
        self._create_directory("monitor")
        self._create_directory("tools")
        self._create_directory("ui")
        # Don't create pre-push hook

        result = health_score.calculate_score(cwd=str(self.fixture_root))

        self.assertIn("score", result)
        self.assertLess(result["score"], 100)

        hook_check = [c for c in result.get("checks", []) if "hook" in c.get("name", "").lower()]
        if hook_check:
            self.assertFalse(hook_check[0].get("passed", True))

    def test_missing_claude_md_reduces_score(self):
        """Missing CLAUDE.md reduces score."""
        import sys
        if "health_score" in sys.modules:
            del sys.modules["health_score"]
        import health_score

        self._write_config(valid=True)
        self._create_directory("daemons")
        self._create_directory("dash")
        self._create_directory("monitor")
        self._create_directory("tools")
        self._create_directory("ui")
        self._write_pre_push_hook()
        # Don't write CLAUDE.md

        result = health_score.calculate_score(cwd=str(self.fixture_root))

        self.assertIn("score", result)
        # Check should show CLAUDE.md as failing
        claude_check = [c for c in result.get("checks", []) if "claude" in c.get("name", "").lower()]
        if claude_check:
            self.assertFalse(claude_check[0].get("passed", True))

    def test_stale_heartbeats_reduce_score(self):
        """Stale heartbeats reduce score."""
        import sys
        if "health_score" in sys.modules:
            del sys.modules["health_score"]
        import health_score

        self._write_config(valid=True)
        self._create_directory("daemons")
        self._create_directory("dash")
        self._create_directory("monitor")
        self._create_directory("tools")
        self._create_directory("ui")
        self._write_pre_push_hook()
        self._write_claude_md(valid=True)
        # Stale heartbeats (>300s old)
        self._write_heartbeat("watchdog", age_seconds=600)

        result = health_score.calculate_score(cwd=str(self.fixture_root))

        self.assertIn("score", result)
        # Score should be reduced due to stale heartbeat
        heartbeat_check = [c for c in result.get("checks", []) if "heartbeat" in c.get("name", "").lower()]
        if heartbeat_check:
            self.assertFalse(heartbeat_check[0].get("passed", True))


class TestHealthScoreWeighting(HealthScoreTestCase):
    """Tests for score weighting logic."""

    def test_critical_checks_weighted_higher(self):
        """Critical checks (config, hooks) weighted more than informational checks."""
        import sys
        if "health_score" in sys.modules:
            del sys.modules["health_score"]
        import health_score

        # Set up with config missing (critical) but other things present
        self._create_directory("daemons")
        self._create_directory("dash")
        self._create_directory("monitor")
        self._create_directory("tools")
        self._create_directory("ui")
        self._write_pre_push_hook()
        self._write_claude_md(valid=True)

        result = health_score.calculate_score(cwd=str(self.fixture_root))
        score_missing_config = result["score"]

        # Now add config but remove hook (also critical)
        self._write_config(valid=True)
        (self.fixture_root / ".git" / "hooks" / "pre-push").unlink()

        result2 = health_score.calculate_score(cwd=str(self.fixture_root))
        score_missing_hook = result2["score"]

        # Both missing critical check should yield similar penalties
        # (exact equality not required, but ballpark should be same range)
        self.assertTrue(
            abs(score_missing_config - score_missing_hook) < 20,
            f"Config penalty {100 - score_missing_config} vs hook penalty {100 - score_missing_hook} should be similar"
        )


class TestHealthScoreEdgeCases(HealthScoreTestCase):
    """Tests for edge cases and error handling."""

    def test_handles_nonexistent_cwd(self):
        """Gracefully handles non-existent working directory."""
        import sys
        if "health_score" in sys.modules:
            del sys.modules["health_score"]
        import health_score

        nonexistent = str(self.fixture_root / "nonexistent")
        result = health_score.calculate_score(cwd=nonexistent)

        self.assertIsInstance(result, dict)
        self.assertIn("score", result)
        self.assertLess(result["score"], 50)  # Should be low score for missing dir

    def test_handles_invalid_config_json(self):
        """Gracefully handles invalid JSON in config."""
        import sys
        if "health_score" in sys.modules:
            del sys.modules["health_score"]
        import health_score

        self._write_config(valid=False)

        result = health_score.calculate_score(cwd=str(self.fixture_root))

        self.assertIsInstance(result, dict)
        self.assertIn("score", result)

        config_check = [c for c in result.get("checks", []) if "config" in c.get("name", "").lower()]
        if config_check:
            self.assertFalse(config_check[0].get("passed", True))

    def test_state_directory_not_writable(self):
        """Detects when state directory is not writable."""
        import sys
        import platform
        if "health_score" in sys.modules:
            del sys.modules["health_score"]
        import health_score

        self._write_config(valid=True)
        self._create_directory("daemons")
        self._create_directory("dash")
        self._create_directory("monitor")
        self._create_directory("tools")
        self._create_directory("ui")

        # Skip on Windows since chmod doesn't work the same way
        if platform.system() == "Windows":
            self.skipTest("File permissions work differently on Windows")

        # Make state dir read-only
        try:
            self.state_dir.chmod(0o444)
            result = health_score.calculate_score(cwd=str(self.fixture_root))

            writable_check = [c for c in result.get("checks", []) if "writable" in c.get("name", "").lower()]
            if writable_check:
                self.assertFalse(writable_check[0].get("passed", True))
        finally:
            self.state_dir.chmod(0o755)

    def test_git_identity_configured(self):
        """Confirms that git identity check passes when configured."""
        import sys
        if "health_score" in sys.modules:
            del sys.modules["health_score"]
        import health_score

        self._write_config(valid=True)
        self._create_directory("daemons")
        self._create_directory("dash")
        self._create_directory("monitor")
        self._create_directory("tools")
        self._create_directory("ui")

        # Git identity is set in setUp, so it should pass
        result = health_score.calculate_score(cwd=str(self.fixture_root))

        identity_check = [c for c in result.get("checks", []) if "identity" in c.get("name", "").lower()]
        if identity_check:
            # Git identity falls back to global config, so it should pass if any config is set
            self.assertTrue(identity_check[0].get("passed", False))


class TestHealthScoreCheckList(HealthScoreTestCase):
    """Tests for check list completeness."""

    def test_score_includes_all_required_checks(self):
        """Score output includes all required checks."""
        import sys
        if "health_score" in sys.modules:
            del sys.modules["health_score"]
        import health_score

        self._write_config(valid=True)
        self._create_directory("daemons")
        self._create_directory("dash")
        self._create_directory("monitor")
        self._create_directory("tools")
        self._create_directory("ui")

        result = health_score.calculate_score(cwd=str(self.fixture_root))

        checks = result.get("checks", [])
        check_names = {c.get("name", "").lower() for c in checks}

        # Verify all required checks are present
        required = {"config", "hooks", "claude", "writable", "heartbeat", "identity", "secret-scan"}
        for req in required:
            self.assertTrue(
                any(req in name for name in check_names),
                f"Missing check for {req}. Found: {check_names}"
            )


if __name__ == "__main__":
    unittest.main()
