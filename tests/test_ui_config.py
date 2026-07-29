"""Unit tests for ui/config.py AESOP_ROOT resolution and path derivation.

Contract: AESOP_ROOT resolution follows a fallback tier:
  (1) AESOP_ROOT env var if set
  (2) Derive from config.py file location: Path(__file__).resolve().parents[1]
  (3) Load config from derived location; if it has aesop_root, use that

All derived paths (WEB_DIST, CONFIG_FILE, STATE_DIR) must follow from resolved AESOP_ROOT.
Config reads must stay CALL-TIME per project convention.

Run: python -m unittest tests.test_ui_config
"""
import json
import os
import sys
import tempfile
import shutil
import unittest
from pathlib import Path


class TestAesopRootResolution(unittest.TestCase):
    """Test cases for AESOP_ROOT fallback tier resolution."""

    def setUp(self):
        """Create temporary fixture directory structure."""
        self.fixture_root = tempfile.mkdtemp(prefix="aesop-config-test-")
        self.state_dir = os.path.join(self.fixture_root, "state")
        self.ui_dir = os.path.join(self.fixture_root, "ui")
        os.makedirs(self.state_dir, exist_ok=True)
        os.makedirs(self.ui_dir, exist_ok=True)

        # Save original env
        self.orig_aesop_root = os.environ.get("AESOP_ROOT")
        self.orig_port = os.environ.get("PORT")
        self.orig_aesop_state_root = os.environ.get("AESOP_STATE_ROOT")
        self.orig_aesop_transcripts_root = os.environ.get("AESOP_TRANSCRIPTS_ROOT")

    def tearDown(self):
        """Clean up temporary fixture and restore environment."""
        # Restore original env
        if self.orig_aesop_root is not None:
            os.environ["AESOP_ROOT"] = self.orig_aesop_root
        elif "AESOP_ROOT" in os.environ:
            del os.environ["AESOP_ROOT"]

        if self.orig_port is not None:
            os.environ["PORT"] = self.orig_port
        elif "PORT" in os.environ:
            del os.environ["PORT"]

        if self.orig_aesop_state_root is not None:
            os.environ["AESOP_STATE_ROOT"] = self.orig_aesop_state_root
        elif "AESOP_STATE_ROOT" in os.environ:
            del os.environ["AESOP_STATE_ROOT"]

        if self.orig_aesop_transcripts_root is not None:
            os.environ["AESOP_TRANSCRIPTS_ROOT"] = self.orig_aesop_transcripts_root
        elif "AESOP_TRANSCRIPTS_ROOT" in os.environ:
            del os.environ["AESOP_TRANSCRIPTS_ROOT"]

        # Clean up temp dir
        if os.path.exists(self.fixture_root):
            shutil.rmtree(self.fixture_root)

        # Remove cached config module if it exists
        if "ui.config" in sys.modules:
            del sys.modules["ui.config"]
        if "ui" in sys.modules:
            del sys.modules["ui"]

    def _load_config_module(self, fixture_root=None):
        """Dynamically import config.py, optionally with fixture AESOP_ROOT and UI location.

        Args:
            fixture_root: If set, create config.py in fixture ui/ dir and use as AESOP_ROOT.
                         If not set, uses actual aesop tree.

        Returns:
            Imported config module.
        """
        if fixture_root is not None:
            # Create config.py in fixture ui/ directory (simulates derived location)
            fixture_config_py = os.path.join(fixture_root, "ui", "config.py")
            actual_config_py = Path(__file__).parent.parent / "ui" / "config.py"

            # Copy the actual config.py to the fixture ui/ directory
            with open(actual_config_py, "r") as f:
                config_content = f.read()
            with open(fixture_config_py, "w") as f:
                f.write(config_content)

        # Remove cached config module if it exists
        if "ui.config" in sys.modules:
            del sys.modules["ui.config"]
        if "ui" in sys.modules:
            del sys.modules["ui"]

        # Import config module dynamically
        config_path = (
            Path(fixture_root) / "ui" / "config.py"
            if fixture_root
            else Path(__file__).parent.parent / "ui" / "config.py"
        )
        import importlib.util
        spec = importlib.util.spec_from_file_location("ui.config", config_path)
        config = importlib.util.module_from_spec(spec)
        sys.modules["ui.config"] = config
        spec.loader.exec_module(config)
        return config

    def test_env_var_takes_priority_1(self):
        """Test that AESOP_ROOT env var has highest priority."""
        # Set env var to fixture root
        os.environ["AESOP_ROOT"] = self.fixture_root

        config = self._load_config_module()

        self.assertEqual(config.AESOP_ROOT, Path(self.fixture_root))

    def test_env_unset_derives_from_file_location(self):
        """Test that AESOP_ROOT derives from config.py file location when env unset."""
        # Ensure env var is not set
        if "AESOP_ROOT" in os.environ:
            del os.environ["AESOP_ROOT"]

        # Load config from fixture location (simulates NPX scaffolding scenario)
        config = self._load_config_module(fixture_root=self.fixture_root)

        # Should derive to fixture_root (parent of ui/config.py)
        # Resolve both paths to handle Windows 8.3 short name differences
        self.assertEqual(config.AESOP_ROOT.resolve(), Path(self.fixture_root).resolve())

    def test_config_aesop_root_overrides_derived(self):
        """Test that aesop_root in config file overrides derived location (priority 3)."""
        # Create a sub-directory to be the "real" aesop root
        real_root = os.path.join(self.fixture_root, "real-aesop")
        os.makedirs(real_root, exist_ok=True)

        # Create aesop.config.json in fixture_root pointing to real_root
        config_file = os.path.join(self.fixture_root, "aesop.config.json")
        config_data = {"aesop_root": real_root}
        with open(config_file, "w") as f:
            json.dump(config_data, f)

        # Ensure env var is not set
        if "AESOP_ROOT" in os.environ:
            del os.environ["AESOP_ROOT"]

        # Load config from fixture location
        config = self._load_config_module(fixture_root=self.fixture_root)

        # Should use real_root from config
        self.assertEqual(config.AESOP_ROOT, Path(real_root))

    def test_config_file_path_follows_aesop_root(self):
        """Test that CONFIG_FILE is derived from resolved AESOP_ROOT."""
        os.environ["AESOP_ROOT"] = self.fixture_root

        config = self._load_config_module()

        expected_config_file = Path(self.fixture_root) / "aesop.config.json"
        self.assertEqual(config.CONFIG_FILE, expected_config_file)

    def test_state_dir_path_follows_aesop_root(self):
        """Test that STATE_DIR defaults derive from resolved AESOP_ROOT."""
        os.environ["AESOP_ROOT"] = self.fixture_root

        config = self._load_config_module()

        expected_state_dir = Path(self.fixture_root) / "state"
        self.assertEqual(config.STATE_DIR, expected_state_dir)

    def test_web_dist_path_follows_aesop_root(self):
        """Test that WEB_DIST derives from resolved AESOP_ROOT."""
        os.environ["AESOP_ROOT"] = self.fixture_root

        config = self._load_config_module()

        expected_web_dist = Path(self.fixture_root) / "ui" / "web" / "dist"
        self.assertEqual(config.WEB_DIST, expected_web_dist)

    def test_config_file_env_var_overrides_default(self):
        """Test that state_root env var overrides default STATE_DIR derivation."""
        custom_state = os.path.join(self.fixture_root, "custom-state")
        os.makedirs(custom_state, exist_ok=True)

        os.environ["AESOP_ROOT"] = self.fixture_root
        os.environ["AESOP_STATE_ROOT"] = custom_state

        config = self._load_config_module()

        self.assertEqual(config.STATE_DIR, Path(custom_state))

    def test_config_call_time_reload(self):
        """Test that config.reload() recomputes all paths from current environment."""
        # Set initial AESOP_ROOT
        os.environ["AESOP_ROOT"] = self.fixture_root
        config = self._load_config_module()
        initial_root = config.AESOP_ROOT

        # Change AESOP_ROOT env var
        new_root = os.path.join(self.fixture_root, "new-root")
        os.makedirs(new_root, exist_ok=True)
        os.environ["AESOP_ROOT"] = new_root

        # Reload config
        config.reload()

        # Verify paths recomputed
        self.assertEqual(config.AESOP_ROOT, Path(new_root))
        self.assertEqual(config.CONFIG_FILE, Path(new_root) / "aesop.config.json")

    def test_precedence_env_over_config_file(self):
        """Test that env vars have higher precedence than config file values."""
        # Create aesop.config.json with state_root
        config_file = os.path.join(self.fixture_root, "aesop.config.json")
        config_data = {"state_root": os.path.join(self.fixture_root, "config-state")}
        with open(config_file, "w") as f:
            json.dump(config_data, f)
        os.makedirs(config_data["state_root"], exist_ok=True)

        # Set env var to override config file
        env_state = os.path.join(self.fixture_root, "env-state")
        os.makedirs(env_state, exist_ok=True)
        os.environ["AESOP_ROOT"] = self.fixture_root
        os.environ["AESOP_STATE_ROOT"] = env_state

        config = self._load_config_module()

        # Should use env var, not config file
        self.assertEqual(config.STATE_DIR, Path(env_state))

    def test_all_derived_paths_consistent(self):
        """Test that all paths derive from the same AESOP_ROOT."""
        os.environ["AESOP_ROOT"] = self.fixture_root

        config = self._load_config_module()

        # All paths should use fixture_root as base
        self.assertTrue(
            str(config.CONFIG_FILE).startswith(self.fixture_root),
            f"CONFIG_FILE {config.CONFIG_FILE} does not start with {self.fixture_root}"
        )
        self.assertTrue(
            str(config.STATE_DIR).startswith(self.fixture_root),
            f"STATE_DIR {config.STATE_DIR} does not start with {self.fixture_root}"
        )
        self.assertTrue(
            str(config.WEB_DIST).startswith(self.fixture_root),
            f"WEB_DIST {config.WEB_DIST} does not start with {self.fixture_root}"
        )

    def test_config_aesop_root_expanduser(self):
        """Test that aesop_root with ~/ expands to user home directory."""
        # Create a sub-directory relative to home
        home = os.path.expanduser("~")
        test_suffix = "aesop-test-" + str(os.getpid())
        expected_root = os.path.join(home, test_suffix)
        os.makedirs(expected_root, exist_ok=True)

        try:
            # Create aesop.config.json with ~ path
            config_file = os.path.join(self.fixture_root, "aesop.config.json")
            config_data = {"aesop_root": f"~/{test_suffix}"}
            with open(config_file, "w") as f:
                json.dump(config_data, f)

            # Ensure env var is not set
            if "AESOP_ROOT" in os.environ:
                del os.environ["AESOP_ROOT"]

            # Load config from fixture location
            config = self._load_config_module(fixture_root=self.fixture_root)

            # Should expand ~ to user home and resolve to expected_root
            self.assertEqual(config.AESOP_ROOT, Path(expected_root))
        finally:
            # Clean up
            if os.path.exists(expected_root):
                shutil.rmtree(expected_root)


class TestHeartbeatPathResolution(unittest.TestCase):
    """Test cases for heartbeat path fallback chain resolution."""

    def setUp(self):
        """Create temporary fixture directory structure."""
        self.fixture_root = tempfile.mkdtemp(prefix="aesop-hb-test-")
        self.state_dir = os.path.join(self.fixture_root, "state")
        self.conductor3_root = os.path.join(self.fixture_root, "conductor3")
        self.conductor3_state = os.path.join(self.conductor3_root, "state")
        self.conductor3_monitor = os.path.join(self.conductor3_root, "monitor")
        os.makedirs(self.state_dir, exist_ok=True)
        os.makedirs(self.conductor3_state, exist_ok=True)
        os.makedirs(self.conductor3_monitor, exist_ok=True)

        # Save original env
        self.orig_aesop_root = os.environ.get("AESOP_ROOT")
        self.orig_hb_env = os.environ.get("AESOP_WATCHDOG_HEARTBEAT")
        self.orig_mhb_env = os.environ.get("AESOP_MONITOR_HEARTBEAT")
        self.orig_conductor3_root = os.environ.get("AESOP_CONDUCTOR3_ROOT")

    def tearDown(self):
        """Clean up temporary fixture and restore environment."""
        # Restore original env
        if self.orig_aesop_root is not None:
            os.environ["AESOP_ROOT"] = self.orig_aesop_root
        elif "AESOP_ROOT" in os.environ:
            del os.environ["AESOP_ROOT"]

        if self.orig_hb_env is not None:
            os.environ["AESOP_WATCHDOG_HEARTBEAT"] = self.orig_hb_env
        elif "AESOP_WATCHDOG_HEARTBEAT" in os.environ:
            del os.environ["AESOP_WATCHDOG_HEARTBEAT"]

        if self.orig_mhb_env is not None:
            os.environ["AESOP_MONITOR_HEARTBEAT"] = self.orig_mhb_env
        elif "AESOP_MONITOR_HEARTBEAT" in os.environ:
            del os.environ["AESOP_MONITOR_HEARTBEAT"]

        if self.orig_conductor3_root is not None:
            os.environ["AESOP_CONDUCTOR3_ROOT"] = self.orig_conductor3_root
        elif "AESOP_CONDUCTOR3_ROOT" in os.environ:
            del os.environ["AESOP_CONDUCTOR3_ROOT"]

        # Clean up temp dir
        if os.path.exists(self.fixture_root):
            shutil.rmtree(self.fixture_root)

        # Remove cached config module
        if "ui.config" in sys.modules:
            del sys.modules["ui.config"]

    def _load_config_module_with_conductor3(self, fixture_root):
        """Load config with AESOP_ROOT set to fixture_root and conductor3 in fixture."""
        # Set AESOP_ROOT to point to fixture (parent of ui/)
        os.environ["AESOP_ROOT"] = fixture_root
        # Set AESOP_CONDUCTOR3_ROOT to fixture conductor3 directory
        os.environ["AESOP_CONDUCTOR3_ROOT"] = self.conductor3_root

        # Create ui directory if needed
        ui_dir = os.path.join(fixture_root, "ui")
        os.makedirs(ui_dir, exist_ok=True)

        # Copy config.py to fixture ui directory
        actual_config_py = Path(__file__).parent.parent / "ui" / "config.py"
        fixture_config_py = os.path.join(ui_dir, "config.py")
        with open(actual_config_py, "r") as f:
            config_content = f.read()
        with open(fixture_config_py, "w") as f:
            f.write(config_content)

        # Remove cached config module
        if "ui.config" in sys.modules:
            del sys.modules["ui.config"]

        # Import config module
        import importlib.util
        spec = importlib.util.spec_from_file_location("ui.config", fixture_config_py)
        config = importlib.util.module_from_spec(spec)
        sys.modules["ui.config"] = config
        spec.loader.exec_module(config)
        return config

    def test_heartbeat_prefers_conductor3_when_present(self):
        """Test that watchdog heartbeat prefers conductor3/state over local state."""
        # Create heartbeat files in both locations
        conductor3_hb = os.path.join(self.conductor3_state, ".watchdog-heartbeat")
        local_hb = os.path.join(self.state_dir, ".watchdog-heartbeat")

        with open(conductor3_hb, "w") as f:
            f.write("1234567890")
        with open(local_hb, "w") as f:
            f.write("1234567891")

        config = self._load_config_module_with_conductor3(self.fixture_root)

        # Should prefer conductor3 location
        self.assertEqual(config.WATCHDOG_HEARTBEAT, Path(conductor3_hb))
        self.assertEqual(config.WATCHDOG_HEARTBEAT_AVAILABILITY, "configured")

    def test_monitor_heartbeat_prefers_conductor3_when_present(self):
        """Test that monitor heartbeat prefers conductor3/monitor over local state."""
        # Create heartbeat files in both locations
        conductor3_mhb = os.path.join(self.conductor3_monitor, ".monitor-heartbeat")
        local_mhb = os.path.join(self.state_dir, ".monitor-heartbeat")

        with open(conductor3_mhb, "w") as f:
            f.write("1234567890")
        with open(local_mhb, "w") as f:
            f.write("1234567891")

        config = self._load_config_module_with_conductor3(self.fixture_root)

        # Should prefer conductor3 location
        self.assertEqual(config.MONITOR_HEARTBEAT, Path(conductor3_mhb))
        self.assertEqual(config.MONITOR_HEARTBEAT_AVAILABILITY, "configured")

    def test_heartbeat_falls_back_to_local_when_conductor3_missing(self):
        """Test that watchdog heartbeat falls back to local state when conductor3 missing."""
        # Create heartbeat only in local location
        local_hb = os.path.join(self.state_dir, ".watchdog-heartbeat")
        with open(local_hb, "w") as f:
            f.write("1234567890")

        config = self._load_config_module_with_conductor3(self.fixture_root)

        # Should fall back to local location
        self.assertEqual(config.WATCHDOG_HEARTBEAT, Path(local_hb))
        self.assertEqual(config.WATCHDOG_HEARTBEAT_AVAILABILITY, "configured")

    def test_heartbeat_degrades_when_missing(self):
        """Test that heartbeat availability is 'not configured' when file missing."""
        # Don't create any heartbeat files
        config = self._load_config_module_with_conductor3(self.fixture_root)

        # Should mark as not configured
        self.assertEqual(config.WATCHDOG_HEARTBEAT_AVAILABILITY, "not configured")

    def test_monitor_heartbeat_degrades_when_missing(self):
        """Test that monitor heartbeat availability is 'not configured' when file missing."""
        # Don't create any heartbeat files
        config = self._load_config_module_with_conductor3(self.fixture_root)

        # Should mark as not configured
        self.assertEqual(config.MONITOR_HEARTBEAT_AVAILABILITY, "not configured")

    def test_env_var_overrides_conductor3(self):
        """Test that env var AESOP_WATCHDOG_HEARTBEAT takes precedence."""
        # Create a custom heartbeat location
        custom_hb = os.path.join(self.fixture_root, "custom-hb")
        os.makedirs(custom_hb, exist_ok=True)
        custom_hb_file = os.path.join(custom_hb, ".watchdog-heartbeat")
        with open(custom_hb_file, "w") as f:
            f.write("1234567890")

        # Also create conductor3 one
        conductor3_hb = os.path.join(self.conductor3_state, ".watchdog-heartbeat")
        with open(conductor3_hb, "w") as f:
            f.write("1234567891")

        os.environ["AESOP_WATCHDOG_HEARTBEAT"] = custom_hb_file

        config = self._load_config_module_with_conductor3(self.fixture_root)

        # Should use env var location
        self.assertEqual(config.WATCHDOG_HEARTBEAT, Path(custom_hb_file))
        self.assertEqual(config.WATCHDOG_HEARTBEAT_AVAILABILITY, "configured")

    def test_env_var_expanduser_works(self):
        """Test that env var paths with ~/ are expanded and used."""
        # Create a custom heartbeat location in home directory
        home = os.path.expanduser("~")
        custom_hb_dir = os.path.join(home, f"aesop-test-hb-{os.getpid()}")
        os.makedirs(custom_hb_dir, exist_ok=True)
        custom_hb_file = os.path.join(custom_hb_dir, ".watchdog-heartbeat")

        try:
            # Write heartbeat file
            with open(custom_hb_file, "w") as f:
                f.write("1234567890")

            # Set env var with ~/ notation
            test_suffix = f"aesop-test-hb-{os.getpid()}"
            os.environ["AESOP_WATCHDOG_HEARTBEAT"] = f"~/{test_suffix}/.watchdog-heartbeat"

            config = self._load_config_module_with_conductor3(self.fixture_root)

            # Path should be expanded
            expanded_path = Path(os.environ["AESOP_WATCHDOG_HEARTBEAT"]).expanduser()
            self.assertEqual(config.WATCHDOG_HEARTBEAT, expanded_path)
            # Should be marked as configured since file exists
            self.assertEqual(config.WATCHDOG_HEARTBEAT_AVAILABILITY, "configured")
        finally:
            # Clean up
            if os.path.exists(custom_hb_dir):
                shutil.rmtree(custom_hb_dir)


if __name__ == "__main__":
    unittest.main()
