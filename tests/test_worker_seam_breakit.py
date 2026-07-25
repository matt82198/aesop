#!/usr/bin/env python3
"""Round-2 adversarial worker-seat regressions (BREAK-IT lens on AgentDriver).

Each test reproduces a contract divergence found by attacking the worker seam
(the AgentDriver ABC and the concrete non-Claude backends). Findings fixed:

  1. backend_config: codex config 'model' field was schema-required but
     silently IGNORED by build_driver -> dispatches ran the default model.
  2. CodexDriver: configured timeout_s never reached the default HTTP
     transport (default_openai_transport has its own timeout_s=120 default).
  3. Retry loop: transport (network/auth) errors were retried WITH a
     "your JSON was invalid" nudge and misreported as
     "structured output validation failed".
  4. Ownership check: exact-string match rejected a model returning
     "src/util.py" for a Windows-authored owned entry "src\\util.py",
     although the read side explicitly treats backslashes as separators.
  5. Partial writes: a mid-loop write failure returned a FAILED result with
     EMPTY files_written, hiding that earlier files were already clobbered.

All offline: fake transports only, no network, no keys, temp dirs only.
"""

import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DRIVER_DIR = REPO / "driver"
if str(DRIVER_DIR) not in sys.path:
    sys.path.insert(0, str(DRIVER_DIR))

import codex_driver  # noqa: E402
from agent_driver import ROLE_WORKER, WorkerRequest  # noqa: E402
from backend_config import build_driver, describe_backend  # noqa: E402
from codex_driver import CodexDriver  # noqa: E402


def make_response(patch, total_tokens=10):
    return {
        "choices": [{"message": {"content": json.dumps(patch)}}],
        "usage": {"total_tokens": total_tokens},
    }


class TestCodexConfigModelHonored(unittest.TestCase):
    """Finding 1: config 'model' must actually drive the worker model."""

    def test_config_model_maps_to_worker_role(self):
        driver = build_driver({"backend": "codex", "model": "gpt-4o"})
        self.assertEqual(driver.resolve_model(ROLE_WORKER), "gpt-4o")

    def test_explicit_model_map_still_wins(self):
        driver = build_driver(
            {
                "backend": "codex",
                "model": "gpt-4o",
                "model_map": {"worker": "gpt-4-turbo"},
            }
        )
        self.assertEqual(driver.resolve_model(ROLE_WORKER), "gpt-4-turbo")

    def test_incapable_config_model_rejected_loudly(self):
        # gpt-3.5-turbo lacks json_schema support; wiring the model means the
        # driver's P1 gate now fires at build time instead of the model being
        # silently swapped for the default.
        with self.assertRaises(ValueError):
            build_driver({"backend": "codex", "model": "gpt-3.5-turbo"})

    def test_allow_unverified_models_passthrough(self):
        driver = build_driver(
            {
                "backend": "codex",
                "model": "gpt-3.5-turbo",
                "allow_unverified_models": True,
            }
        )
        self.assertEqual(driver.resolve_model(ROLE_WORKER), "gpt-3.5-turbo")

    def test_describe_backend_does_not_crash_on_rejected_model(self):
        desc = describe_backend({"backend": "codex", "model": "gpt-3.5-turbo"})
        self.assertIn("invalid model config", desc)


class TestTimeoutReachesDefaultTransport(unittest.TestCase):
    """Finding 2: configured timeout_s must be bound into the default transport."""

    def test_default_transport_receives_configured_timeout(self):
        seen = {}

        def recording_transport(payload, timeout_s=120.0, base_url=""):
            seen["timeout_s"] = timeout_s
            return make_response({"files": [], "summary": "s", "done": True})

        original = codex_driver.default_openai_transport
        codex_driver.default_openai_transport = recording_transport
        try:
            driver = CodexDriver(transport=None, timeout_s=7.5)
            with tempfile.TemporaryDirectory() as tmpdir:
                result = driver.dispatch_worker(
                    WorkerRequest(prompt="noop", owned_files=(), workdir=tmpdir)
                )
            self.assertTrue(result.ok)
            self.assertEqual(seen.get("timeout_s"), 7.5)
        finally:
            codex_driver.default_openai_transport = original


class TestTransportErrorNotMislabeled(unittest.TestCase):
    """Finding 3: network/auth failures are transport errors, not JSON errors."""

    def test_hard_transport_error_reported_as_transport_failure(self):
        calls = []

        def failing_transport(payload):
            calls.append(payload)
            raise RuntimeError("HTTP Error 401: Unauthorized")

        driver = CodexDriver(transport=failing_transport, max_retries=2)
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "a.py").write_text("x = 1\n", encoding="utf-8")
            result = driver.dispatch_worker(
                WorkerRequest(prompt="fix", owned_files=("a.py",), workdir=tmpdir)
            )

        self.assertFalse(result.ok)
        self.assertIn("transport failed", result.error)
        self.assertNotIn("validation failed", result.error)
        # No schema nudge appended for transport errors: every retry sends the
        # ORIGINAL two messages, not a growing "return ONLY the JSON" thread.
        for payload in calls:
            self.assertEqual(len(payload["messages"]), 2)

    def test_transient_transport_error_then_success(self):
        state = {"calls": 0}
        patch = {
            "files": [{"path": "a.py", "contents": "x = 2\n"}],
            "summary": "s",
            "done": True,
        }

        def flaky_transport(payload):
            state["calls"] += 1
            if state["calls"] == 1:
                raise RuntimeError("connection reset")
            return make_response(patch)

        driver = CodexDriver(transport=flaky_transport, max_retries=2)
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "a.py").write_text("x = 1\n", encoding="utf-8")
            result = driver.dispatch_worker(
                WorkerRequest(prompt="fix", owned_files=("a.py",), workdir=tmpdir)
            )
        self.assertTrue(result.ok)
        self.assertEqual(state["calls"], 2)

    def test_malformed_json_error_message_unchanged(self):
        junk = {
            "choices": [{"message": {"content": "not json"}}],
            "usage": {"total_tokens": 0},
        }
        driver = CodexDriver(transport=lambda p: junk, max_retries=1)
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "a.py").write_text("x = 1\n", encoding="utf-8")
            result = driver.dispatch_worker(
                WorkerRequest(prompt="fix", owned_files=("a.py",), workdir=tmpdir)
            )
        self.assertFalse(result.ok)
        self.assertIn("validation failed", result.error)


class TestOwnershipSeparatorNormalization(unittest.TestCase):
    """Finding 4: ownership must use the read side's separator policy."""

    def test_backslash_owned_slash_returned_accepted(self):
        patch = {
            "files": [{"path": "src/c.py", "contents": "y = 3\n"}],
            "summary": "s",
            "done": True,
        }
        driver = CodexDriver(transport=lambda p: make_response(patch))
        with tempfile.TemporaryDirectory() as tmpdir:
            src = Path(tmpdir) / "src"
            src.mkdir()
            (src / "c.py").write_text("y = 2\n", encoding="utf-8")
            result = driver.dispatch_worker(
                WorkerRequest(
                    prompt="fix", owned_files=("src\\c.py",), workdir=tmpdir
                )
            )
            self.assertTrue(result.ok, result.error)
            self.assertEqual(
                (src / "c.py").read_text(encoding="utf-8"), "y = 3\n"
            )
            # files_written reports the canonical owned entry.
            self.assertEqual(result.files_written, ("src\\c.py",))

    def test_genuinely_out_of_scope_still_rejected(self):
        patch = {
            "files": [{"path": "evil.py", "contents": "boom"}],
            "summary": "s",
            "done": True,
        }
        driver = CodexDriver(transport=lambda p: make_response(patch))
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "a.py").write_text("x = 1\n", encoding="utf-8")
            result = driver.dispatch_worker(
                WorkerRequest(prompt="fix", owned_files=("a.py",), workdir=tmpdir)
            )
            self.assertFalse(result.ok)
            self.assertIn("out-of-scope", result.error)
            self.assertFalse((Path(tmpdir) / "evil.py").exists())

    def test_traversal_alias_of_owned_path_not_widened(self):
        # Normalization must not let "src/../a.py" match owned "a.py":
        # comparison is string-level on separators only, no dot-segment
        # resolution, so aliases stay out-of-scope.
        patch = {
            "files": [{"path": "src\\..\\a.py", "contents": "boom"}],
            "summary": "s",
            "done": True,
        }
        driver = CodexDriver(transport=lambda p: make_response(patch))
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "a.py").write_text("x = 1\n", encoding="utf-8")
            result = driver.dispatch_worker(
                WorkerRequest(prompt="fix", owned_files=("a.py",), workdir=tmpdir)
            )
            self.assertFalse(result.ok)
            self.assertIn("out-of-scope", result.error)


class TestPartialWriteVisibility(unittest.TestCase):
    """Finding 5: a failed dispatch must report which files it already wrote."""

    def test_write_failure_reports_prior_writes(self):
        patch = {
            "files": [
                {"path": "one.py", "contents": "changed\n"},
                {"path": "two.py", "contents": "changed\n"},
            ],
            "summary": "s",
            "done": True,
        }
        driver = CodexDriver(transport=lambda p: make_response(patch))
        with tempfile.TemporaryDirectory() as tmpdir:
            p1 = Path(tmpdir) / "one.py"
            p2 = Path(tmpdir) / "two.py"
            p1.write_text("a\n", encoding="utf-8")
            p2.write_text("b\n", encoding="utf-8")
            os.chmod(str(p2), stat.S_IREAD)  # second write fails
            try:
                result = driver.dispatch_worker(
                    WorkerRequest(
                        prompt="fix",
                        owned_files=("one.py", "two.py"),
                        workdir=tmpdir,
                    )
                )
                self.assertFalse(result.ok)
                self.assertIn("write_failed", result.error)
                # one.py WAS modified before the failure; the result must say so.
                self.assertEqual(result.files_written, ("one.py",))
                self.assertEqual(
                    p1.read_text(encoding="utf-8"), "changed\n"
                )
            finally:
                os.chmod(str(p2), stat.S_IWRITE)  # allow temp-dir cleanup


if __name__ == "__main__":
    unittest.main()
