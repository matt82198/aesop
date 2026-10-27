"""Unit tests for ui/serve.py alert path resolution.

Contract: security alerts live in state/SECURITY-ALERTS.log (canonical location used by
daemons and monitor) — NOT scan/. The bug was serve.py reading from scan/ instead of state/.

Run: python -m unittest tests.test_serve

# secretscan: allow-pattern-docs
Test fixtures use dummy secret-like values for validation; these are test-only, never committed.
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from tests.sys_modules_restore import snapshot_sys_modules


# Scrub the state-root env vars for this whole module. A lane shell (or a daemon)
# that exports AESOP_STATE_ROOT / CONDUCTOR_ROOT would otherwise make serve.py read
# the live box state instead of each test's fixture (test-process-isolation hygiene).
_STATE_ENV_KEYS = ("AESOP_STATE_ROOT", "CONDUCTOR_ROOT")
_SAVED_STATE_ENV = {}


def setUpModule():
    for env_name in _STATE_ENV_KEYS:
        _SAVED_STATE_ENV[env_name] = os.environ.pop(env_name, None)


def tearDownModule():
    for env_name, value in _SAVED_STATE_ENV.items():
        if value is None:
            os.environ.pop(env_name, None)
        else:
            os.environ[env_name] = value
    _SAVED_STATE_ENV.clear()


class TestServeAlertPath(unittest.TestCase):
    """Test cases for ui/serve.py alert path resolution."""

    def setUp(self):
        """Create temporary fixture directory structure."""
        self.fixture_root = tempfile.mkdtemp(prefix="aesop-serve-test-")
        self.state_dir = os.path.join(self.fixture_root, "state")
        self.scan_dir = os.path.join(self.fixture_root, "scan")
        os.makedirs(self.state_dir, exist_ok=True)
        os.makedirs(self.scan_dir, exist_ok=True)

        # Save original env
        self.orig_aesop_root = os.environ.get("AESOP_ROOT")

    def tearDown(self):
        """Clean up temporary fixture."""
        # Restore original env
        if self.orig_aesop_root is not None:
            os.environ["AESOP_ROOT"] = self.orig_aesop_root
        elif "AESOP_ROOT" in os.environ:
            del os.environ["AESOP_ROOT"]

        # Clean up temp dir
        import shutil
        if os.path.exists(self.fixture_root):
            shutil.rmtree(self.fixture_root)

    def _load_serve_module(self):
        """Dynamically import serve.py with fixture AESOP_ROOT."""
        # Set fixture AESOP_ROOT before importing
        self.addCleanup(snapshot_sys_modules("ui.serve", "ui"))
        os.environ["AESOP_ROOT"] = self.fixture_root

        # Remove cached serve module if it exists
        if "ui.serve" in sys.modules:
            del sys.modules["ui.serve"]
        if "ui" in sys.modules:
            del sys.modules["ui"]

        # Import serve module
        serve_path = Path(__file__).parent.parent / "ui" / "serve.py"
        import importlib.util
        spec = importlib.util.spec_from_file_location("serve", serve_path)
        serve = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(serve)
        return serve

    def test_alerts_in_state_directory_are_read(self):
        """Alert lines in state/SECURITY-ALERTS.log must be counted (canonical path)."""
        # Write HIGH alert to state/ (canonical location)
        alert_file = os.path.join(self.state_dir, "SECURITY-ALERTS.log")
        with open(alert_file, "w") as f:
            f.write("2026-07-12T00:00:00Z HIGH test alert for fixture\n")

        # Load serve module with fixture
        serve = self._load_serve_module()

        # Get alerts
        alerts = serve.get_alerts()

        # Verify alert was read from state/
        self.assertGreater(
            alerts["count"],
            0,
            "Alert written to state/SECURITY-ALERTS.log must be counted "
            "(this is the canonical location used by daemons)"
        )
        self.assertEqual(len(alerts["lines"]), 1)
        self.assertIn("HIGH", alerts["lines"][0])

    def test_alerts_in_scan_directory_are_not_read(self):
        """Alert lines in scan/ directory must NOT be read (non-canonical path)."""
        # Write decoy alert to scan/ (non-canonical, old/wrong location)
        scan_alert_file = os.path.join(self.scan_dir, "SECURITY-ALERTS.log")
        with open(scan_alert_file, "w") as f:
            f.write("2026-07-12T00:00:00Z CRITICAL decoy alert in scan/\n")

        # Load serve module with fixture
        serve = self._load_serve_module()

        # Get alerts
        alerts = serve.get_alerts()

        # Verify alert in scan/ was NOT read
        self.assertEqual(
            alerts["count"],
            0,
            "Alerts in scan/SECURITY-ALERTS.log must not be read — "
            "scan/ is not the canonical location (use state/ instead)"
        )

    def test_degrades_gracefully_when_no_alerts_exist(self):
        """Must not crash when SECURITY-ALERTS.log doesn't exist."""
        # Don't create any alert file

        # Load serve module with fixture
        serve = self._load_serve_module()

        # Get alerts - should not crash
        alerts = serve.get_alerts()

        # Verify graceful degradation
        self.assertEqual(alerts["count"], 0)
        self.assertEqual(alerts["lines"], [])


class TestBacklogEndpoint(unittest.TestCase):
    """Test cases for /api/backlog endpoint."""

    def setUp(self):
        """Create temporary fixture directory structure."""
        self.fixture_root = tempfile.mkdtemp(prefix="aesop-backlog-endpoint-test-")
        self.state_dir = os.path.join(self.fixture_root, "state")
        os.makedirs(self.state_dir, exist_ok=True)

        # Save original env
        self.orig_aesop_root = os.environ.get("AESOP_ROOT")

    def tearDown(self):
        """Clean up temporary fixture."""
        # Restore original env
        if self.orig_aesop_root is not None:
            os.environ["AESOP_ROOT"] = self.orig_aesop_root
        elif "AESOP_ROOT" in os.environ:
            del os.environ["AESOP_ROOT"]

        # Clean up temp dir
        import shutil
        if os.path.exists(self.fixture_root):
            shutil.rmtree(self.fixture_root)

    def _load_serve_module(self):
        """Dynamically import serve.py with fixture AESOP_ROOT."""
        self.addCleanup(snapshot_sys_modules("ui.serve", "ui"))
        os.environ["AESOP_ROOT"] = self.fixture_root

        if "ui.serve" in sys.modules:
            del sys.modules["ui.serve"]
        if "ui" in sys.modules:
            del sys.modules["ui"]

        serve_path = Path(__file__).parent.parent / "ui" / "serve.py"
        import importlib.util
        spec = importlib.util.spec_from_file_location("serve", serve_path)
        serve = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(serve)
        return serve

    def test_backlog_endpoint_returns_valid_json(self):
        """Verify /api/backlog endpoint returns valid JSON structure."""
        backlog_content = """# Audit backlog

## P0 — correctness / security (do first)

- ✅ **[sec] Item 1**
- 🔵 **[arch] Item 2**
"""
        backlog_path = os.path.join(self.fixture_root, "AUDIT-BACKLOG.md")
        with open(backlog_path, "w", encoding="utf-8") as f:
            f.write(backlog_content)

        serve = self._load_serve_module()
        data = serve.parse_audit_backlog()

        # Verify JSON structure
        self.assertIn("tiers", data)
        self.assertIsInstance(data["tiers"], list)
        if data["tiers"]:
            tier = data["tiers"][0]
            self.assertIn("tier", tier)
            self.assertIn("items", tier)
            self.assertIn("done", tier)
            self.assertIn("inflight", tier)
            self.assertIn("todo", tier)
            self.assertIn("total", tier)


class TestConfigPrecedence(unittest.TestCase):
    """Test cases for config file precedence (env > config > default)."""

    def setUp(self):
        """Create temporary fixture directory structure."""
        self.fixture_root = tempfile.mkdtemp(prefix="aesop-config-precedence-test-")
        self.state_dir = os.path.join(self.fixture_root, "state")
        self.config_state_dir = os.path.join(self.fixture_root, "config-state")
        os.makedirs(self.state_dir, exist_ok=True)
        os.makedirs(self.config_state_dir, exist_ok=True)

        # Save original env
        self.orig_aesop_root = os.environ.get("AESOP_ROOT")

    def tearDown(self):
        """Clean up temporary fixture."""
        if self.orig_aesop_root is not None:
            os.environ["AESOP_ROOT"] = self.orig_aesop_root
        elif "AESOP_ROOT" in os.environ:
            del os.environ["AESOP_ROOT"]

        import shutil
        if os.path.exists(self.fixture_root):
            shutil.rmtree(self.fixture_root)

    def _load_serve_module(self):
        """Dynamically import serve.py with fixture AESOP_ROOT."""
        self.addCleanup(snapshot_sys_modules("ui.serve", "ui"))
        os.environ["AESOP_ROOT"] = self.fixture_root

        if "ui.serve" in sys.modules:
            del sys.modules["ui.serve"]
        if "ui" in sys.modules:
            del sys.modules["ui"]

        serve_path = Path(__file__).parent.parent / "ui" / "serve.py"
        import importlib.util
        spec = importlib.util.spec_from_file_location("serve", serve_path)
        serve = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(serve)
        return serve

    def test_config_state_root_precedence(self):
        """Config state_root must be honored when env var is unset."""
        # Write alert to config-specified state directory
        config_alert_file = os.path.join(self.config_state_dir, "SECURITY-ALERTS.log")
        with open(config_alert_file, "w") as f:
            f.write("2026-07-12T00:00:00Z HIGH config-specified alert\n")

        # Write aesop.config.json with custom state_root
        config_file = os.path.join(self.fixture_root, "aesop.config.json")
        with open(config_file, "w") as f:
            json.dump({
                "state_root": self.config_state_dir,
                "repos": []
            }, f)

        # Load serve module (will use config file for state_root)
        serve = self._load_serve_module()

        # Get alerts - should read from config-specified state directory
        alerts = serve.get_alerts()

        self.assertGreater(
            alerts["count"],
            0,
            "Config file state_root must be honored when env var is unset"
        )
        self.assertEqual(len(alerts["lines"]), 1)
        self.assertIn("HIGH", alerts["lines"][0])


class TestAuditBacklogParser(unittest.TestCase):
    """Test cases for parsing AUDIT-BACKLOG.md format."""

    def setUp(self):
        """Create temporary fixture directory structure."""
        self.fixture_root = tempfile.mkdtemp(prefix="aesop-backlog-test-")
        self.state_dir = os.path.join(self.fixture_root, "state")
        os.makedirs(self.state_dir, exist_ok=True)

        # Save original env
        self.orig_aesop_root = os.environ.get("AESOP_ROOT")

    def tearDown(self):
        """Clean up temporary fixture."""
        # Restore original env
        if self.orig_aesop_root is not None:
            os.environ["AESOP_ROOT"] = self.orig_aesop_root
        elif "AESOP_ROOT" in os.environ:
            del os.environ["AESOP_ROOT"]

        # Clean up temp dir
        import shutil
        if os.path.exists(self.fixture_root):
            shutil.rmtree(self.fixture_root)

    def _load_serve_module(self):
        """Dynamically import serve.py with fixture AESOP_ROOT."""
        # Set fixture AESOP_ROOT before importing
        self.addCleanup(snapshot_sys_modules("ui.serve", "ui"))
        os.environ["AESOP_ROOT"] = self.fixture_root

        # Remove cached serve module if it exists
        if "ui.serve" in sys.modules:
            del sys.modules["ui.serve"]
        if "ui" in sys.modules:
            del sys.modules["ui"]

        # Import serve module
        serve_path = Path(__file__).parent.parent / "ui" / "serve.py"
        import importlib.util
        spec = importlib.util.spec_from_file_location("serve", serve_path)
        serve = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(serve)
        return serve

    def test_parse_basic_backlog_structure(self):
        """Parse P0, P1, P2, and Needs a user decision sections with correct item counts."""
        backlog_content = """# Audit backlog — five-lens specialist review (2026-07-12)

## P0 — correctness / security (do first)

- ✅ **[sec] First P0 item**
  Some description here.
- 🔵 **[arch] Second P0 item**
- ⬜ **[bash] Third P0 item**

## P1 — hardening / robustness

- ⬜ **[js] First P1 item**
- ✅ **[sec] Second P1 item**

## P2 — honesty / polish / docs

- 🔵 **[arch] Only P2 item**

## Needs a user decision (⏸)

- ⏸ **[honest] User decision item**

## Landing log

This section is NOT parsed.
"""
        # Write fixture backlog
        backlog_path = os.path.join(self.fixture_root, "AUDIT-BACKLOG.md")
        with open(backlog_path, "w", encoding="utf-8") as f:
            f.write(backlog_content)

        # Load serve module
        serve = self._load_serve_module()

        # Parse backlog
        result = serve.parse_audit_backlog()

        # Verify structure
        self.assertIsNotNone(result)
        self.assertIn("tiers", result)

        # Check P0
        p0_tier = next((t for t in result["tiers"] if t["tier"] == "P0"), None)
        self.assertIsNotNone(p0_tier, "P0 tier should exist")
        self.assertEqual(len(p0_tier["items"]), 3, "P0 should have 3 items")

        # Check P1
        p1_tier = next((t for t in result["tiers"] if t["tier"] == "P1"), None)
        self.assertIsNotNone(p1_tier, "P1 tier should exist")
        self.assertEqual(len(p1_tier["items"]), 2, "P1 should have 2 items")

        # Check P2
        p2_tier = next((t for t in result["tiers"] if t["tier"] == "P2"), None)
        self.assertIsNotNone(p2_tier, "P2 tier should exist")
        self.assertEqual(len(p2_tier["items"]), 1, "P2 should have 1 item")

        # Check Needs decision (⏸)
        decision_tier = next((t for t in result["tiers"] if t["tier"] == "Needs decision"), None)
        self.assertIsNotNone(decision_tier, "Needs decision tier should exist")
        self.assertEqual(len(decision_tier["items"]), 1, "Needs decision should have 1 item")

    def test_parse_backlog_status_glyphs(self):
        """Verify status glyphs (✅, 🔵, ⬜, ⏸) are correctly classified."""
        backlog_content = """# Audit backlog

## P0 — correctness / security (do first)

- ✅ **[sec] Done item**
- 🔵 **[arch] In flight item**
- ⬜ **[bash] Unclaimed item**
- ⏸ **[js] User decision item**
"""
        backlog_path = os.path.join(self.fixture_root, "AUDIT-BACKLOG.md")
        with open(backlog_path, "w", encoding="utf-8") as f:
            f.write(backlog_content)

        serve = self._load_serve_module()
        result = serve.parse_audit_backlog()

        p0_tier = result["tiers"][0]
        items = p0_tier["items"]

        # Verify status glyphs
        self.assertEqual(items[0]["status"], "✅", "First item should have done status")
        self.assertEqual(items[1]["status"], "🔵", "Second item should have in-flight status")
        self.assertEqual(items[2]["status"], "⬜", "Third item should have unclaimed status")
        self.assertEqual(items[3]["status"], "⏸", "Fourth item should have decision status")

    def test_parse_backlog_extracts_tag_and_title(self):
        """Verify tag (e.g. [sec]) and title are correctly extracted."""
        backlog_content = """# Audit backlog

## P0 — correctness / security (do first)

- ✅ **[sec] This is the title**
- 🔵 **[arch] Another title with [brackets] in it**
"""
        backlog_path = os.path.join(self.fixture_root, "AUDIT-BACKLOG.md")
        with open(backlog_path, "w", encoding="utf-8") as f:
            f.write(backlog_content)

        serve = self._load_serve_module()
        result = serve.parse_audit_backlog()

        items = result["tiers"][0]["items"]

        # Verify tag and title extraction
        self.assertEqual(items[0]["tag"], "[sec]", "First item tag should be [sec]")
        self.assertEqual(items[0]["title"], "This is the title", "First item title should be extracted")

        self.assertEqual(items[1]["tag"], "[arch]", "Second item tag should be [arch]")
        self.assertEqual(items[1]["title"], "Another title with [brackets] in it", "Second item title should handle brackets")

    def test_parse_backlog_counts_status_per_tier(self):
        """Verify done/inflight/todo counts are correctly calculated per tier."""
        backlog_content = """# Audit backlog

## P0 — correctness / security (do first)

- ✅ **[sec] Done 1**
- ✅ **[sec] Done 2**
- 🔵 **[arch] In flight 1**
- 🔵 **[arch] In flight 2**
- ⬜ **[bash] Todo 1**
- ⬜ **[bash] Todo 2**
- ⬜ **[bash] Todo 3**
"""
        backlog_path = os.path.join(self.fixture_root, "AUDIT-BACKLOG.md")
        with open(backlog_path, "w", encoding="utf-8") as f:
            f.write(backlog_content)

        serve = self._load_serve_module()
        result = serve.parse_audit_backlog()

        p0_tier = result["tiers"][0]

        # Verify counts
        self.assertEqual(p0_tier["done"], 2, "P0 should have 2 done items")
        self.assertEqual(p0_tier["inflight"], 2, "P0 should have 2 in-flight items")
        self.assertEqual(p0_tier["todo"], 3, "P0 should have 3 todo items")
        self.assertEqual(p0_tier["total"], 7, "P0 should have 7 total items")

    def test_parse_backlog_missing_file_degrades_gracefully(self):
        """Must not crash when AUDIT-BACKLOG.md doesn't exist."""
        # Don't create backlog file

        serve = self._load_serve_module()
        result = serve.parse_audit_backlog()

        # Should return graceful empty structure
        self.assertIsNotNone(result)
        self.assertIn("tiers", result)
        self.assertEqual(len(result["tiers"]), 0, "Should return empty tiers on missing file")

    def test_parse_backlog_ignores_landing_log_and_dispatch_plan(self):
        """Must not parse items after 'Landing log' or 'Dispatch plan' sections."""
        backlog_content = """# Audit backlog

## P0 — correctness / security (do first)

- ✅ **[sec] Real P0 item**

## Landing log

This should NOT be parsed:
- ⬜ **[fake] Fake landing log item**

## Dispatch plan

- ⬜ **[fake] Fake dispatch plan item**
"""
        backlog_path = os.path.join(self.fixture_root, "AUDIT-BACKLOG.md")
        with open(backlog_path, "w", encoding="utf-8") as f:
            f.write(backlog_content)

        serve = self._load_serve_module()
        result = serve.parse_audit_backlog()

        p0_tier = result["tiers"][0]

        # Should only have 1 real item, not the fake ones
        self.assertEqual(len(p0_tier["items"]), 1, "Should ignore Landing log and Dispatch plan sections")
        self.assertEqual(p0_tier["items"][0]["title"], "Real P0 item")


class TestCSRFProtection(unittest.TestCase):
    """Test cases for CSRF protection on /submit endpoint."""

    def setUp(self):
        """Create temporary fixture directory structure."""
        self.fixture_root = tempfile.mkdtemp(prefix="aesop-csrf-test-")
        self.state_dir = os.path.join(self.fixture_root, "state")
        os.makedirs(self.state_dir, exist_ok=True)

        # Save original env
        self.orig_aesop_root = os.environ.get("AESOP_ROOT")

    def tearDown(self):
        """Clean up temporary fixture."""
        if self.orig_aesop_root is not None:
            os.environ["AESOP_ROOT"] = self.orig_aesop_root
        elif "AESOP_ROOT" in os.environ:
            del os.environ["AESOP_ROOT"]

        import shutil
        if os.path.exists(self.fixture_root):
            shutil.rmtree(self.fixture_root)

    def _load_serve_module(self):
        """Dynamically import serve.py with fixture AESOP_ROOT."""
        self.addCleanup(snapshot_sys_modules("ui.serve", "ui"))
        os.environ["AESOP_ROOT"] = self.fixture_root

        if "ui.serve" in sys.modules:
            del sys.modules["ui.serve"]
        if "ui" in sys.modules:
            del sys.modules["ui"]

        serve_path = Path(__file__).parent.parent / "ui" / "serve.py"
        import importlib.util
        spec = importlib.util.spec_from_file_location("serve", serve_path)
        serve = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(serve)
        return serve

    def test_session_token_generated_at_startup(self):
        """Session token must be generated at startup and persisted to state/.ui-session-token."""
        serve = self._load_serve_module()

        # Check that token file is created
        token_file = os.path.join(self.state_dir, ".ui-session-token")
        self.assertTrue(
            os.path.exists(token_file) or hasattr(serve, "SESSION_TOKEN"),
            "Session token must be generated at startup"
        )

    def test_csrf_token_has_adequate_entropy(self):
        """Generated CSRF token must be cryptographically random with adequate length."""
        serve = self._load_serve_module()

        # Check that SESSION_TOKEN or file content is sufficiently long
        if hasattr(serve, "SESSION_TOKEN"):
            token = serve.SESSION_TOKEN
            self.assertGreaterEqual(
                len(token),
                32,
                "CSRF token must have at least 32 characters for adequate entropy"
            )
        else:
            token_file = os.path.join(self.state_dir, ".ui-session-token")
            if os.path.exists(token_file):
                with open(token_file, "r") as f:
                    token = f.read().strip()
                self.assertGreaterEqual(
                    len(token),
                    32,
                    "CSRF token must have at least 32 characters for adequate entropy"
                )

    def test_csrf_validation_rejects_foreign_origin_without_token(self):
        """CSRF validation must reject POST with foreign Origin and no token."""
        serve = self._load_serve_module()

        headers = {"Origin": "https://attacker.com"}
        is_valid, reason = serve.validate_csrf_request(headers, expected_port=8770)

        self.assertFalse(is_valid, "Foreign Origin must be rejected")
        self.assertIn("Foreign", reason)

    def test_csrf_validation_rejects_missing_token(self):
        """CSRF validation must reject POST without X-Aesop-Token."""
        serve = self._load_serve_module()

        headers = {"Origin": "http://127.0.0.1:8770"}
        is_valid, reason = serve.validate_csrf_request(headers, expected_port=8770)

        self.assertFalse(is_valid, "Missing token must be rejected")
        self.assertIn("X-Aesop-Token", reason)

    def test_csrf_validation_rejects_invalid_token(self):
        """CSRF validation must reject POST with invalid token."""
        serve = self._load_serve_module()

        headers = {
            "Origin": "http://127.0.0.1:8770",
            "X-Aesop-Token": "invalid-token-value"
        }
        is_valid, reason = serve.validate_csrf_request(headers, expected_port=8770)

        self.assertFalse(is_valid, "Invalid token must be rejected")
        self.assertIn("Invalid", reason)

    def test_csrf_validation_accepts_valid_token_with_local_origin(self):
        """CSRF validation must accept POST with valid token and local origin."""
        serve = self._load_serve_module()

        headers = {
            "Origin": "http://127.0.0.1:8770",
            "X-Aesop-Token": serve.SESSION_TOKEN
        }
        is_valid, reason = serve.validate_csrf_request(headers, expected_port=8770)

        self.assertTrue(is_valid, "Valid token with local origin must be accepted")
        self.assertIsNone(reason)

    def test_csrf_validation_accepts_valid_token_with_localhost(self):
        """CSRF validation must accept POST with valid token and localhost origin."""
        serve = self._load_serve_module()

        headers = {
            "Origin": "http://localhost:8770",
            "X-Aesop-Token": serve.SESSION_TOKEN
        }
        is_valid, reason = serve.validate_csrf_request(headers, expected_port=8770)

        self.assertTrue(is_valid, "Valid token with localhost origin must be accepted")
        self.assertIsNone(reason)

    def test_csrf_validation_rejects_foreign_origin_even_with_valid_token(self):
        """CSRF validation must reject foreign Origin even if token is valid (defense-in-depth)."""
        serve = self._load_serve_module()

        headers = {
            "Origin": "https://attacker.com",
            "X-Aesop-Token": serve.SESSION_TOKEN
        }
        is_valid, reason = serve.validate_csrf_request(headers, expected_port=8770)

        self.assertFalse(is_valid, "Foreign Origin must be rejected even with valid token")
        self.assertIn("Foreign", reason)

    def test_csrf_validation_allows_no_origin_with_valid_token(self):
        """CSRF validation must allow POST with valid token and no Origin/Referer."""
        serve = self._load_serve_module()

        headers = {
            "X-Aesop-Token": serve.SESSION_TOKEN
        }
        is_valid, reason = serve.validate_csrf_request(headers, expected_port=8770)

        self.assertTrue(is_valid, "Valid token with no Origin/Referer must be accepted (CLI use case)")
        self.assertIsNone(reason)


class TestCSRFTokenFileSecurity(unittest.TestCase):
    """Test cases for CSRF token file creation security (P2 TOCTOU fix).

    Validates that the token file is created atomically with restrictive permissions
    (0600 on POSIX), avoiding any TOCTOU window where the file exists with
    world-readable permissions.
    """

    def setUp(self):
        """Create temporary fixture directory structure."""
        self.fixture_root = tempfile.mkdtemp(prefix="aesop-csrf-toctou-test-")
        self.state_dir = os.path.join(self.fixture_root, "state")
        os.makedirs(self.state_dir, exist_ok=True)

        # Save original env
        self.orig_aesop_root = os.environ.get("AESOP_ROOT")

    def tearDown(self):
        """Clean up temporary fixture."""
        if self.orig_aesop_root is not None:
            os.environ["AESOP_ROOT"] = self.orig_aesop_root
        elif "AESOP_ROOT" in os.environ:
            del os.environ["AESOP_ROOT"]

        import shutil
        if os.path.exists(self.fixture_root):
            shutil.rmtree(self.fixture_root)

    def _load_serve_module(self):
        """Dynamically import serve.py with fixture AESOP_ROOT."""
        self.addCleanup(snapshot_sys_modules("ui.serve", "ui"))
        os.environ["AESOP_ROOT"] = self.fixture_root

        if "ui.serve" in sys.modules:
            del sys.modules["ui.serve"]
        if "ui" in sys.modules:
            del sys.modules["ui"]

        serve_path = Path(__file__).parent.parent / "ui" / "serve.py"
        import importlib.util
        spec = importlib.util.spec_from_file_location("serve", serve_path)
        serve = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(serve)
        return serve

    def test_token_file_created_with_restricted_permissions_posix(self):
        """After first-run token creation, file mode must be 0600 on POSIX (skip Windows)."""
        import stat

        serve = self._load_serve_module()

        # On POSIX systems (Linux, macOS), verify file mode is 0600
        if os.name == 'posix':  # POSIX systems (Linux/macOS)
            token_file = os.path.join(self.state_dir, ".ui-session-token")
            self.assertTrue(
                os.path.exists(token_file),
                "Token file must exist after generate_session_token()"
            )

            # Get file mode
            file_stat = os.stat(token_file)
            file_mode = stat.S_IMODE(file_stat.st_mode)

            # Verify mode is exactly 0o600 (user read+write only)
            self.assertEqual(
                file_mode, 0o600,
                f"Token file mode must be 0o600 (user-only), got {oct(file_mode)}"
            )
        else:
            # Windows: skip mode check (mode bits ignored), but file should exist
            self.skipTest("File mode checks apply only to POSIX systems")

    def test_token_file_not_world_readable(self):
        """Verify token file is never world-readable (no TOCTOU window)."""
        import stat

        serve = self._load_serve_module()

        # Only check on POSIX systems
        if os.name == 'posix':
            token_file = os.path.join(self.state_dir, ".ui-session-token")
            file_stat = os.stat(token_file)
            file_mode = stat.S_IMODE(file_stat.st_mode)

            # Check that world-read bit is NOT set (S_IROTH = 0o004)
            self.assertFalse(
                file_mode & stat.S_IROTH,
                f"Token file must not be world-readable, mode is {oct(file_mode)}"
            )

            # Check that group-read bit is NOT set (S_IRGRP = 0o040)
            self.assertFalse(
                file_mode & stat.S_IRGRP,
                f"Token file must not be group-readable, mode is {oct(file_mode)}"
            )

    def test_token_reused_from_existing_file(self):
        """If token file already exists, second import must reuse it (no overwrite)."""
        # First, generate a token
        self.addCleanup(snapshot_sys_modules("ui.serve", "ui"))
        serve1 = self._load_serve_module()
        token1 = serve1.SESSION_TOKEN

        # Now reload the serve module in a fresh process context
        if "ui.serve" in sys.modules:
            del sys.modules["ui.serve"]
        if "ui" in sys.modules:
            del sys.modules["ui"]

        # Re-import should read the existing token file, not create a new one
        serve2 = self._load_serve_module()
        token2 = serve2.SESSION_TOKEN

        # Tokens must be identical
        self.assertEqual(
            token1, token2,
            "Existing token file must be reused on second import (idempotent)"
        )

    def test_create_race_condition_handled(self):
        """If file exists during creation (race), must fall back to reading it."""
        # Pre-create a token file with a known value
        token_file = os.path.join(self.state_dir, ".ui-session-token")
        preexisting_token = "x" * 49

        with open(token_file, "w") as f:
            f.write(preexisting_token)

        # Make the directory so serve.py doesn't try to create it
        os.makedirs(self.state_dir, exist_ok=True)

        # Now load serve and check it reads the pre-existing token
        serve = self._load_serve_module()

        # Should have read the pre-existing token, not generated a new one
        self.assertEqual(
            serve.SESSION_TOKEN, preexisting_token,
            "Pre-existing token file must be read and reused (race condition handling)"
        )


if __name__ == "__main__":
    unittest.main()
