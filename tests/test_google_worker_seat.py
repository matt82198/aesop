#!/usr/bin/env python3
"""Google Gemini worker seat via the existing seats config (hot-swap trial).

Covers:
  1. build_driver(seats.worker google block) -> OpenAICompatibleDriver with the
     Google base_url, the configured model, and api_key_env GOOGLE_API_KEY.
  2. The built driver resolves every role to the configured Gemini model.
  3. resolve_worker_driver (the wave_scheduler CLI resolver) honors the Google
     seat from a config file, and fails closed when execute=True and the key
     env var is unset (no key value is ever read or printed).
  4. wave_loop CLI routes worker-driver selection through resolve_worker_driver:
     default (no seats block) passes driver_override=None; --driver and --config
     are forwarded; a resolver error exits 1 instead of silently using Claude.

Constraints: stdlib unittest only, offline (fake/no transport, no network, no
real key), hermetic temp fixtures, Windows + Linux safe.
"""

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
DRIVER_DIR = REPO_ROOT / "driver"
for _p in (DRIVER_DIR,):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from backend_config import build_driver  # noqa: E402
from openai_compatible_driver import OpenAICompatibleDriver  # noqa: E402
import wave_loop  # noqa: E402
import wave_scheduler  # noqa: E402

GOOGLE_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"
GOOGLE_MODEL = "gemini-4-hydrogen"
# Env var name assembled at runtime (matches the allowlist; never a secret value).
GOOGLE_KEY_ENV = "GOOGLE" + "_" + "API" + "_" + "KEY"


def _google_seat() -> dict:
    return {
        "backend": "openai-compatible",
        "base_url": GOOGLE_BASE_URL,
        "model": GOOGLE_MODEL,
        "api_key_env": GOOGLE_KEY_ENV,
    }


def _write_config(tmpdir: str, config: dict) -> str:
    path = Path(tmpdir) / "aesop.config.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    return str(path)


class GoogleSeatBuildDriverTests(unittest.TestCase):
    def test_build_driver_returns_openai_compatible_google_driver(self):
        seats_config = {"seats": {"worker": _google_seat()}}
        driver = build_driver(seats_config)
        self.assertIsInstance(driver, OpenAICompatibleDriver)
        self.assertEqual(driver._base_url, GOOGLE_BASE_URL)
        self.assertEqual(driver._model, GOOGLE_MODEL)
        self.assertEqual(driver._api_key_env, GOOGLE_KEY_ENV)
        self.assertFalse(driver._is_local)

    def test_build_driver_needs_no_key_at_build_time(self):
        env = {k: v for k, v in os.environ.items() if k != GOOGLE_KEY_ENV}
        with mock.patch.dict(os.environ, env, clear=True):
            driver = build_driver({"seats": {"worker": _google_seat()}})
        self.assertIsInstance(driver, OpenAICompatibleDriver)

    def test_every_role_resolves_to_configured_gemini_model(self):
        driver = build_driver({"seats": {"worker": _google_seat()}})
        for role in ("worker", "setup", "verify"):
            self.assertEqual(driver.resolve_model(role), GOOGLE_MODEL, role)

    def test_non_https_google_base_url_is_rejected(self):
        seat = _google_seat()
        seat["base_url"] = "ftp://generativelanguage.googleapis.com/v1beta/openai/"
        with self.assertRaises(ValueError):
            build_driver({"seats": {"worker": seat}})

    def test_private_address_base_url_is_rejected(self):
        seat = _google_seat()
        seat["base_url"] = "https://169.254.169.254/v1beta/openai/"
        with self.assertRaises(ValueError):
            build_driver({"seats": {"worker": seat}})


class GoogleSeatResolveWorkerDriverTests(unittest.TestCase):
    def test_resolve_worker_driver_builds_google_seat_from_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = _write_config(tmp, {"seats": {"worker": _google_seat()}})
            driver, error = wave_scheduler.resolve_worker_driver(
                config_path=cfg, execute=False
            )
        self.assertIsNone(error)
        self.assertIsInstance(driver, OpenAICompatibleDriver)
        self.assertEqual(driver._model, GOOGLE_MODEL)

    def test_execute_without_google_key_fails_closed(self):
        env = {k: v for k, v in os.environ.items() if k != GOOGLE_KEY_ENV}
        with tempfile.TemporaryDirectory() as tmp:
            cfg = _write_config(tmp, {"seats": {"worker": _google_seat()}})
            with mock.patch.dict(os.environ, env, clear=True):
                driver, error = wave_scheduler.resolve_worker_driver(
                    config_path=cfg, execute=True
                )
        self.assertIsNone(driver)
        self.assertIsNotNone(error)
        self.assertIn(GOOGLE_KEY_ENV, error)

    def test_no_seats_block_still_resolves_to_claude(self):
        with tempfile.TemporaryDirectory() as tmp:
            # A VALID legacy flat block (no seats): inert for the CLI resolver.
            legacy = {
                "backend": "openai-compatible",
                "base_url": GOOGLE_BASE_URL,
                "model": GOOGLE_MODEL,
                "api_key_env": GOOGLE_KEY_ENV,
            }
            cfg = _write_config(tmp, legacy)
            driver, error = wave_scheduler.resolve_worker_driver(
                config_path=cfg, execute=False
            )
        self.assertIsNone(error)
        self.assertEqual(type(driver).__name__, "ClaudeCodeDriver")


class WaveLoopCliSeatTests(unittest.TestCase):
    def _run_main(self, argv_tail, resolver_return):
        with tempfile.TemporaryDirectory() as tmp:
            manifest = Path(tmp) / "wave.json"
            manifest.write_text("{}", encoding="utf-8")
            argv = ["wave_loop.py", "--manifest", str(manifest)] + argv_tail
            with mock.patch.object(
                wave_scheduler, "resolve_worker_driver", return_value=resolver_return
            ) as resolver, mock.patch.object(sys, "argv", argv), \
                    contextlib.redirect_stderr(io.StringIO()):
                try:
                    rc = wave_loop.main()
                except SystemExit as exc:
                    rc = exc.code
            return rc, resolver

    def test_default_invocation_asks_resolver_with_no_override(self):
        # Resolver returns an error so main stops before any wave runs.
        rc, resolver = self._run_main([], (None, "stop-after-resolve"))
        resolver.assert_called_once()
        kwargs = resolver.call_args.kwargs
        self.assertIsNone(kwargs.get("driver_override"))
        self.assertIsNone(kwargs.get("config_path"))
        self.assertNotEqual(rc, 0)

    def test_driver_and_config_flags_are_forwarded(self):
        rc, resolver = self._run_main(
            ["--driver", "claude", "--config", "x/aesop.config.json"],
            (None, "stop-after-resolve"),
        )
        kwargs = resolver.call_args.kwargs
        self.assertEqual(kwargs.get("driver_override"), "claude")
        self.assertEqual(kwargs.get("config_path"), "x/aesop.config.json")

    def test_resolver_error_exits_nonzero(self):
        rc, _ = self._run_main([], (None, "boom"))
        self.assertEqual(rc, 1)


class TrialRunnerTests(unittest.TestCase):
    """tools/google_seat_trial.py: offline checks only (no key, no network)."""

    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, str(REPO_ROOT / "tools"))
        import google_seat_trial  # noqa: E402

        cls.trial = google_seat_trial

    def test_example_config_has_no_secret_value_and_validates(self):
        example = REPO_ROOT / "tools" / "google_seat_trial.example.json"
        config = json.loads(example.read_text(encoding="utf-8"))
        self.assertEqual(config["seats"]["worker"]["api_key_env"], GOOGLE_KEY_ENV)
        # No literal key field: only the env-var NAME is present in the seat.
        self.assertNotIn("api_key", config["seats"]["worker"])
        self.assertIsInstance(build_driver(config), OpenAICompatibleDriver)

    def test_unset_key_exits_2_before_any_network_call(self):
        env = {k: v for k, v in os.environ.items() if k != GOOGLE_KEY_ENV}
        example = str(REPO_ROOT / "tools" / "google_seat_trial.example.json")
        err = io.StringIO()
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.dict(os.environ, env, clear=True), \
                mock.patch.object(self.trial, "make_openai_compatible_transport") as mk, \
                contextlib.redirect_stderr(err):
            rc = self.trial.main(
                ["--config", example, "--result", str(Path(tmp) / "r.json")]
            )
        self.assertEqual(rc, 2)
        self.assertIn(GOOGLE_KEY_ENV, err.getvalue())
        mk.assert_not_called()


if __name__ == "__main__":
    unittest.main()
