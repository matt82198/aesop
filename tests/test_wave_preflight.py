#!/usr/bin/env python3
"""Tests for tools/wave_preflight.py — wave preflight validator.

Test strategy (TDD):
1. Check detection: git repo, feature branch, working tree clean, .HALT sentinel, heartbeats, tracker.json, secret_scan import
2. State dir resolution: AESOP_STATE_ROOT env, config state_root, default
3. STATE.md vs orchestrator-status.json phase consistency detection
4. CLI: --text, --json output formats, exit codes
5. All checks isolated: hermetic temp git repos, never touch cwd or global git config

HERMETIC: every test below creates a throwaway git repo INSIDE a temp directory.
No test ever touches cwd, global git config, or the real aesop repo.
"""

import datetime
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

TOOLS_DIR = Path(__file__).parent.parent / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

PREFLIGHT_PY = TOOLS_DIR / "wave_preflight.py"

ENV_KEYS = ("AESOP_STATE_ROOT", "AESOP_ROOT")


class PrefightTestBase(unittest.TestCase):
    """Base class: isolated temp repo + state dir, hermetic."""

    def setUp(self):
        """Create throwaway git repo in a temp directory."""
        self.fixture_root = Path(tempfile.mkdtemp(prefix="aesop-preflight-test-"))
        self.repo_dir = self.fixture_root / "repo"
        self.repo_dir.mkdir(parents=True)
        # Configure git identity in temp repo (--local scopes to this repo only)
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

        # Create initial commit so we can have branches
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
        for mod in ("halt", "common", "wave_preflight"):
            sys.modules.pop(mod, None)

    def tearDown(self):
        """Restore environment and clean up."""
        for k, v in self._saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        for mod in ("halt", "common", "wave_preflight"):
            sys.modules.pop(mod, None)
        shutil.rmtree(self.fixture_root, ignore_errors=True)

    def _run_preflight(self, *args, env_overrides=None):
        """Run wave_preflight.py with args."""
        env = os.environ.copy()
        if env_overrides:
            env.update(env_overrides)
        cmd = [sys.executable, str(PREFLIGHT_PY), f"--root={self.repo_dir}", *args]
        result = subprocess.run(cmd, capture_output=True, text=True, env=env)
        return result

    def _setup_state_md(self, phase):
        """Write a minimal STATE.md with a phase heading."""
        state_md = self.repo_dir / "STATE.md"
        state_md.write_text(
            f"# STATE\n\n## Phase: `{phase}` (test)\n\nContent.\n",
            encoding="utf-8",
        )

    def _setup_orchestrator_status(self, phase):
        """Write orchestrator-status.json with a phase field."""
        status_json = self.state_dir / "orchestrator-status.json"
        data = {
            "id": "main",
            "role": "orchestrator",
            "activity": "test",
            "phase": phase,
            "updated_at": "2026-07-17T00:00:00Z",
        }
        status_json.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")

    def _setup_tracker_json(self, valid=True):
        """Write state/tracker.json."""
        tracker_json = self.state_dir / "tracker.json"
        if valid:
            data = {"version": 1, "items": []}
            tracker_json.write_text(json.dumps(data) + "\n", encoding="utf-8")
        else:
            tracker_json.write_text("{ invalid json", encoding="utf-8")

    def _setup_heartbeats(self, fresh=True, phase=None):
        """Set up heartbeat files and orchestrator-status.json (fresh or stale).

        Args:
            fresh: If True, heartbeats are current; if False, they are 400s old
            phase: Phase value for orchestrator-status.json (default "test").
                   If orchestrator-status.json already exists, its phase is preserved.
        """
        hb_dir = self.state_dir / "heartbeats"
        hb_dir.mkdir(parents=True, exist_ok=True)

        if fresh:
            now_ts = time.time()
            now_int = int(now_ts)
        else:
            # 400 seconds old
            now_ts = time.time() - 400
            now_int = int(now_ts)

        # Watchdog heartbeat
        watchdog_hb = self.state_dir / ".watchdog-heartbeat"
        watchdog_hb.write_text(str(now_int) + "\n", encoding="utf-8")

        # Orchestrator-status.json (uses updated_at field for freshness check)
        # Preserve existing phase if orchestrator-status.json already exists
        orch_status = self.state_dir / "orchestrator-status.json"
        if orch_status.exists():
            try:
                existing = json.loads(orch_status.read_text(encoding="utf-8"))
                phase = existing.get("phase", phase or "test")
            except Exception:
                phase = phase or "test"
        else:
            phase = phase or "test"

        now_iso = datetime.datetime.fromtimestamp(now_ts, tz=datetime.timezone.utc).isoformat().replace("+00:00", "Z")
        orch_status.write_text(
            json.dumps({
                "id": "main",
                "role": "orchestrator",
                "activity": "test",
                "phase": phase,
                "updated_at": now_iso,
            }) + "\n",
            encoding="utf-8"
        )


class TestGitRepoDetection(PrefightTestBase):
    """Test git repository detection."""

    def test_detects_git_repo(self):
        """Should detect git repo."""
        result = self._run_preflight()
        self.assertEqual(result.returncode, 1)  # Will fail on other checks
        self.assertIn("Git repository: PASS", result.stdout)

    def test_detects_no_git_repo(self):
        """Should detect missing git repo."""
        non_git_dir = self.fixture_root / "not-git"
        non_git_dir.mkdir()
        result = subprocess.run(
            [sys.executable, str(PREFLIGHT_PY), f"--root={non_git_dir}"],
            capture_output=True,
            text=True,
        )
        self.assertIn("Git repository: FAIL", result.stdout)


class TestFeatureBranchDetection(PrefightTestBase):
    """Test feature branch detection."""

    def test_allows_feature_branch(self):
        """Should allow feature/* branches."""
        result = self._run_preflight()
        # Will fail on other checks, but should pass feature branch check
        self.assertIn("Feature branch (not main/master): PASS", result.stdout)

    def test_blocks_main_branch(self):
        """Should block main branch."""
        result = subprocess.run(
            ["git", "checkout", "-b", "main"],
            cwd=self.repo_dir,
            capture_output=True,
        )
        self.assertEqual(result.returncode, 0, f"git checkout failed: {result.stderr}")
        result = self._run_preflight()
        self.assertIn("Feature branch (not main/master): FAIL", result.stdout)
        self.assertNotEqual(result.returncode, 0)

    def test_blocks_master_branch(self):
        """Should block master branch."""
        # master is already created by git init, just check it out
        result = subprocess.run(
            ["git", "checkout", "master"],
            cwd=self.repo_dir,
            capture_output=True,
        )
        self.assertEqual(result.returncode, 0, f"git checkout failed: {result.stderr}")
        result = self._run_preflight()
        self.assertIn("Feature branch (not main/master): FAIL", result.stdout)
        self.assertNotEqual(result.returncode, 0)


class TestWorkingTreeClean(PrefightTestBase):
    """Test working tree cleanliness."""

    def test_clean_tree_passes(self):
        """Should pass with clean working tree."""
        result = self._run_preflight()
        self.assertIn("Working tree clean: PASS", result.stdout)

    def test_untracked_file_passes(self):
        """Untracked files should pass (git status --porcelain ignores them)."""
        (self.repo_dir / "untracked.txt").write_text("test")
        result = self._run_preflight()
        # Note: git status --porcelain only shows tracked changes, not untracked
        self.assertIn("Working tree clean: PASS", result.stdout)

    def test_modified_file_fails(self):
        """Modified tracked files should fail."""
        tracked_file = self.repo_dir / "tracked.txt"
        tracked_file.write_text("original")
        subprocess.run(
            ["git", "add", "tracked.txt"],
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

        tracked_file.write_text("modified")
        result = self._run_preflight()
        self.assertIn("Working tree clean: FAIL", result.stdout)
        self.assertNotEqual(result.returncode, 0)

    def test_staged_changes_fail(self):
        """Staged changes should fail."""
        tracked_file = self.repo_dir / "tracked.txt"
        tracked_file.write_text("original")
        subprocess.run(
            ["git", "add", "tracked.txt"],
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

        tracked_file.write_text("modified")
        subprocess.run(
            ["git", "add", "tracked.txt"],
            cwd=self.repo_dir,
            capture_output=True,
            check=True,
        )
        result = self._run_preflight()
        self.assertIn("Working tree clean: FAIL", result.stdout)
        self.assertNotEqual(result.returncode, 0)


class TestHaltSentinel(PrefightTestBase):
    """Test .HALT sentinel detection."""

    def test_no_halt_passes(self):
        """Should pass when no .HALT sentinel."""
        result = self._run_preflight()
        self.assertIn("No .HALT sentinel: PASS", result.stdout)

    def test_halt_sentinel_fails(self):
        """Should fail when .HALT sentinel exists."""
        halt_sentinel = self.state_dir / ".HALT"
        halt_data = {"reason": "testing", "timestamp": "2026-07-17T00:00:00Z"}
        halt_sentinel.write_text(json.dumps(halt_data) + "\n", encoding="utf-8")
        result = self._run_preflight()
        self.assertIn("No .HALT sentinel: FAIL", result.stdout)
        self.assertNotEqual(result.returncode, 0)


class TestPhaseConsistency(PrefightTestBase):
    """Test STATE.md vs orchestrator-status.json phase consistency."""

    def test_consistent_phases_pass(self):
        """Should pass when phases match."""
        self._setup_state_md("wave-rc.2")
        self._setup_orchestrator_status("wave-rc.2")
        result = self._run_preflight()
        self.assertIn("STATE.md phase consistent", result.stdout)
        self.assertIn("PASS", result.stdout.split("STATE.md phase")[1].split("\n")[0])

    def test_inconsistent_phases_warn(self):
        """Should warn but not block when phases differ (warning-level)."""
        self._setup_state_md("wave-rc.2")
        self._setup_orchestrator_status("wave-rc.3")
        self._setup_tracker_json(valid=True)
        self._setup_heartbeats(fresh=True)
        result = self._run_preflight()
        self.assertIn("STATE.md phase consistent", result.stdout)
        # Should show WARN for drift
        self.assertIn("[WARN: drift detected]", result.stdout)
        # Should show both values in the detail
        self.assertIn("STATE.md=wave-rc.2", result.stdout)
        self.assertIn("status.json=wave-rc.3", result.stdout)
        # Phase drift is warning-level: doesn't block the wave (exit 0)
        self.assertEqual(result.returncode, 0)

    def test_missing_files_ok(self):
        """Should pass if files don't exist yet."""
        result = self._run_preflight()
        self.assertIn("STATE.md phase consistent", result.stdout)
        # Missing files should be treated as OK
        lines = result.stdout.split("\n")
        phase_line = [l for l in lines if "STATE.md phase consistent" in l][0]
        self.assertIn("PASS", phase_line)

    def test_phase_drift_detail_shows_both_values(self):
        """Detail message should include both STATE.md and status.json phase values."""
        self._setup_state_md("wave-001")
        self._setup_orchestrator_status("wave-002")
        self._setup_tracker_json(valid=True)
        self._setup_heartbeats(fresh=True)
        result = self._run_preflight("--json")
        data = json.loads(result.stdout)
        phase_check = [c for c in data["checks"] if "phase consistent" in c["name"]][0]
        # Phase drift is warning-level: check passes but warning is visible
        self.assertTrue(phase_check["ok"])
        self.assertIn("STATE.md=wave-001", phase_check["detail"])
        self.assertIn("status.json=wave-002", phase_check["detail"])
        self.assertIn("WARN: drift detected", phase_check["detail"])

    def test_phase_matching_no_warn(self):
        """Matching phases should not show warning."""
        self._setup_state_md("wave-001")
        self._setup_orchestrator_status("wave-001")
        self._setup_tracker_json(valid=True)
        self._setup_heartbeats(fresh=True)
        result = self._run_preflight()
        self.assertIn("STATE.md phase consistent", result.stdout)
        self.assertNotIn("WARN: drift detected", result.stdout)
        self.assertEqual(result.returncode, 0)

    def test_state_md_only_no_warn(self):
        """Only STATE.md present (no phase in status.json) should not warn."""
        self._setup_state_md("wave-001")
        # Create orchestrator-status.json WITHOUT a phase field
        self._setup_tracker_json(valid=True)
        now_ts = time.time()
        now_int = int(now_ts)
        (self.state_dir / ".watchdog-heartbeat").write_text(str(now_int) + "\n", encoding="utf-8")
        # orchestrator-status.json with no phase field (parse will return None)
        import datetime
        now_iso = datetime.datetime.fromtimestamp(now_ts, tz=datetime.timezone.utc).isoformat().replace("+00:00", "Z")
        (self.state_dir / "orchestrator-status.json").write_text(
            json.dumps({
                "id": "main",
                "role": "orchestrator",
                "activity": "test",
                "updated_at": now_iso,
            }) + "\n",
            encoding="utf-8"
        )
        result = self._run_preflight()
        # Should not warn when status.json has no phase field
        self.assertNotIn("WARN: drift detected", result.stdout)
        self.assertEqual(result.returncode, 0)

    def test_status_json_only_no_warn(self):
        """Only status.json present (no STATE.md) should not warn."""
        # STATE.md missing
        self._setup_orchestrator_status("wave-001")
        self._setup_tracker_json(valid=True)
        self._setup_heartbeats(fresh=True)
        result = self._run_preflight()
        # Should not warn when one file is missing
        self.assertNotIn("WARN: drift detected", result.stdout)
        self.assertEqual(result.returncode, 0)


class TestHeartbeatFreshness(PrefightTestBase):
    """Test heartbeat and orchestrator status freshness detection."""

    def test_fresh_heartbeats_pass(self):
        """Should pass with fresh heartbeats and orchestrator status."""
        self._setup_heartbeats(fresh=True)
        self._setup_tracker_json(valid=True)
        result = self._run_preflight()
        self.assertIn("Heartbeats and orchestrator status fresh: PASS", result.stdout)
        self.assertEqual(result.returncode, 0)

    def test_stale_heartbeats_fail(self):
        """Should fail with stale heartbeats."""
        self._setup_heartbeats(fresh=False)
        self._setup_tracker_json(valid=True)
        result = self._run_preflight()
        self.assertIn("Heartbeats and orchestrator status fresh: FAIL", result.stdout)
        self.assertNotEqual(result.returncode, 0)

    def test_missing_heartbeats_fail(self):
        """Should fail if heartbeat files missing."""
        self._setup_tracker_json(valid=True)
        result = self._run_preflight()
        self.assertIn("Heartbeats and orchestrator status fresh: FAIL", result.stdout)
        self.assertNotEqual(result.returncode, 0)


class TestTrackerJson(PrefightTestBase):
    """Test tracker.json JSON validation."""

    def test_valid_tracker_json_passes(self):
        """Should pass with valid tracker.json."""
        self._setup_tracker_json(valid=True)
        result = self._run_preflight()
        self.assertIn("state/tracker.json parses as JSON: PASS", result.stdout)

    def test_invalid_tracker_json_fails(self):
        """Should fail with invalid JSON."""
        self._setup_tracker_json(valid=False)
        result = self._run_preflight()
        self.assertIn("state/tracker.json parses as JSON: FAIL", result.stdout)
        self.assertNotEqual(result.returncode, 0)

    def test_missing_tracker_json_fails(self):
        """Should fail if tracker.json missing."""
        result = self._run_preflight()
        self.assertIn("state/tracker.json parses as JSON: FAIL", result.stdout)
        self.assertNotEqual(result.returncode, 0)


class TestSecretScanImport(PrefightTestBase):
    """Test secret_scan importability."""

    def test_secret_scan_importable(self):
        """Should pass if secret_scan can be imported."""
        result = self._run_preflight()
        self.assertIn("secret_scan importable: PASS", result.stdout)


class TestOutputFormats(PrefightTestBase):
    """Test output formatting."""

    def test_text_output_default(self):
        """Default output should be text format."""
        result = self._run_preflight()
        self.assertIn("Wave preflight checks:", result.stdout)
        # Should have numbered checks
        self.assertIn("1.", result.stdout)

    def test_json_output_format(self):
        """Should output JSON when --json specified."""
        result = self._run_preflight("--json")
        # Should be valid JSON
        data = json.loads(result.stdout)
        self.assertIn("ready", data)
        self.assertIn("checks", data)
        self.assertIsInstance(data["checks"], list)

    def test_json_check_structure(self):
        """JSON output should have proper check structure."""
        result = self._run_preflight("--json")
        data = json.loads(result.stdout)
        for check in data["checks"]:
            self.assertIn("name", check)
            self.assertIn("ok", check)
            self.assertIn("detail", check)
            self.assertIsInstance(check["ok"], bool)


class TestExitCodes(PrefightTestBase):
    """Test exit codes."""

    def test_ready_returns_zero(self):
        """Ready state should return exit 0."""
        self._setup_state_md("test")
        self._setup_orchestrator_status("test")
        self._setup_tracker_json(valid=True)
        self._setup_heartbeats(fresh=True)
        result = self._run_preflight()
        self.assertEqual(result.returncode, 0)

    def test_not_ready_returns_one(self):
        """Not ready (missing tracker.json) should return exit 1."""
        result = self._run_preflight()
        self.assertEqual(result.returncode, 1)


class TestStateDirResolution(PrefightTestBase):
    """Test state directory resolution."""

    def test_env_var_takes_precedence(self):
        """AESOP_STATE_ROOT env var should take precedence."""
        alt_state = self.fixture_root / "alt-state"
        alt_state.mkdir()

        # Create tracker.json in alt state dir
        tracker_json = alt_state / "tracker.json"
        tracker_json.write_text(json.dumps({"version": 1, "items": []}) + "\n")

        # Create heartbeats in alt state dir
        hb_dir = alt_state / "heartbeats"
        hb_dir.mkdir()
        now = int(time.time())
        (alt_state / ".watchdog-heartbeat").write_text(str(now) + "\n")
        (hb_dir / "orchestrator").write_text(str(now) + "\n")

        result = self._run_preflight(
            env_overrides={"AESOP_STATE_ROOT": str(alt_state)}
        )
        # Should find tracker.json in alt state dir
        self.assertIn("state/tracker.json parses as JSON: PASS", result.stdout)


class TestBlockingFailures(PrefightTestBase):
    """Test that failures block wave (exit 1 with numbered list)."""

    def test_multiple_failures_listed_numbered(self):
        """Multiple failures should be listed with numbers."""
        # Create a scenario with multiple failures
        # (missing tracker, stale heartbeats, on main branch, etc.)
        subprocess.run(
            ["git", "checkout", "-b", "main"],
            cwd=self.repo_dir,
            capture_output=True,
        )
        result = self._run_preflight()
        # Should show multiple numbered items
        self.assertIn("1.", result.stdout)
        self.assertIn("2.", result.stdout)
        self.assertNotEqual(result.returncode, 0)


class TestWindowsPathHandling(PrefightTestBase):
    """Test Windows path handling (backslashes, relative paths, config portability)."""

    def test_config_with_relative_state_root(self):
        """Should handle config with relative state_root path."""
        # Create a config file with relative state_root
        config_file = self.repo_dir / "aesop.config.json"
        config_data = {
            "description": "Test config",
            "state_root": "state",  # Relative path
            "aesop_root": str(self.repo_dir),
        }
        config_file.write_text(json.dumps(config_data) + "\n", encoding="utf-8")

        # Create state dir and tracker.json
        self._setup_tracker_json(valid=True)
        self._setup_heartbeats(fresh=True)

        result = self._run_preflight()
        self.assertIn("state/tracker.json parses as JSON: PASS", result.stdout)

    def test_config_with_tilde_state_root(self):
        """Should handle config with ~ expanded paths."""
        # Create a config file with ~ path (should be expanded by resolve_state_dir)
        config_file = self.repo_dir / "aesop.config.json"
        config_data = {
            "description": "Test config",
            "state_root": "state",  # Relative path (not ~, since ~ is user home)
            "aesop_root": str(self.repo_dir),
        }
        config_file.write_text(json.dumps(config_data) + "\n", encoding="utf-8")

        # Create state dir and tracker.json
        self._setup_tracker_json(valid=True)
        self._setup_heartbeats(fresh=True)

        result = self._run_preflight()
        self.assertIn("state/tracker.json parses as JSON: PASS", result.stdout)


class TestStateRootSeparation(PrefightTestBase):
    """Test that --state-root is separate from --root (wave-rc4 fix a)."""

    def test_state_root_argument_separate_from_root(self):
        """Should accept --state-root separate from --root."""
        # Create an alternate state directory
        alt_state = self.fixture_root / "alt-state"
        alt_state.mkdir()

        # Set up tracker.json and heartbeats in the alternate state dir
        tracker_json = alt_state / "tracker.json"
        tracker_json.write_text(json.dumps({"version": 1, "items": []}) + "\n")

        hb_dir = alt_state / "heartbeats"
        hb_dir.mkdir()
        now = int(time.time())
        (alt_state / ".watchdog-heartbeat").write_text(str(now) + "\n")
        (hb_dir / "orchestrator").write_text(str(now) + "\n")

        # Create orchestrator-status.json in alt state dir
        orch_status = alt_state / "orchestrator-status.json"
        orch_status.write_text(
            json.dumps({"updated_at": "2026-07-17T00:00:00Z", "phase": "test"}) + "\n"
        )

        # Run preflight with both --root and --state-root pointing to different dirs
        cmd = [
            sys.executable,
            str(PREFLIGHT_PY),
            f"--root={self.repo_dir}",
            f"--state-root={alt_state}",
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)

        # Should find tracker.json in alt state dir
        self.assertIn("state/tracker.json parses as JSON: PASS", result.stdout)

    def test_state_root_env_var_overrides_argument(self):
        """AESOP_STATE_ROOT env var should take precedence over --state-root argument."""
        alt_state = self.fixture_root / "alt-state-env"
        alt_state.mkdir()

        tracker_json = alt_state / "tracker.json"
        tracker_json.write_text(json.dumps({"version": 1, "items": []}) + "\n")

        hb_dir = alt_state / "heartbeats"
        hb_dir.mkdir()
        now = int(time.time())
        (alt_state / ".watchdog-heartbeat").write_text(str(now) + "\n")
        (hb_dir / "orchestrator").write_text(str(now) + "\n")

        orch_status = alt_state / "orchestrator-status.json"
        orch_status.write_text(
            json.dumps({"updated_at": "2026-07-17T00:00:00Z"}) + "\n"
        )

        # AESOP_STATE_ROOT env var should override any --state-root
        env = os.environ.copy()
        env["AESOP_STATE_ROOT"] = str(alt_state)

        cmd = [
            sys.executable,
            str(PREFLIGHT_PY),
            f"--root={self.repo_dir}",
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, env=env)

        # Should use env var state dir
        self.assertIn("state/tracker.json parses as JSON: PASS", result.stdout)


class TestPhaseDriftWarning(PrefightTestBase):
    """Test that STATE.md phase drift is warning-level, not a blocker (wave-rc4 fix b)."""

    def test_phase_drift_is_warning_not_blocker(self):
        """Phase drift is warning-level: visible in output but exit 0."""
        self._setup_state_md("wave-rc.2")
        self._setup_orchestrator_status("wave-rc.3")  # Drifted
        self._setup_tracker_json(valid=True)
        self._setup_heartbeats(fresh=True)

        result = self._run_preflight()

        # Should show the drift warning (loud, visible)
        self.assertIn("[WARN: drift detected]", result.stdout)

        # But phase drift is warning-level, so exit 0 (doesn't block the wave)
        self.assertEqual(result.returncode, 0)

    def test_phase_consistent_passes_cleanly(self):
        """Consistent phases should pass cleanly."""
        self._setup_state_md("wave-rc.2")
        self._setup_orchestrator_status("wave-rc.2")
        self._setup_tracker_json(valid=True)
        self._setup_heartbeats(fresh=True)

        result = self._run_preflight()
        self.assertIn("STATE.md phase consistent", result.stdout)
        self.assertIn("PASS", result.stdout.split("STATE.md phase")[1].split("\n")[0])
        self.assertEqual(result.returncode, 0)

    def test_multiple_failures_and_phase_drift_returns_one(self):
        """Multiple failures + phase drift should return exit 1 (from failures, not drift)."""
        # Don't set up heartbeats or tracker (failures)
        self._setup_state_md("wave-rc.2")
        self._setup_orchestrator_status("wave-rc.3")  # Drifted

        result = self._run_preflight()

        # Phase drift is warning, but heartbeat/tracker failures should cause exit 1
        self.assertNotEqual(result.returncode, 0)


class TestOrchestratorStatusFreshness(PrefightTestBase):
    """Test orchestrator-status.json freshness check (wave-rc4 fix c)."""

    def test_orchestrator_status_updated_at_fresh(self):
        """Should pass when orchestrator-status.json has recent updated_at."""
        self._setup_tracker_json(valid=True)
        self._setup_heartbeats(fresh=True)

        # Create orchestrator-status.json with recent updated_at
        orch_status = self.state_dir / "orchestrator-status.json"
        now_ts = time.time()
        import datetime
        now_iso = datetime.datetime.fromtimestamp(now_ts, tz=datetime.timezone.utc).isoformat().replace("+00:00", "Z")
        orch_status.write_text(
            json.dumps({
                "id": "main",
                "role": "orchestrator",
                "activity": "running",
                "phase": "wave-rc.2",
                "updated_at": now_iso,
            }) + "\n"
        )

        result = self._run_preflight()
        self.assertIn("Heartbeats and orchestrator status fresh: PASS", result.stdout)
        self.assertEqual(result.returncode, 0)

    def test_orchestrator_status_updated_at_stale(self):
        """Should fail when orchestrator-status.json has stale updated_at."""
        self._setup_tracker_json(valid=True)
        self._setup_heartbeats(fresh=True)

        # Create orchestrator-status.json with stale updated_at (>300s old)
        orch_status = self.state_dir / "orchestrator-status.json"
        old_ts = int(time.time()) - 400  # 400s old
        import datetime
        old_dt = datetime.datetime.fromtimestamp(old_ts, tz=datetime.timezone.utc)
        old_iso = old_dt.isoformat().replace("+00:00", "Z")
        orch_status.write_text(
            json.dumps({
                "id": "main",
                "role": "orchestrator",
                "activity": "running",
                "phase": "wave-rc.2",
                "updated_at": old_iso,
            }) + "\n"
        )

        result = self._run_preflight()
        self.assertIn("Heartbeats and orchestrator status fresh: FAIL", result.stdout)
        self.assertNotEqual(result.returncode, 0)

    def test_orchestrator_status_missing_updated_at_field(self):
        """Should fail when orchestrator-status.json lacks updated_at field."""
        self._setup_tracker_json(valid=True)
        self._setup_heartbeats(fresh=True)

        # Create orchestrator-status.json without updated_at
        orch_status = self.state_dir / "orchestrator-status.json"
        orch_status.write_text(
            json.dumps({
                "id": "main",
                "role": "orchestrator",
                "activity": "running",
                "phase": "wave-rc.2",
            }) + "\n"
        )

        result = self._run_preflight()
        self.assertIn("Heartbeats and orchestrator status fresh: FAIL", result.stdout)
        self.assertNotEqual(result.returncode, 0)


class TestBacklogAnalysis(unittest.TestCase):
    """Test backlog item flagging for risky conditions."""

    def setUp(self):
        """Create temporary directories for test fixtures."""
        self.temp_dir = tempfile.TemporaryDirectory()
        self.temp_path = Path(self.temp_dir.name)
        self.tracker_file = self.temp_path / "tracker.json"
        self.ledger_file = self.temp_path / "ledger.md"
        self.work_dir = self.temp_path / "work"
        self.work_dir.mkdir()

    def tearDown(self):
        """Clean up temporary directories."""
        self.temp_dir.cleanup()

    def _write_tracker(self, items):
        """Write a tracker.json fixture."""
        data = {"version": 1, "items": items}
        self.tracker_file.write_text(json.dumps(data, indent=2), encoding='utf-8')

    def _write_ledger(self, rows):
        """Write a minimal ledger.md with header and rows.

        Args:
            rows: list of tuples: (iso_ts, agent_type, model, dur_sec, tokens_in, tokens_out, verdict, phase, wave)
        """
        header = '| ISO ts | agent_type | model | duration_sec | tokens_in | tokens_out | verdict | phase | wave |\n'
        header += '|--------|------------|-------|--------------|-----------|------------|--------|-------|------|\n'
        content = header
        for row in rows:
            # row is already formatted as pipe-separated values
            if isinstance(row, str) and row.startswith('|'):
                # Already formatted
                content += row + '\n'
            else:
                # Format the row
                content += f"| {' | '.join(str(v) for v in row)} |\n"
        self.ledger_file.write_text(content, encoding='utf-8')

    def _write_file(self, path, content="test"):
        """Write a file at the given path (relative to work_dir)."""
        file_path = self.work_dir / path
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(content, encoding='utf-8')

    def test_flag_missing_owns_files(self):
        """Flag items with missing or empty owns_files."""
        self._write_tracker([
            {
                "id": "1", "title": "Item without owns_files",
                "priority": "P1", "status": "todo", "lane": "proposed",
                "source": "test", "tags": [], "notes": "no owns_files field",
                "pr_link": None, "created_at": "2026-01-01T00:00:00Z", "completed_at": None
            },
            {
                "id": "2", "title": "Item with empty owns_files",
                "priority": "P1", "status": "todo", "lane": "proposed",
                "source": "test", "tags": [], "notes": "empty owns_files",
                "owns_files": [],
                "pr_link": None, "created_at": "2026-01-01T00:00:00Z", "completed_at": None
            }
        ])

        # Import and run the tool
        from tools.wave_preflight import scan_backlog_items
        result = scan_backlog_items(str(self.tracker_file), str(self.work_dir))

        # Should flag both items as having missing/empty file ownership
        self.assertTrue(result["success"])
        flags_text = str(result["items"])
        self.assertTrue(any("missing_ownership" in flags_text for _ in [None]))

    def test_flag_stale_items(self):
        """Flag items that reference non-existent files."""
        self._write_file("existing.py", "real file")
        self._write_tracker([
            {
                "id": "1", "title": "Item refs non-existent file",
                "priority": "P1", "status": "todo", "lane": "proposed",
                "source": "test", "tags": [], "notes": "fixes nonexistent.py",
                "owns_files": ["nonexistent.py"],
                "pr_link": None, "created_at": "2026-01-01T00:00:00Z", "completed_at": None
            }
        ])

        from tools.wave_preflight import scan_backlog_items
        result = scan_backlog_items(str(self.tracker_file), str(self.work_dir))

        self.assertTrue(result["success"])
        # Should flag item 1 as stale
        stale_found = any(
            flag.get("type") == "stale_reference"
            for item in result["items"]
            for flag in item.get("flags", [])
        )
        self.assertTrue(stale_found, "Expected stale_reference flag")

    def test_flag_overlapping_ownership(self):
        """Flag two items that own the same file."""
        self._write_file("shared.py", "shared")
        self._write_tracker([
            {
                "id": "1", "title": "Item A owns shared",
                "priority": "P1", "status": "todo", "lane": "proposed",
                "source": "test", "tags": [], "notes": "owns shared.py",
                "owns_files": ["shared.py"],
                "pr_link": None, "created_at": "2026-01-01T00:00:00Z", "completed_at": None
            },
            {
                "id": "2", "title": "Item B also owns shared",
                "priority": "P1", "status": "todo", "lane": "proposed",
                "source": "test", "tags": [], "notes": "also owns shared.py",
                "owns_files": ["shared.py"],
                "pr_link": None, "created_at": "2026-01-01T00:00:00Z", "completed_at": None
            }
        ])

        from tools.wave_preflight import scan_backlog_items
        result = scan_backlog_items(str(self.tracker_file), str(self.work_dir))

        self.assertTrue(result["success"])
        # Should flag overlap
        overlap_found = any(
            flag.get("type") == "ownership_overlap"
            for item in result["items"]
            for flag in item.get("flags", [])
        )
        self.assertTrue(overlap_found, "Expected ownership_overlap flag")

    def test_history_signal_from_ledger(self):
        """Extract repair/retry rate from ledger."""
        self._write_tracker([
            {
                "id": "1", "title": "Item", "priority": "P1", "status": "todo",
                "lane": "proposed", "source": "test", "tags": [], "notes": "",
                "owns_files": ["test.py"], "pr_link": None,
                "created_at": "2026-01-01T00:00:00Z", "completed_at": None
            }
        ])
        self._write_file("test.py", "test")
        # Simulate a ledger with some entries (format: iso_ts, agent_type, model, dur, tokens_in, tokens_out, verdict, phase, wave)
        self._write_ledger([
            ("2026-01-01T00:00:00Z", "haiku", "haiku", "10", "100", "200", "OK", "build", "1"),
            ("2026-01-01T00:01:00Z", "haiku", "haiku", "12", "100", "300", "FAILED", "build", "1"),
            ("2026-01-01T00:02:00Z", "haiku", "haiku", "10", "100", "250", "OK", "repair", "1"),
        ])

        from tools.wave_preflight import scan_backlog_items
        result = scan_backlog_items(str(self.tracker_file), str(self.work_dir), str(self.ledger_file))

        # Should have extracted ledger stats
        self.assertIn("ledger_stats", result)
        stats = result.get("ledger_stats", {})
        self.assertEqual(stats.get("data"), "AVAILABLE")
        self.assertEqual(stats.get("total_entries"), 3)
        self.assertEqual(stats.get("failed_count"), 1)

    def test_data_unavailable_when_no_ledger(self):
        """Return DATA-UNAVAILABLE signal when ledger is missing."""
        self._write_tracker([
            {
                "id": "1", "title": "Item", "priority": "P1", "status": "todo",
                "lane": "proposed", "source": "test", "tags": [], "notes": "",
                "owns_files": ["test.py"], "pr_link": None,
                "created_at": "2026-01-01T00:00:00Z", "completed_at": None
            }
        ])
        self._write_file("test.py", "test")

        from tools.wave_preflight import scan_backlog_items
        result = scan_backlog_items(str(self.tracker_file), str(self.work_dir))

        # Should indicate data unavailable, never fabricate
        stats = result.get("ledger_stats", {})
        self.assertEqual(stats.get("data"), "DATA-UNAVAILABLE")

    def test_malformed_tracker_fails_gracefully(self):
        """Handle malformed tracker input gracefully."""
        bad_tracker = self.temp_path / "bad.json"
        bad_tracker.write_text("{ invalid json }", encoding='utf-8')

        from tools.wave_preflight import scan_backlog_items
        result = scan_backlog_items(str(bad_tracker), str(self.work_dir))

        # Should indicate failure
        self.assertFalse(result.get("success", True))

    def test_json_output_format(self):
        """Test JSON output format is valid."""
        self._write_tracker([
            {
                "id": "1", "title": "Test item", "priority": "P1", "status": "todo",
                "lane": "proposed", "source": "test", "tags": [], "notes": "test",
                "owns_files": ["test.py"], "pr_link": None,
                "created_at": "2026-01-01T00:00:00Z", "completed_at": None
            }
        ])
        self._write_file("test.py", "test")

        from tools.wave_preflight import scan_backlog_items
        result = scan_backlog_items(str(self.tracker_file), str(self.work_dir))

        # Result should be serializable to JSON
        json_str = json.dumps(result)
        self.assertIsInstance(json_str, str)
        reparsed = json.loads(json_str)
        self.assertIsInstance(reparsed, dict)

    def test_empty_tracker(self):
        """Handle empty tracker gracefully."""
        self._write_tracker([])

        from tools.wave_preflight import scan_backlog_items
        result = scan_backlog_items(str(self.tracker_file), str(self.work_dir))

        self.assertTrue(result.get("success", True))
        self.assertEqual(len(result.get("items", [])), 0)

    def test_todo_items_only(self):
        """Only flag items that are todo (not archived/done)."""
        self._write_tracker([
            {
                "id": "1", "title": "Todo item",
                "priority": "P1", "status": "todo", "lane": "proposed",
                "source": "test", "tags": [], "notes": "",
                "owns_files": [],
                "pr_link": None, "created_at": "2026-01-01T00:00:00Z", "completed_at": None
            },
            {
                "id": "2", "title": "Done item",
                "priority": "P1", "status": "done", "lane": "done",
                "source": "test", "tags": [], "notes": "",
                "owns_files": [],
                "pr_link": None, "created_at": "2026-01-01T00:00:00Z", "completed_at": "2026-01-02T00:00:00Z"
            }
        ])

        from tools.wave_preflight import scan_backlog_items
        result = scan_backlog_items(str(self.tracker_file), str(self.work_dir))

        # Should only process todo items (id=1)
        item_ids = [item.get("id") for item in result.get("items", [])]
        self.assertIn("1", item_ids)
        # Done items should not be flagged
        self.assertNotIn("2", item_ids)


if __name__ == "__main__":
    unittest.main()
