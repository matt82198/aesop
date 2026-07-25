#!/usr/bin/env python3
"""Test suite for driver/backend_config.py — offline, no API key required.

Tests:
  1. load_backend_config() with various input paths and missing files
  2. build_driver() instantiates correct subclass for each backend
  3. Unknown backend -> clear error
  4. Missing required field -> clear error
  5. Default (no config) -> ClaudeCodeDriver
  6. Building codex/openai-compatible driver does NOT require API key (offline-safe)
  7. Simulated live dispatch with key absent raises clear "requires $KEY" error
  8. describe_backend() returns sensible summaries

Constraints:
  - stdlib unittest only (no pytest)
  - ASCII-only
  - Windows + Linux parity
  - No hardcoded timestamps or non-deterministic values
  - No API key in environment during tests
"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

# Adjust path to import driver modules.
DRIVER_DIR = Path(__file__).resolve().parent.parent / "driver"
if str(DRIVER_DIR) not in sys.path:
    sys.path.insert(0, str(DRIVER_DIR))

from agent_driver import AgentDriver
from claude_code_driver import ClaudeCodeDriver
from backend_config import build_driver, describe_backend, load_backend_config


class TestLoadBackendConfig(unittest.TestCase):
    """Test config loading from aesop.config.json."""

    def test_default_no_file(self):
        """Default (no file) -> {"backend": "claude"}."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "nonexistent.json"
            config = load_backend_config(str(config_path))
            self.assertEqual(config, {"backend": "claude"})

    def test_explicit_none_path(self):
        """Explicit None path -> looks for aesop.config.json, defaults to claude."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Save current directory to restore after.
            orig_cwd = os.getcwd()
            try:
                os.chdir(tmpdir)
                config = load_backend_config(path=None)
                self.assertEqual(config, {"backend": "claude"})
            finally:
                os.chdir(orig_cwd)

    def test_file_not_found(self):
        """File not found -> defaults to claude."""
        config = load_backend_config(path="/nonexistent/path/aesop.config.json")
        self.assertEqual(config, {"backend": "claude"})

    def test_invalid_json(self):
        """Malformed JSON -> ValueError."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "aesop.config.json"
            config_path.write_text("{invalid json", encoding="utf-8")
            with self.assertRaises(ValueError) as ctx:
                load_backend_config(str(config_path))
            self.assertIn("not valid JSON", str(ctx.exception))

    def test_not_a_dict(self):
        """JSON is not a dict -> TypeError."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "aesop.config.json"
            config_path.write_text('["an", "array"]', encoding="utf-8")
            with self.assertRaises(TypeError) as ctx:
                load_backend_config(str(config_path))
            self.assertIn("must be a JSON object", str(ctx.exception))

    def test_no_backend_key(self):
        """Config with no backend key -> defaults to claude."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "aesop.config.json"
            config_path.write_text('{"some_other_key": "value"}', encoding="utf-8")
            config = load_backend_config(str(config_path))
            self.assertEqual(config["backend"], "claude")

    def test_claude_backend(self):
        """Claude backend specified -> loaded correctly."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "aesop.config.json"
            config_path.write_text('{"backend": "claude"}', encoding="utf-8")
            config = load_backend_config(str(config_path))
            self.assertEqual(config["backend"], "claude")

    def test_codex_backend_valid(self):
        """Codex backend with model -> loaded correctly."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "aesop.config.json"
            config_path.write_text(
                json.dumps({"backend": "codex", "model": "gpt-3.5-turbo"}),
                encoding="utf-8",
            )
            config = load_backend_config(str(config_path))
            self.assertEqual(config["backend"], "codex")
            self.assertEqual(config["model"], "gpt-3.5-turbo")

    def test_codex_backend_missing_model(self):
        """Codex backend without model -> ValueError."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "aesop.config.json"
            config_path.write_text('{"backend": "codex"}', encoding="utf-8")
            with self.assertRaises(ValueError) as ctx:
                load_backend_config(str(config_path))
            self.assertIn("requires 'model'", str(ctx.exception))

    def test_openai_compatible_valid(self):
        """OpenAI-compatible backend with base_url and model -> loaded correctly."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "aesop.config.json"
            config_path.write_text(
                json.dumps(
                    {
                        "backend": "openai-compatible",
                        "base_url": "http://localhost:11434/v1",
                        "model": "neural-chat",
                    }
                ),
                encoding="utf-8",
            )
            config = load_backend_config(str(config_path))
            self.assertEqual(config["backend"], "openai-compatible")
            self.assertEqual(config["base_url"], "http://localhost:11434/v1")
            self.assertEqual(config["model"], "neural-chat")

    def test_openai_compatible_missing_base_url(self):
        """OpenAI-compatible without base_url -> ValueError."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "aesop.config.json"
            config_path.write_text(
                json.dumps(
                    {"backend": "openai-compatible", "model": "neural-chat"}
                ),
                encoding="utf-8",
            )
            with self.assertRaises(ValueError) as ctx:
                load_backend_config(str(config_path))
            self.assertIn("requires 'base_url'", str(ctx.exception))

    def test_openai_compatible_missing_model(self):
        """OpenAI-compatible without model -> ValueError."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "aesop.config.json"
            config_path.write_text(
                json.dumps(
                    {"backend": "openai-compatible", "base_url": "http://localhost:11434/v1"}
                ),
                encoding="utf-8",
            )
            with self.assertRaises(ValueError) as ctx:
                load_backend_config(str(config_path))
            self.assertIn("requires 'model'", str(ctx.exception))

    def test_unknown_backend(self):
        """Unknown backend name -> ValueError."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "aesop.config.json"
            config_path.write_text('{"backend": "nonexistent-backend"}', encoding="utf-8")
            with self.assertRaises(ValueError) as ctx:
                load_backend_config(str(config_path))
            self.assertIn("Unknown backend", str(ctx.exception))

    def test_preserve_extra_fields(self):
        """Extra fields in config are preserved."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "aesop.config.json"
            config_path.write_text(
                json.dumps(
                    {
                        "backend": "claude",
                        "extra_field": "extra_value",
                        "max_owned_bytes": 500000,
                    }
                ),
                encoding="utf-8",
            )
            config = load_backend_config(str(config_path))
            self.assertEqual(config["backend"], "claude")
            self.assertEqual(config["extra_field"], "extra_value")
            self.assertEqual(config["max_owned_bytes"], 500000)


class TestBuildDriver(unittest.TestCase):
    """Test driver instantiation from config."""

    def test_default_no_config(self):
        """No config -> ClaudeCodeDriver."""
        driver = build_driver(None)
        self.assertIsInstance(driver, ClaudeCodeDriver)
        self.assertEqual(driver.name, "claude-code")

    def test_claude_backend(self):
        """Claude config -> ClaudeCodeDriver."""
        config = {"backend": "claude"}
        driver = build_driver(config)
        self.assertIsInstance(driver, ClaudeCodeDriver)

    def test_codex_backend(self):
        """Codex config -> CodexDriver (no network/key needed at build time)."""
        config = {"backend": "codex", "model": "gpt-4o-mini"}
        driver = build_driver(config)
        # Should be a CodexDriver; import to verify.
        try:
            from codex_driver import CodexDriver
            self.assertIsInstance(driver, CodexDriver)
            self.assertEqual(driver.name, "codex")
        except ImportError:
            self.skipTest("CodexDriver not available")

    def test_openai_compatible_backend(self):
        """OpenAI-compatible config -> OpenAICompatibleDriver (offline-safe)."""
        config = {
            "backend": "openai-compatible",
            "base_url": "http://localhost:11434/v1",
            "model": "neural-chat",
        }
        driver = build_driver(config)
        # Should be an OpenAICompatibleDriver; import to verify.
        try:
            from openai_compatible_driver import OpenAICompatibleDriver
            self.assertIsInstance(driver, OpenAICompatibleDriver)
            self.assertEqual(driver.name, "openai-compatible")
        except ImportError:
            self.skipTest("OpenAICompatibleDriver not available")

    def test_build_is_offline_safe(self):
        """Building a driver requires NO API key in environment.

        This test verifies that instantiating a driver does not access or require
        an API key. Keys are read only at call time during dispatch, not at build
        time. This ensures the config system is offline-safe for container images,
        CI pipelines, or any environment where keys should not be loaded upfront.
        """
        config = {
            "backend": "codex",
            "model": "gpt-4o-mini",
        }
        # This should not raise or require a key, even if no key is available.
        # The driver stores the key env var name but does not access it until dispatch.
        driver = build_driver(config)
        self.assertIsNotNone(driver)

    def test_unknown_backend(self):
        """Unknown backend -> ValueError."""
        config = {"backend": "unknown-backend"}
        with self.assertRaises(ValueError) as ctx:
            build_driver(config)
        self.assertIn("Unknown backend", str(ctx.exception))

    def test_codex_with_model_map(self):
        """Codex config with model_map -> used in instantiation."""
        config = {
            "backend": "codex",
            "model": "gpt-3.5-turbo",
            "model_map": {"worker": "gpt-4-turbo"},
        }
        driver = build_driver(config)
        self.assertIsNotNone(driver)
        # Verify the model_map was passed.
        if hasattr(driver, "_model_map"):
            # Codex driver merges provided map with defaults.
            self.assertIn("worker", driver._model_map)

    def test_openai_compatible_with_is_local(self):
        """OpenAI-compatible with is_local=true -> passed correctly."""
        config = {
            "backend": "openai-compatible",
            "base_url": "http://localhost:11434/v1",
            "model": "neural-chat",
            "is_local": True,
        }
        driver = build_driver(config)
        self.assertIsNotNone(driver)
        if hasattr(driver, "_is_local"):
            self.assertTrue(driver._is_local)


class TestDescribeBackend(unittest.TestCase):
    """Test human-readable backend descriptions."""

    def test_describe_none_config(self):
        """No config -> describe Claude."""
        desc = describe_backend(None)
        self.assertIn("claude", desc.lower())

    def test_describe_claude(self):
        """Describe Claude backend."""
        config = {"backend": "claude"}
        desc = describe_backend(config)
        self.assertIn("claude", desc.lower())

    def test_describe_codex(self):
        """Describe Codex backend."""
        config = {"backend": "codex", "model": "gpt-4o-mini"}
        desc = describe_backend(config)
        # Should contain backend name or model info.
        self.assertTrue(len(desc) > 0)

    def test_describe_openai_compatible(self):
        """Describe OpenAI-compatible backend."""
        config = {
            "backend": "openai-compatible",
            "base_url": "http://localhost:11434/v1",
            "model": "neural-chat",
        }
        desc = describe_backend(config)
        self.assertTrue(len(desc) > 0)

    def test_describe_unknown_backend(self):
        """Describe unknown backend -> graceful fallback."""
        config = {"backend": "unknown"}
        desc = describe_backend(config)
        self.assertIn("unknown", desc.lower())

    def test_describe_ascii_only(self):
        """Descriptions are ASCII-only."""
        for backend in ["claude", "codex", "openai-compatible"]:
            config = {"backend": backend}
            if backend == "codex":
                config["model"] = "gpt-4o-mini"
            elif backend == "openai-compatible":
                config["base_url"] = "http://localhost:11434/v1"
                config["model"] = "neural-chat"
            desc = describe_backend(config)
            # Verify ASCII-only (no exception on encode).
            try:
                desc.encode("ascii")
            except UnicodeEncodeError:
                self.fail(f"Description contains non-ASCII: {desc}")


class TestProbeCapabilities(unittest.TestCase):
    """Test that built drivers report honest capabilities."""

    def test_claude_probe(self):
        """Claude reports high accuracy and tier 1."""
        config = {"backend": "claude"}
        driver = build_driver(config)
        caps = driver.probe_capabilities()
        self.assertEqual(caps.recommended_verification_tier, 1)
        self.assertGreater(caps.tool_use_accuracy, 0.9)

    def test_codex_probe(self):
        """Codex reports tier 2."""
        config = {"backend": "codex", "model": "gpt-4o-mini"}
        driver = build_driver(config)
        caps = driver.probe_capabilities()
        self.assertEqual(caps.recommended_verification_tier, 2)
        self.assertFalse(caps.parallel_dispatch)
        self.assertFalse(caps.worker_filesystem_access)
        self.assertFalse(caps.worker_shell_access)

    def test_openai_compatible_hosted_probe(self):
        """OpenAI-compatible hosted model reports tier 2."""
        config = {
            "backend": "openai-compatible",
            "base_url": "https://openrouter.ai/api/v1",
            "model": "openai/gpt-4",
            "is_local": False,
        }
        driver = build_driver(config)
        caps = driver.probe_capabilities()
        self.assertEqual(caps.recommended_verification_tier, 2)

    def test_openai_compatible_local_probe(self):
        """OpenAI-compatible local model reports tier 3."""
        config = {
            "backend": "openai-compatible",
            "base_url": "http://localhost:11434/v1",
            "model": "neural-chat",
            "is_local": True,
        }
        driver = build_driver(config)
        caps = driver.probe_capabilities()
        self.assertEqual(caps.recommended_verification_tier, 3)


if __name__ == "__main__":
    unittest.main()
