#!/usr/bin/env python3
"""
TDD tests for receipt key path resolution with fallback to default path.

Tests three cases:
(a) When AESOP_RECEIPT_KEY is unset and a key file exists at the default path,
    use the default path
(b) When neither AESOP_RECEIPT_KEY env var nor default path exist, return None
    (caller decides warning vs fail-open)
(c) When both exist, AESOP_RECEIPT_KEY wins (env var takes precedence)
"""

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
TOOLS = REPO_ROOT / "tools"
sys.path.insert(0, str(TOOLS))


def _load(name):
    spec = importlib.util.spec_from_file_location(name + "_under_test", TOOLS / (name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


rc = _load("receipt_common")


class TestKeyPathResolution(unittest.TestCase):
    """TDD tests for resolve_receipt_key_path()."""

    def test_env_var_takes_precedence_over_default(self):
        """When both env var and default path exist, env var wins."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)

            # Create a default key file
            default_key = tmp_path / ".aesop" / "receipt_key.pem"
            default_key.parent.mkdir(parents=True, exist_ok=True)
            default_key.write_text("default_key_content", encoding="utf-8")

            # Create an env var key file
            env_key = tmp_path / "env_key.pem"
            env_key.write_text("env_key_content", encoding="utf-8")

            # Mock HOME and AESOP_RECEIPT_KEY
            environ = {
                "HOME": str(tmp_path),
                rc.KEY_ENV: str(env_key),
            }

            result = rc.resolve_receipt_key_path(environ=environ)
            self.assertEqual(result, str(env_key))

    def test_fallback_to_default_when_env_unset(self):
        """When AESOP_RECEIPT_KEY is unset and default path exists, use default."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)

            # Create a default key file
            default_key = tmp_path / ".aesop" / "receipt_key.pem"
            default_key.parent.mkdir(parents=True, exist_ok=True)
            default_key.write_text("default_key_content", encoding="utf-8")

            # Mock HOME without AESOP_RECEIPT_KEY
            environ = {
                "HOME": str(tmp_path),
            }

            result = rc.resolve_receipt_key_path(environ=environ)
            self.assertEqual(result, str(default_key))

    def test_return_none_when_neither_env_nor_default_exist(self):
        """When neither env var nor default path exist, return None."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)

            # Don't create any key files
            environ = {
                "HOME": str(tmp_path),
            }

            result = rc.resolve_receipt_key_path(environ=environ)
            self.assertIsNone(result)

    def test_respects_aesop_home_override(self):
        """When AESOP_HOME is set, use it instead of HOME for default path."""
        with tempfile.TemporaryDirectory() as tmp1:
            with tempfile.TemporaryDirectory() as tmp2:
                tmp1_path = Path(tmp1)
                tmp2_path = Path(tmp2)

                # Create default key in AESOP_HOME
                default_key = tmp2_path / ".aesop" / "receipt_key.pem"
                default_key.parent.mkdir(parents=True, exist_ok=True)
                default_key.write_text("aesop_home_key", encoding="utf-8")

                # Mock HOME and AESOP_HOME
                environ = {
                    "HOME": str(tmp1_path),
                    "AESOP_HOME": str(tmp2_path),
                }

                result = rc.resolve_receipt_key_path(environ=environ)
                self.assertEqual(result, str(default_key))

    def test_env_var_wins_over_aesop_home_default(self):
        """AESOP_RECEIPT_KEY takes precedence over both HOME and AESOP_HOME."""
        with tempfile.TemporaryDirectory() as tmp1:
            with tempfile.TemporaryDirectory() as tmp2:
                with tempfile.TemporaryDirectory() as tmp3:
                    tmp1_path = Path(tmp1)
                    tmp2_path = Path(tmp2)
                    tmp3_path = Path(tmp3)

                    # Create default keys in both HOME and AESOP_HOME
                    home_key = tmp1_path / ".aesop" / "receipt_key.pem"
                    home_key.parent.mkdir(parents=True, exist_ok=True)
                    home_key.write_text("home_key", encoding="utf-8")

                    aesop_key = tmp2_path / ".aesop" / "receipt_key.pem"
                    aesop_key.parent.mkdir(parents=True, exist_ok=True)
                    aesop_key.write_text("aesop_key", encoding="utf-8")

                    # Create env var key
                    env_key = tmp3_path / "env_key.pem"
                    env_key.write_text("env_key", encoding="utf-8")

                    # All three set
                    environ = {
                        "HOME": str(tmp1_path),
                        "AESOP_HOME": str(tmp2_path),
                        rc.KEY_ENV: str(env_key),
                    }

                    result = rc.resolve_receipt_key_path(environ=environ)
                    self.assertEqual(result, str(env_key))

    def test_no_home_no_aesop_home_returns_none(self):
        """When HOME and AESOP_HOME are both missing, return None."""
        environ = {}
        result = rc.resolve_receipt_key_path(environ=environ)
        self.assertIsNone(result)


def suite():
    """Declare test suite."""
    loader = unittest.TestLoader()
    suite_ = unittest.TestSuite()
    suite_.addTests(loader.loadTestsFromTestCase(TestKeyPathResolution))
    return suite_


if __name__ == "__main__":
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite())
    sys.exit(0 if result.wasSuccessful() else 1)
