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

import json
import os
import sys
import tempfile
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


if __name__ == "__main__":
    unittest.main()
