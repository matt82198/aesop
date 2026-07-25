#!/usr/bin/env python3
"""HS-1 unified two-seat config tests (worker + orchestrator from ONE block).

Covers:
  1. seats parse: seats.worker/seats.orchestrator recognized in aesop.config.json
  2. Backward compat: legacy flat backend block still parses identically
  3. build_orchestrator_backend(): harness/claude/absent -> HarnessOrchestratorBackend
     (null seat), openai-compatible -> configured OpenAICompatibleOrchestratorBackend
  4. api_key_env honored (parity with the worker seat; no hardcoded OPENAI_API_KEY)
  5. is_local dummy-key path (local Ollama needs no real key)
  6. validate_base_url enforced at load time, build time, AND direct construction
  7. HARD INVARIANT: with NO seats block, behavior is byte-identical to today --
     Claude Code worker + harness orchestrator, no OpenAI backend constructed,
     no key required (no-op for every existing install)
  8. wave_scheduler resolves the worker driver from config; --driver stays an override
  9. shadow adjudication tools build the orchestrator seat from config; --model overrides

Constraints: stdlib unittest only, ASCII-only, offline (no API key, no network),
Windows + Linux safe, hermetic temp fixtures.
"""

import contextlib
import io
import json
import os
import socket
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
DRIVER_DIR = REPO_ROOT / "driver"
TOOLS_DIR = REPO_ROOT / "tools"
for _p in (DRIVER_DIR, TOOLS_DIR):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from claude_code_driver import ClaudeCodeDriver  # noqa: E402
from backend_config import (  # noqa: E402
    build_driver,
    build_orchestrator_backend,
    load_backend_config,
)
from orchestrator_backend import (  # noqa: E402
    HarnessOrchestratorBackend,
    OpenAICompatibleOrchestratorBackend,
)

# Env var names assembled at runtime (never contiguous literals in fixtures).
_KEY_ENV_DEFAULT = "OPENAI" + "_" + "API" + "_" + "KEY"
_KEY_ENV_CUSTOM = "MY_PROVIDER" + "_" + "KEY"


def _write_config(tmpdir, config_dict):
    """Write a config dict to <tmpdir>/aesop.config.json and return the path."""
    config_path = Path(tmpdir) / "aesop.config.json"
    config_path.write_text(json.dumps(config_dict), encoding="utf-8")
    return str(config_path)


class _FakeDecideTransport:
    """Minimal legacy-signature transport capturing calls; returns a canned verdict."""

    def __init__(self):
        self.calls = []

    def __call__(self, payload, timeout_s=120, base_url="https://api.openai.com/v1"):
        self.calls.append({"payload": payload, "base_url": base_url})
        return {"choices": [{"message": {"content": json.dumps({"verdict": "ok"})}}]}


# ============================================================================
# 1. Seats block parsing
# ============================================================================


class TestSeatsParse(unittest.TestCase):
    """load_backend_config understands the namespaced seats block."""

    def test_seats_worker_flattened_to_worker_view(self):
        """seats.worker fields become the top-level (worker) backend view."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = _write_config(tmpdir, {
                "seats": {
                    "worker": {
                        "backend": "openai-compatible",
                        "base_url": "http://localhost:11434/v1",
                        "model": "mistral",
                        "is_local": True,
                    },
                    "orchestrator": {"backend": "harness"},
                },
            })
            config = load_backend_config(path)
            self.assertEqual(config["backend"], "openai-compatible")
            self.assertEqual(config["base_url"], "http://localhost:11434/v1")
            self.assertEqual(config["model"], "mistral")
            self.assertTrue(config["is_local"])
            # seats preserved for the orchestrator-side builder.
            self.assertIn("seats", config)
            self.assertEqual(config["seats"]["orchestrator"]["backend"], "harness")

    def test_seats_orchestrator_only_defaults_worker_to_claude(self):
        """seats with only an orchestrator seat leaves the worker at claude."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = _write_config(tmpdir, {
                "seats": {
                    "orchestrator": {
                        "backend": "openai-compatible",
                        "model": "gpt-4o-mini",
                    },
                },
            })
            config = load_backend_config(path)
            self.assertEqual(config["backend"], "claude")
            self.assertEqual(
                config["seats"]["orchestrator"]["model"], "gpt-4o-mini"
            )

    def test_seats_worker_takes_precedence_over_legacy_block(self):
        """When both seats.worker and a legacy flat block exist, seats wins."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = _write_config(tmpdir, {
                "backend": "codex",
                "model": "gpt-3.5-turbo",
                "seats": {
                    "worker": {"backend": "claude"},
                },
            })
            config = load_backend_config(path)
            self.assertEqual(config["backend"], "claude")

    def test_legacy_flat_block_still_parses_with_no_seats(self):
        """BACKWARD COMPAT: the existing flat worker block parses unchanged."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = _write_config(tmpdir, {
                "backend": "codex",
                "model": "gpt-4o-mini",
            })
            config = load_backend_config(path)
            self.assertEqual(config["backend"], "codex")
            self.assertEqual(config["model"], "gpt-4o-mini")
            self.assertNotIn("seats", config)

    def test_seats_not_a_dict_rejected(self):
        """seats must be an object."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = _write_config(tmpdir, {"seats": ["worker"]})
            with self.assertRaises((TypeError, ValueError)):
                load_backend_config(path)

    def test_seats_worker_missing_backend_rejected(self):
        """A worker seat without a backend field is a loud error."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = _write_config(tmpdir, {
                "seats": {"worker": {"model": "mistral"}},
            })
            with self.assertRaises((TypeError, ValueError)):
                load_backend_config(path)

    def test_seats_orchestrator_unknown_backend_rejected(self):
        """Unknown orchestrator backend -> ValueError naming valid choices."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = _write_config(tmpdir, {
                "seats": {"orchestrator": {"backend": "gemini"}},
            })
            with self.assertRaises(ValueError) as ctx:
                load_backend_config(path)
            self.assertIn("orchestrator", str(ctx.exception).lower())

    def test_seats_orchestrator_openai_requires_model(self):
        """openai-compatible orchestrator seat without model -> ValueError."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = _write_config(tmpdir, {
                "seats": {"orchestrator": {"backend": "openai-compatible"}},
            })
            with self.assertRaises(ValueError) as ctx:
                load_backend_config(path)
            self.assertIn("model", str(ctx.exception))

    def test_seats_orchestrator_base_url_ssrf_checked_at_load(self):
        """Private-range orchestrator base_url rejected at load time."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = _write_config(tmpdir, {
                "seats": {
                    "orchestrator": {
                        "backend": "openai-compatible",
                        "model": "x",
                        "base_url": "http://169.254.169.254/v1",
                    },
                },
            })
            with self.assertRaises(ValueError):
                load_backend_config(path)

    def test_seats_worker_ssrf_checked_at_load(self):
        """Private-range worker base_url rejected at load time (parity)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = _write_config(tmpdir, {
                "seats": {
                    "worker": {
                        "backend": "openai-compatible",
                        "model": "x",
                        "base_url": "http://192.168.1.5/v1",
                    },
                },
            })
            with self.assertRaises(ValueError):
                load_backend_config(path)


# ============================================================================
# 2. build_driver reads seats.worker
# ============================================================================


class TestBuildDriverFromSeats(unittest.TestCase):
    """build_driver() honors the seats.worker block (and its legacy fallback)."""

    def test_build_driver_from_loaded_seats_config(self):
        """Full path: seats.worker openai-compatible -> OpenAICompatibleDriver."""
        from openai_compatible_driver import OpenAICompatibleDriver
        with tempfile.TemporaryDirectory() as tmpdir:
            path = _write_config(tmpdir, {
                "seats": {
                    "worker": {
                        "backend": "openai-compatible",
                        "base_url": "http://localhost:11434/v1",
                        "model": "mistral",
                        "is_local": True,
                    },
                },
            })
            driver = build_driver(load_backend_config(path))
            self.assertIsInstance(driver, OpenAICompatibleDriver)

    def test_build_driver_accepts_raw_seats_dict(self):
        """A hand-built {'seats': {'worker': ...}} dict works without the loader."""
        from openai_compatible_driver import OpenAICompatibleDriver
        driver = build_driver({
            "seats": {
                "worker": {
                    "backend": "openai-compatible",
                    "base_url": "http://localhost:11434/v1",
                    "model": "mistral",
                },
            },
        })
        self.assertIsInstance(driver, OpenAICompatibleDriver)

    def test_build_driver_seats_without_worker_falls_back(self):
        """seats with no worker seat -> legacy/default (Claude) worker."""
        driver = build_driver({
            "backend": "claude",
            "seats": {"orchestrator": {"backend": "harness"}},
        })
        self.assertIsInstance(driver, ClaudeCodeDriver)


# ============================================================================
# 3. build_orchestrator_backend
# ============================================================================


class TestBuildOrchestratorBackend(unittest.TestCase):
    """The orchestrator-seat mirror of build_driver()."""

    def test_none_config_returns_harness_null(self):
        backend = build_orchestrator_backend(None)
        self.assertIsInstance(backend, HarnessOrchestratorBackend)

    def test_no_seats_returns_harness_null(self):
        backend = build_orchestrator_backend({"backend": "claude"})
        self.assertIsInstance(backend, HarnessOrchestratorBackend)

    def test_harness_seat_returns_harness_null(self):
        backend = build_orchestrator_backend({
            "seats": {"orchestrator": {"backend": "harness"}},
        })
        self.assertIsInstance(backend, HarnessOrchestratorBackend)

    def test_claude_seat_returns_harness_null(self):
        """'claude' orchestrator seat == the live harness (null backend)."""
        backend = build_orchestrator_backend({
            "seats": {"orchestrator": {"backend": "claude"}},
        })
        self.assertIsInstance(backend, HarnessOrchestratorBackend)

    def test_openai_compatible_seat_builds_configured_backend(self):
        """openai-compatible seat -> configured backend, offline-safe (no key)."""
        env = {k: v for k, v in os.environ.items() if k != _KEY_ENV_DEFAULT}
        with mock.patch.dict(os.environ, env, clear=True):
            backend = build_orchestrator_backend({
                "seats": {
                    "orchestrator": {
                        "backend": "openai-compatible",
                        "model": "gpt-4o-mini",
                        "base_url": "http://localhost:11434/v1",
                        "api_key_env": _KEY_ENV_CUSTOM,
                        "is_local": True,
                        "timeout_s": 30,
                    },
                },
            })
        self.assertIsInstance(backend, OpenAICompatibleOrchestratorBackend)
        self.assertEqual(backend.model, "gpt-4o-mini")
        self.assertEqual(backend.base_url, "http://localhost:11434/v1")
        self.assertEqual(backend.api_key_env, _KEY_ENV_CUSTOM)
        self.assertTrue(backend.is_local)
        self.assertEqual(backend.timeout_s, 30.0)

    def test_openai_compatible_seat_defaults(self):
        """base_url defaults to the hosted OpenAI endpoint; key env defaults."""
        backend = build_orchestrator_backend({
            "seats": {
                "orchestrator": {
                    "backend": "openai-compatible",
                    "model": "gpt-4o-mini",
                },
            },
        })
        self.assertIsInstance(backend, OpenAICompatibleOrchestratorBackend)
        self.assertEqual(backend.base_url, "https://api.openai.com/v1")
        self.assertEqual(backend.api_key_env, _KEY_ENV_DEFAULT)
        self.assertFalse(backend.is_local)

    def test_build_validates_base_url(self):
        """SSRF guard applies when building from a raw (unloaded) dict."""
        with self.assertRaises(ValueError):
            build_orchestrator_backend({
                "seats": {
                    "orchestrator": {
                        "backend": "openai-compatible",
                        "model": "x",
                        "base_url": "http://10.0.0.7/v1",
                    },
                },
            })

    def test_harness_null_decide_call_raises_documented_error(self):
        """The null backend refuses decide_call with a clear seat explanation."""
        backend = HarnessOrchestratorBackend()
        with self.assertRaises(RuntimeError) as ctx:
            backend.decide_call("adjudicate this")
        msg = str(ctx.exception).lower()
        self.assertIn("harness", msg)
        self.assertIn("seats.orchestrator", str(ctx.exception))


# ============================================================================
# 4. Orchestrator backend: api_key_env, is_local, SSRF on direct construction
# ============================================================================


class TestOrchestratorBackendSeatParity(unittest.TestCase):
    """OpenAICompatibleOrchestratorBackend gains worker-seat parity knobs."""

    def test_api_key_env_honored(self):
        """A custom api_key_env is read instead of the hardcoded default."""
        transport = _FakeDecideTransport()
        backend = OpenAICompatibleOrchestratorBackend(
            model="m", transport=transport, api_key_env=_KEY_ENV_CUSTOM
        )
        env = {k: v for k, v in os.environ.items() if k != _KEY_ENV_DEFAULT}
        env[_KEY_ENV_CUSTOM] = "test-" + "key"
        with mock.patch.dict(os.environ, env, clear=True):
            result = backend.decide_call("prompt")
        self.assertIn("ok", result)

    def test_missing_custom_key_names_the_env_var(self):
        """Missing key error names the CONFIGURED env var, not OPENAI_API_KEY."""
        transport = _FakeDecideTransport()
        backend = OpenAICompatibleOrchestratorBackend(
            model="m", transport=transport, api_key_env=_KEY_ENV_CUSTOM
        )
        env = {
            k: v
            for k, v in os.environ.items()
            if k not in (_KEY_ENV_DEFAULT, _KEY_ENV_CUSTOM)
        }
        with mock.patch.dict(os.environ, env, clear=True):
            with self.assertRaises(RuntimeError) as ctx:
                backend.decide_call("prompt")
        self.assertIn(_KEY_ENV_CUSTOM, str(ctx.exception))

    def test_is_local_needs_no_key(self):
        """is_local=True: decide_call proceeds with NO key env set (dummy key)."""
        transport = _FakeDecideTransport()
        backend = OpenAICompatibleOrchestratorBackend(
            model="m",
            base_url="http://localhost:11434/v1",
            transport=transport,
            is_local=True,
        )
        env = {
            k: v
            for k, v in os.environ.items()
            if k not in (_KEY_ENV_DEFAULT, _KEY_ENV_CUSTOM)
        }
        with mock.patch.dict(os.environ, env, clear=True):
            result = backend.decide_call("prompt")
        self.assertIn("ok", result)

    def test_default_key_env_behavior_unchanged(self):
        """Default construction still reads OPENAI_API_KEY (regression)."""
        transport = _FakeDecideTransport()
        backend = OpenAICompatibleOrchestratorBackend(model="m", transport=transport)
        self.assertEqual(backend.api_key_env, _KEY_ENV_DEFAULT)
        env = {k: v for k, v in os.environ.items() if k != _KEY_ENV_DEFAULT}
        with mock.patch.dict(os.environ, env, clear=True):
            with self.assertRaises(RuntimeError) as ctx:
                backend.decide_call("prompt")
        self.assertIn(_KEY_ENV_DEFAULT, str(ctx.exception))

    def test_direct_construction_validates_base_url(self):
        """SSRF guard fires in __init__ (no config-layer bypass)."""
        with self.assertRaises(ValueError):
            OpenAICompatibleOrchestratorBackend(
                model="m", base_url="ftp://example.com/v1"
            )
        with self.assertRaises(ValueError):
            OpenAICompatibleOrchestratorBackend(
                model="m", base_url="http://169.254.169.254/v1"
            )


# ============================================================================
# 5. HARD INVARIANT: no seats block == byte-identical to today
# ============================================================================


class TestNoOpDefaultInvariant(unittest.TestCase):
    """With NO seats block, every existing install behaves exactly as today."""

    def test_missing_config_file_full_default_path(self):
        """No config file at all: Claude worker + harness orchestrator, no key."""
        env = {
            k: v
            for k, v in os.environ.items()
            if k not in (_KEY_ENV_DEFAULT, _KEY_ENV_CUSTOM)
        }
        with mock.patch.dict(os.environ, env, clear=True):
            with tempfile.TemporaryDirectory() as tmpdir:
                config = load_backend_config(str(Path(tmpdir) / "none.json"))
                self.assertEqual(config, {"backend": "claude"})
                driver = build_driver(config)
                orch = build_orchestrator_backend(config)
        self.assertIsInstance(driver, ClaudeCodeDriver)
        self.assertIsInstance(orch, HarnessOrchestratorBackend)
        self.assertNotIsInstance(orch, OpenAICompatibleOrchestratorBackend)

    def test_existing_style_config_without_seats_unchanged(self):
        """A current-install config (no seats) loads byte-identically."""
        with tempfile.TemporaryDirectory() as tmpdir:
            legacy = {
                "brain_root": "~/.claude",
                "repos": [],
                "cardinal_rules": {"subagent_model": "haiku"},
            }
            path = _write_config(tmpdir, legacy)
            config = load_backend_config(path)
            # Identical to the pre-seats loader output for this input.
            self.assertEqual(config, {"backend": "claude"})
            self.assertIsInstance(build_driver(config), ClaudeCodeDriver)
            self.assertIsInstance(
                build_orchestrator_backend(config), HarnessOrchestratorBackend
            )

    def test_shipped_example_config_is_noop(self):
        """The shipped aesop.config.example.json selects the no-op defaults."""
        example = REPO_ROOT / "aesop.config.example.json"
        self.assertTrue(example.exists())
        config = load_backend_config(str(example))
        self.assertEqual(config["backend"], "claude")
        self.assertIsInstance(build_driver(config), ClaudeCodeDriver)
        self.assertIsInstance(
            build_orchestrator_backend(config), HarnessOrchestratorBackend
        )


# ============================================================================
# 6. wave_scheduler: config-driven worker driver + --driver override
# ============================================================================


class TestWaveSchedulerDriverResolution(unittest.TestCase):
    """wave_scheduler resolves the worker seat from config; --driver overrides."""

    def _resolve(self, **kwargs):
        import wave_scheduler
        return wave_scheduler.resolve_worker_driver(**kwargs)

    def test_default_no_config_is_claude(self):
        """No override + no config file -> ClaudeCodeDriver (today's default)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            driver, err = self._resolve(
                driver_override=None,
                config_path=str(Path(tmpdir) / "none.json"),
                execute=False,
            )
        self.assertIsNone(err)
        self.assertIsInstance(driver, ClaudeCodeDriver)

    def test_config_selects_openai_compatible(self):
        """seats.worker openai-compatible reachable from the scheduler now."""
        from openai_compatible_driver import OpenAICompatibleDriver
        with tempfile.TemporaryDirectory() as tmpdir:
            path = _write_config(tmpdir, {
                "seats": {
                    "worker": {
                        "backend": "openai-compatible",
                        "base_url": "http://localhost:11434/v1",
                        "model": "mistral",
                        "is_local": True,
                    },
                },
            })
            driver, err = self._resolve(
                driver_override=None, config_path=path, execute=False
            )
        self.assertIsNone(err)
        self.assertIsInstance(driver, OpenAICompatibleDriver)

    def test_driver_flag_overrides_config(self):
        """--driver claude wins over a config that says codex."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = _write_config(tmpdir, {
                "seats": {"worker": {"backend": "codex", "model": "gpt-4o-mini"}},
            })
            driver, err = self._resolve(
                driver_override="claude", config_path=path, execute=False
            )
        self.assertIsNone(err)
        self.assertIsInstance(driver, ClaudeCodeDriver)

    def test_codex_override_still_requires_key_on_execute(self):
        """Existing --driver codex --execute key gate preserved."""
        env = {k: v for k, v in os.environ.items() if k != _KEY_ENV_DEFAULT}
        with mock.patch.dict(os.environ, env, clear=True):
            driver, err = self._resolve(
                driver_override="codex", config_path=None, execute=True
            )
        self.assertIsNone(driver)
        self.assertIn(_KEY_ENV_DEFAULT, err)

    def test_config_local_backend_executes_without_key(self):
        """is_local worker seat needs no API key even with --execute."""
        env = {k: v for k, v in os.environ.items() if k != _KEY_ENV_DEFAULT}
        with mock.patch.dict(os.environ, env, clear=True):
            with tempfile.TemporaryDirectory() as tmpdir:
                path = _write_config(tmpdir, {
                    "seats": {
                        "worker": {
                            "backend": "openai-compatible",
                            "base_url": "http://localhost:11434/v1",
                            "model": "mistral",
                            "is_local": True,
                        },
                    },
                })
                driver, err = self._resolve(
                    driver_override=None, config_path=path, execute=True
                )
        self.assertIsNone(err)
        self.assertIsNotNone(driver)

    def test_config_hosted_backend_requires_named_key_on_execute(self):
        """Hosted seat + --execute demands the seat's api_key_env, by name."""
        custom_env = "OPEN" + "ROUTER" + "_" + "API" + "_" + "KEY"
        env = {
            k: v
            for k, v in os.environ.items()
            if k not in (_KEY_ENV_DEFAULT, custom_env)
        }
        with mock.patch.dict(os.environ, env, clear=True):
            with tempfile.TemporaryDirectory() as tmpdir:
                path = _write_config(tmpdir, {
                    "seats": {
                        "worker": {
                            "backend": "openai-compatible",
                            "base_url": "https://openrouter.ai/api/v1",
                            "model": "openai/gpt-4-turbo",
                            "api_key_env": custom_env,
                        },
                    },
                })
                driver, err = self._resolve(
                    driver_override=None, config_path=path, execute=True
                )
        self.assertIsNone(driver)
        self.assertIn(custom_env, err)

    def test_malformed_config_is_loud(self):
        """Broken config -> clear error, not a silent claude fallback."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "aesop.config.json"
            path.write_text("{not json", encoding="utf-8")
            driver, err = self._resolve(
                driver_override=None, config_path=str(path), execute=False
            )
        self.assertIsNone(driver)
        self.assertIsNotNone(err)


# ============================================================================
# 7. Shadow adjudication tools build the orchestrator seat from config
# ============================================================================


class TestShadowToolsSeatWiring(unittest.TestCase):
    """Both shadow tools resolve their live backend via the seats config."""

    def _seat_config(self, tmpdir):
        return _write_config(tmpdir, {
            "seats": {
                "orchestrator": {
                    "backend": "openai-compatible",
                    "model": "seat-model",
                    "base_url": "http://localhost:11434/v1",
                    "api_key_env": _KEY_ENV_CUSTOM,
                    "is_local": True,
                },
            },
        })

    def _check_tool(self, module_name, fallback_model):
        import importlib
        tool = importlib.import_module(module_name)

        # No config -> legacy hosted default with the tool's fallback model.
        with tempfile.TemporaryDirectory() as tmpdir:
            backend = tool.build_live_backend(
                cli_model=None, config_path=str(Path(tmpdir) / "none.json")
            )
            self.assertIsInstance(backend, OpenAICompatibleOrchestratorBackend)
            self.assertEqual(backend.model, fallback_model)

            # Configured seat -> seat model + seat knobs.
            path = self._seat_config(tmpdir)
            backend = tool.build_live_backend(cli_model=None, config_path=path)
            self.assertEqual(backend.model, "seat-model")
            self.assertEqual(backend.api_key_env, _KEY_ENV_CUSTOM)
            self.assertTrue(backend.is_local)

            # CLI --model overrides the seat model; seat knobs stay.
            backend = tool.build_live_backend(cli_model="cli-model", config_path=path)
            self.assertEqual(backend.model, "cli-model")
            self.assertEqual(backend.api_key_env, _KEY_ENV_CUSTOM)

    def test_shadow_adjudication_seat_wiring(self):
        self._check_tool("shadow_adjudication", "gpt-4o-mini")

    def test_seated_shadow_adjudication_seat_wiring(self):
        self._check_tool("seated_shadow_adjudication", "gpt-5.6-sol")


# ============================================================================
# 8. Round-1 audit fixes (PR #378)
# ============================================================================

# More assembled env-var names (never contiguous literals in fixtures).
_ENV_DENIED_GH = "GITHUB" + "_" + "TOKEN"
_ENV_DENIED_AWS = "AWS" + "_" + "SECRET" + "_" + "ACCESS" + "_" + "KEY"


class TestLegacyFlatBlockInertInScheduler(unittest.TestCase):
    """Fix 1: a legacy flat backend block stays INERT in the scheduler default.

    On main the flat {"backend": "codex", ...} block was documented but DEAD
    (nothing consumed it). HS-1's config-first scheduler default must not
    silently activate it: only the new seats block is the opt-in surface.
    """

    def _resolve(self, **kwargs):
        import wave_scheduler
        return wave_scheduler.resolve_worker_driver(**kwargs)

    def test_legacy_flat_codex_block_default_stays_claude(self):
        """Legacy flat codex block + scheduler default -> ClaudeCodeDriver."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = _write_config(tmpdir, {
                "backend": "codex",
                "model": "gpt-4o-mini",
            })
            driver, err = self._resolve(
                driver_override=None, config_path=path, execute=False
            )
        self.assertIsNone(err)
        self.assertIsInstance(driver, ClaudeCodeDriver)

    def test_legacy_flat_gpt35_example_dry_run_survives(self):
        """RELEASE-CRITICAL repro: main's documented example block used
        gpt-3.5-turbo, which CodexDriver's json_schema guard rejects at init.
        A dry run that worked on main must still resolve to Claude, not die."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = _write_config(tmpdir, {
                "backend": "codex",
                "model": "gpt-3.5" + "-turbo",
            })
            driver, err = self._resolve(
                driver_override=None, config_path=path, execute=False
            )
        self.assertIsNone(err)
        self.assertIsInstance(driver, ClaudeCodeDriver)

    def test_legacy_flat_openai_block_execute_demands_no_key(self):
        """Legacy flat hosted block + --execute: inert -> no key demanded."""
        env = {
            k: v
            for k, v in os.environ.items()
            if k not in (_KEY_ENV_DEFAULT, _KEY_ENV_CUSTOM)
        }
        with mock.patch.dict(os.environ, env, clear=True):
            with tempfile.TemporaryDirectory() as tmpdir:
                path = _write_config(tmpdir, {
                    "backend": "openai-compatible",
                    "base_url": "https://openrouter.ai/api/v1",
                    "model": "openai/gpt-4-turbo",
                    "api_key_env": _KEY_ENV_CUSTOM,
                })
                driver, err = self._resolve(
                    driver_override=None, config_path=path, execute=True
                )
        self.assertIsNone(err)
        self.assertIsInstance(driver, ClaudeCodeDriver)

    def test_seats_worker_block_still_activates_config_first(self):
        """The seats block remains the opt-in surface for config-first."""
        from codex_driver import CodexDriver
        with tempfile.TemporaryDirectory() as tmpdir:
            path = _write_config(tmpdir, {
                "seats": {"worker": {"backend": "codex", "model": "gpt-4o-mini"}},
            })
            driver, err = self._resolve(
                driver_override=None, config_path=path, execute=False
            )
        self.assertIsNone(err)
        self.assertIsInstance(driver, CodexDriver)

    def test_shipped_examples_use_no_gpt35(self):
        """No shipped example config recommends a json_schema-less model."""
        for rel in ("aesop.config.example.json",
                    Path("driver") / "aesop.config.example.json"):
            text = (REPO_ROOT / rel).read_text(encoding="utf-8")
            self.assertNotIn("gpt-3.5" + "-turbo", text, str(rel))


class TestIsLocalRequiresLocalBaseUrl(unittest.TestCase):
    """Fix 2: is_local=True must be pinned to a loopback base_url.

    is_local disables the key requirement; without a locality check it lets a
    config exfiltrate prompt content to any remote host with a dummy Bearer.
    """

    _REMOTE = "https://api.openai.com/v1"
    _LOCAL = "http://localhost:11434/v1"

    def test_worker_seat_is_local_remote_rejected_at_load(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = _write_config(tmpdir, {
                "seats": {
                    "worker": {
                        "backend": "openai-compatible",
                        "base_url": self._REMOTE,
                        "model": "m",
                        "is_local": True,
                    },
                },
            })
            with self.assertRaises(ValueError) as ctx:
                load_backend_config(path)
        self.assertIn("is_local", str(ctx.exception))

    def test_orchestrator_seat_is_local_remote_rejected_at_load(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = _write_config(tmpdir, {
                "seats": {
                    "orchestrator": {
                        "backend": "openai-compatible",
                        "model": "m",
                        "base_url": self._REMOTE,
                        "is_local": True,
                    },
                },
            })
            with self.assertRaises(ValueError):
                load_backend_config(path)

    def test_orchestrator_seat_is_local_default_base_url_rejected(self):
        """is_local with NO base_url defaults to hosted OpenAI -> rejected."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = _write_config(tmpdir, {
                "seats": {
                    "orchestrator": {
                        "backend": "openai-compatible",
                        "model": "m",
                        "is_local": True,
                    },
                },
            })
            with self.assertRaises(ValueError):
                load_backend_config(path)

    def test_legacy_flat_is_local_remote_rejected_at_load(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = _write_config(tmpdir, {
                "backend": "openai-compatible",
                "base_url": self._REMOTE,
                "model": "m",
                "is_local": True,
            })
            with self.assertRaises(ValueError):
                load_backend_config(path)

    def test_orchestrator_backend_construct_is_local_remote_rejected(self):
        with self.assertRaises(ValueError):
            OpenAICompatibleOrchestratorBackend(
                model="m",
                base_url=self._REMOTE,
                transport=_FakeDecideTransport(),
                is_local=True,
            )

    def test_worker_driver_construct_is_local_remote_rejected(self):
        from openai_compatible_driver import OpenAICompatibleDriver
        with self.assertRaises(ValueError):
            OpenAICompatibleDriver(
                base_url=self._REMOTE,
                model="m",
                is_local=True,
                transport=lambda payload: {},
            )

    def test_is_local_localhost_still_works_everywhere(self):
        from openai_compatible_driver import OpenAICompatibleDriver
        with tempfile.TemporaryDirectory() as tmpdir:
            path = _write_config(tmpdir, {
                "seats": {
                    "worker": {
                        "backend": "openai-compatible",
                        "base_url": self._LOCAL,
                        "model": "m",
                        "is_local": True,
                    },
                    "orchestrator": {
                        "backend": "openai-compatible",
                        "model": "m",
                        "base_url": self._LOCAL,
                        "is_local": True,
                    },
                },
            })
            config = load_backend_config(path)
        self.assertTrue(config["is_local"])
        backend = OpenAICompatibleOrchestratorBackend(
            model="m",
            base_url=self._LOCAL,
            transport=_FakeDecideTransport(),
            is_local=True,
        )
        self.assertTrue(backend.is_local)
        driver = OpenAICompatibleDriver(
            base_url=self._LOCAL,
            model="m",
            is_local=True,
            transport=lambda payload: {},
        )
        self.assertTrue(driver._is_local)

    def test_ipv6_loopback_accepted(self):
        from backend_config import validate_is_local_base_url
        validate_is_local_base_url("http://[::1]:11434/v1")
        validate_is_local_base_url("http://127.0.0.1:11434/v1")
        with self.assertRaises(ValueError):
            validate_is_local_base_url("https://api.openai.com/v1")


class TestApiKeyEnvValidation(unittest.TestCase):
    """Fix 3: api_key_env must look like an LLM API key env var name."""

    def _worker_config(self, tmpdir, key_env):
        return _write_config(tmpdir, {
            "seats": {
                "worker": {
                    "backend": "openai-compatible",
                    "base_url": "https://openrouter.ai/api/v1",
                    "model": "m",
                    "api_key_env": key_env,
                },
            },
        })

    def test_worker_seat_github_token_rejected(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._worker_config(tmpdir, _ENV_DENIED_GH)
            with self.assertRaises(ValueError) as ctx:
                load_backend_config(path)
        self.assertIn("api_key_env", str(ctx.exception))

    def test_orchestrator_seat_aws_secret_rejected(self):
        """AWS_SECRET_ACCESS_KEY ends in _KEY but is an obvious non-LLM secret."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = _write_config(tmpdir, {
                "seats": {
                    "orchestrator": {
                        "backend": "openai-compatible",
                        "model": "m",
                        "api_key_env": _ENV_DENIED_AWS,
                    },
                },
            })
            with self.assertRaises(ValueError):
                load_backend_config(path)

    def test_legacy_flat_non_key_env_rejected(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = _write_config(tmpdir, {
                "backend": "openai-compatible",
                "base_url": "https://openrouter.ai/api/v1",
                "model": "m",
                "api_key_env": _ENV_DENIED_GH,
            })
            with self.assertRaises(ValueError):
                load_backend_config(path)

    def test_known_provider_key_accepted_silently(self):
        """A known LLM-provider key name loads with NO notice (allowlist-primary)."""
        stderr = io.StringIO()
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._worker_config(tmpdir, "OPEN" + "ROUTER" + "_API" + "_KEY")
            with contextlib.redirect_stderr(stderr):
                config = load_backend_config(path)
        self.assertEqual(config["backend"], "openai-compatible")
        self.assertNotIn("NOTICE", stderr.getvalue())

    def test_custom_gateway_key_accepted_with_notice(self):
        """An unknown-but-key-shaped name loads, with a loud NOTICE on stderr."""
        stderr = io.StringIO()
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._worker_config(
                tmpdir, "MY_LLM" + "_GATEWAY" + "_API" + "_KEY"
            )
            with contextlib.redirect_stderr(stderr):
                config = load_backend_config(path)
        self.assertEqual(config["backend"], "openai-compatible")
        self.assertIn("NOTICE", stderr.getvalue())
        self.assertIn("api_key_env", stderr.getvalue())

    def test_default_key_env_no_notice(self):
        """The default key env name loads silently (no NOTICE spam)."""
        stderr = io.StringIO()
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._worker_config(tmpdir, _KEY_ENV_DEFAULT)
            with contextlib.redirect_stderr(stderr):
                load_backend_config(path)
        self.assertNotIn("NOTICE", stderr.getvalue())


class TestBuildDriverSeatPromotionParity(unittest.TestCase):
    """Fix 4: build_driver raw-dict seats promotion == loader promotion."""

    def test_raw_dict_and_loader_agree(self):
        from openai_compatible_driver import OpenAICompatibleDriver
        seat = {
            "backend": "openai-compatible",
            "base_url": "http://localhost:11434/v1",
            "model": "mistral",
            "is_local": True,
        }
        raw_driver = build_driver({"seats": {"worker": dict(seat)}})
        with tempfile.TemporaryDirectory() as tmpdir:
            path = _write_config(tmpdir, {"seats": {"worker": dict(seat)}})
            loaded_driver = build_driver(load_backend_config(path))
        for d in (raw_driver, loaded_driver):
            self.assertIsInstance(d, OpenAICompatibleDriver)
        self.assertEqual(raw_driver._base_url, loaded_driver._base_url)
        self.assertEqual(raw_driver._model, loaded_driver._model)
        self.assertEqual(raw_driver._is_local, loaded_driver._is_local)
        self.assertEqual(raw_driver._api_key_env, loaded_driver._api_key_env)

    def test_top_level_base_url_does_not_leak_into_seat(self):
        """Seat promotion REPLACES; a stray top-level base_url must not fill
        in a missing seat field (merge-vs-replace divergence)."""
        with self.assertRaises(ValueError) as ctx:
            build_driver({
                "base_url": "http://localhost:9999/v1",
                "seats": {
                    "worker": {"backend": "openai-compatible", "model": "m"},
                },
            })
        self.assertIn("base_url", str(ctx.exception))

    def test_invalid_raw_seat_fails_loud(self):
        """An SSRF-unsafe raw seat fails validation, not silently builds."""
        with self.assertRaises(ValueError):
            build_driver({
                "seats": {
                    "worker": {
                        "backend": "openai-compatible",
                        "model": "m",
                        "base_url": "http://169.254.169.254/v1",
                    },
                },
            })


class TestValidateBaseUrlDnsResolution(unittest.TestCase):
    """Fix 5: validate_base_url resolves hostnames, not just IP literals."""

    @staticmethod
    def _addrinfo(addr, family=socket.AF_INET):
        if family == socket.AF_INET6:
            sockaddr = (addr, 0, 0, 0)
        else:
            sockaddr = (addr, 0)
        return [(family, socket.SOCK_STREAM, 6, "", sockaddr)]

    def test_hostname_resolving_private_rejected(self):
        from backend_config import validate_base_url
        with mock.patch("socket.getaddrinfo",
                        return_value=self._addrinfo("10.0.0.5")):
            with self.assertRaises(ValueError) as ctx:
                validate_base_url("https://internal.example.com/v1")
        self.assertIn("resolves", str(ctx.exception))

    def test_hostname_resolving_metadata_v6mapped_rejected(self):
        from backend_config import validate_base_url
        with mock.patch(
            "socket.getaddrinfo",
            return_value=self._addrinfo(
                "::ffff:169.254.169.254", family=socket.AF_INET6
            ),
        ):
            with self.assertRaises(ValueError):
                validate_base_url("https://metadata.example.com/v1")

    def test_hostname_resolving_public_accepted(self):
        from backend_config import validate_base_url
        with mock.patch("socket.getaddrinfo",
                        return_value=self._addrinfo("93.184.216.34")):
            validate_base_url("https://api.example.com/v1")

    def test_unresolvable_hostname_allowed_documented_residual(self):
        """Offline/no-DNS load must not brick hosted configs (residual is
        documented in the docstring; connection-time still fails)."""
        from backend_config import validate_base_url
        with mock.patch("socket.getaddrinfo",
                        side_effect=socket.gaierror("no dns")):
            validate_base_url("https://api.openai.com/v1")


class TestOrchestratorBackendFailClosed(unittest.TestCase):
    """Fix 6: missing SSRF guard import -> construction fails closed."""

    def test_construction_fails_closed_without_ssrf_guard(self):
        import orchestrator_backend
        with mock.patch.object(orchestrator_backend, "validate_base_url", None):
            with self.assertRaises(RuntimeError) as ctx:
                OpenAICompatibleOrchestratorBackend(
                    model="m", transport=_FakeDecideTransport()
                )
        self.assertIn("fail", str(ctx.exception).lower())


class TestHarnessSeatModelWarn(unittest.TestCase):
    """Fix 7: harness/claude orchestrator seat with a model field warns."""

    def test_harness_seat_with_model_warns(self):
        stderr = io.StringIO()
        with tempfile.TemporaryDirectory() as tmpdir:
            path = _write_config(tmpdir, {
                "seats": {
                    "orchestrator": {"backend": "harness", "model": "gpt-4o-mini"},
                },
            })
            with contextlib.redirect_stderr(stderr):
                config = load_backend_config(path)
        backend = build_orchestrator_backend(config)
        self.assertIsInstance(backend, HarnessOrchestratorBackend)
        out = stderr.getvalue()
        self.assertIn("model", out)
        self.assertIn("harness", out)

    def test_harness_seat_without_model_silent(self):
        stderr = io.StringIO()
        with tempfile.TemporaryDirectory() as tmpdir:
            path = _write_config(tmpdir, {
                "seats": {"orchestrator": {"backend": "harness"}},
            })
            with contextlib.redirect_stderr(stderr):
                load_backend_config(path)
        self.assertEqual(stderr.getvalue(), "")


# ============================================================================
# 9. Round-2 audit fixes
# ============================================================================


class TestApiKeyEnvAllowlistPrimary(unittest.TestCase):
    """Round-2 item 2: validate_api_key_env is ALLOWLIST-PRIMARY.

    Known LLM-provider key names pass SILENTLY; the shape regex + denylist
    still hard-reject obvious secrets; any other key-shaped name is allowed
    but emits a LOUD NOTICE naming the risk (custom gateways keep working).
    """

    # Assembled at runtime (never contiguous literals in fixtures).
    _PROVIDERS = tuple(
        p + "_" + "API" + "_" + "KEY"
        for p in (
            "OPENAI", "ANTHROPIC", "OPENROUTER", "TOGETHER", "GROQ",
            "MISTRAL", "DEEPSEEK", "FIREWORKS", "OLLAMA",
            "AZURE_OPENAI", "GOOGLE",
        )
    )
    _RISKY_SHAPED = tuple(
        p + "_" + "KEY"
        for p in ("MASTER", "ENCRYPTION", "HMAC", "JWT", "LICENSE", "DEPLOY")
    ) + ("VAULT" + "_" + "API" + "_" + "KEY", "STRIPE" + "_" + "API" + "_" + "KEY")

    def test_known_providers_silent(self):
        from backend_config import validate_api_key_env
        for name in self._PROVIDERS:
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                validate_api_key_env(name, where="backend")
            self.assertEqual(stderr.getvalue(), "", name)

    def test_unknown_shaped_names_allowed_with_loud_notice(self):
        from backend_config import validate_api_key_env
        for name in self._RISKY_SHAPED:
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                validate_api_key_env(name, where="backend")
            out = stderr.getvalue()
            self.assertIn("NOTICE", out, name)
            self.assertIn(name, out)
            # The notice must name the risk: not a known LLM-provider key.
            self.assertIn("not a known LLM", out, name)

    def test_denylist_still_hard_rejects(self):
        from backend_config import validate_api_key_env
        for name in (_ENV_DENIED_GH, _ENV_DENIED_AWS):
            with self.assertRaises(ValueError):
                validate_api_key_env(name, where="backend")

    def test_default_still_silent(self):
        from backend_config import validate_api_key_env
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            validate_api_key_env(_KEY_ENV_DEFAULT, where="backend")
        self.assertEqual(stderr.getvalue(), "")


class TestBuildDriverFlatPathValidation(unittest.TestCase):
    """Round-2 item 4: build_driver's legacy-flat raw-dict path runs the SAME
    validation as the loader (api_key_env check + clean ValueError on missing
    required fields, not KeyError)."""

    def test_flat_missing_base_url_is_value_error(self):
        with self.assertRaises(ValueError) as ctx:
            build_driver({"backend": "openai-compatible", "model": "m"})
        self.assertIn("base_url", str(ctx.exception))

    def test_flat_missing_model_is_value_error(self):
        with self.assertRaises(ValueError) as ctx:
            build_driver({
                "backend": "openai-compatible",
                "base_url": "http://localhost:11434/v1",
            })
        self.assertIn("model", str(ctx.exception))

    def test_flat_bad_api_key_env_rejected_like_loader(self):
        with self.assertRaises(ValueError) as ctx:
            build_driver({
                "backend": "openai-compatible",
                "base_url": "https://openrouter.ai/api/v1",
                "model": "m",
                "api_key_env": _ENV_DENIED_GH,
            })
        self.assertIn("api_key_env", str(ctx.exception))

    def test_flat_aws_secret_rejected_like_loader(self):
        with self.assertRaises(ValueError):
            build_driver({
                "backend": "openai-compatible",
                "base_url": "https://openrouter.ai/api/v1",
                "model": "m",
                "api_key_env": _ENV_DENIED_AWS,
            })


class TestGetaddrinfoBounded(unittest.TestCase):
    """Round-2 item 5: hostname resolution during validate_base_url is
    time-bounded so a hanging DNS name cannot stall config load."""

    def test_slow_resolver_does_not_hang(self):
        import backend_config

        def slow_getaddrinfo(hostname, port):
            time.sleep(5)
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.5", 0))]

        with mock.patch.object(backend_config, "_GETADDRINFO_TIMEOUT_S", 0.3):
            with mock.patch("socket.getaddrinfo", side_effect=slow_getaddrinfo):
                start = time.monotonic()
                # Timed-out resolution is treated like an unresolvable name:
                # allowed through (offline load must not fail); no hang.
                backend_config.validate_base_url("https://slow.example.com/v1")
                elapsed = time.monotonic() - start
        self.assertLess(elapsed, 3.0, "config load stalled on slow DNS")

    def test_fast_private_resolution_still_rejected(self):
        import backend_config
        addrinfo = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.5", 0))]
        with mock.patch("socket.getaddrinfo", return_value=addrinfo):
            with self.assertRaises(ValueError):
                backend_config.validate_base_url("https://internal.example.com/v1")


class TestShadowToolsLiveHelp(unittest.TestCase):
    """Fix 8: --live help reflects api_key_env/is_local, not a hardcoded key."""

    def test_live_help_no_stale_key_claim(self):
        stale = "requires OPENAI" + "_API" + "_KEY env var"
        for name in ("shadow_adjudication.py", "seated_shadow_adjudication.py"):
            text = (REPO_ROOT / "tools" / name).read_text(encoding="utf-8")
            self.assertNotIn(stale, text, name)


if __name__ == "__main__":
    unittest.main()
