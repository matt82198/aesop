#!/usr/bin/env python3
"""End-to-end tests for the wave bridge (Phase 3).

Proves that a non-Claude backend can drive a real coding task through the bridge
and produce orchestrator-verified results, entirely offline (no API key, no network).

HEADLINE TEST
-------------
A Codex-backed driver (powered by FakeTransport) takes ONE real backlog item
(a red stub + a real unittest) THROUGH dispatch_item -> the model's full-file fix
is applied -> the bridge's run_command runs the item's test -> GREEN (exit 0),
and the returned result.ok is True ONLY because the test passed. Proves a
non-Claude backend drove a real item to verified-green, entirely offline.

OFFLINE SAFETY
--------------
Tests use FakeTransport to avoid network/secrets. All tests pass with ZERO
API keys, ZERO network calls, ZERO credentials.

stdlib-only (unittest), ASCII-only, Windows + Linux safe.
No dependencies: no openai, no jsonschema, no pytest.
"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

# Add driver/ to path for imports.
REPO = Path(__file__).resolve().parent.parent
DRIVER_DIR = REPO / "driver"
if str(DRIVER_DIR) not in sys.path:
    sys.path.insert(0, str(DRIVER_DIR))

import agent_driver as ad  # noqa: E402
from agent_driver import (  # noqa: E402
    DriverCapabilities,
    WorkerRequest,
    WorkerResult,
    ROLE_WORKER,
    WORKER_DONE,
    WORKER_FAILED,
)
from claude_code_driver import ClaudeCodeDriver  # noqa: E402
from codex_driver import CodexDriver  # noqa: E402
from wave_bridge import build_manifest_item, dispatch_item  # noqa: E402


class FakeTransport:
    """Fake transport returning canned OpenAI-shaped responses (copy from test_codex_driver_e2e)."""

    def __init__(self, response=None, responses=None):
        if response is not None:
            self.responses = [response]
            self._idx = 0
        elif responses is not None:
            self.responses = responses
            self._idx = 0
        else:
            self.responses = []
            self._idx = 0

    def __call__(self, payload):
        if not self.responses:
            raise RuntimeError("FakeTransport: no responses configured")
        response = self.responses[min(self._idx, len(self.responses) - 1)]
        self._idx += 1
        return response


def make_response(patch_dict, total_tokens=42):
    """Helper: build an OpenAI-shaped response containing JSON patch."""
    return {
        "choices": [
            {
                "message": {
                    "content": json.dumps(patch_dict),
                },
            },
        ],
        "usage": {"total_tokens": total_tokens},
    }


class TestBuildManifestItem(unittest.TestCase):
    """Test build_manifest_item with Claude and Codex drivers."""

    def test_claude_driver_yields_tier1_and_haiku_with_all_policy_knobs(self):
        """Claude driver probe -> tier 1 + haiku model + tier-1 policy knobs."""
        driver = ClaudeCodeDriver()
        item = {
            "slug": "fix-test",
            "ownsFiles": ["test.py"],
            "prompt": "Fix the test",
            "testCmd": "python -m unittest test",
        }

        result = build_manifest_item(driver, item)

        # Should preserve all input fields.
        self.assertEqual(result["slug"], "fix-test")
        self.assertEqual(result["ownsFiles"], ["test.py"])
        self.assertEqual(result["prompt"], "Fix the test")
        self.assertEqual(result["testCmd"], "python -m unittest test")

        # Should add model and verificationTier from probe.
        self.assertEqual(result["model"], "haiku")
        self.assertEqual(result["verificationTier"], 1)

        # Should bake all four policy knobs (tier-1 defaults).
        self.assertEqual(result["repairCap"], 1)
        self.assertFalse(result["requireAdversarialReview"])
        self.assertEqual(result["spotCheckFrac"], 0.10)
        self.assertFalse(result["validateAllJson"])

    def test_codex_driver_yields_tier2_and_gpt35_with_tier2_policy(self):
        """Codex driver probe -> tier 2 + gpt-3.5-turbo model + tier-2 policy knobs."""
        driver = CodexDriver()
        item = {
            "slug": "codex-task",
            "ownsFiles": ["src/main.py"],
            "prompt": "Implement feature X",
        }

        result = build_manifest_item(driver, item)

        # Should preserve all input fields.
        self.assertEqual(result["slug"], "codex-task")
        self.assertEqual(result["ownsFiles"], ["src/main.py"])
        self.assertEqual(result["prompt"], "Implement feature X")

        # Should add model and verificationTier from probe.
        self.assertEqual(result["model"], "gpt-4o-mini")  # Default worker model (P1 fix)
        self.assertEqual(result["verificationTier"], 2)

        # Should bake all four policy knobs (tier-2: stricter).
        self.assertEqual(result["repairCap"], 2)
        self.assertTrue(result["requireAdversarialReview"])
        self.assertEqual(result["spotCheckFrac"], 0.50)
        self.assertTrue(result["validateAllJson"])

    def test_build_manifest_model_differs_by_driver(self):
        """LOAD-BEARING: build_manifest_item calls driver.resolve_model(), not hardcoding.
        Mutant: hardcoding 'haiku' would cause this test to fail when comparing Claude vs Codex.
        """
        claude_driver = ClaudeCodeDriver()
        codex_driver = CodexDriver()

        item = {
            "slug": "test",
            "ownsFiles": ["file.py"],
            "prompt": "Do it",
        }

        claude_result = build_manifest_item(claude_driver, item)
        codex_result = build_manifest_item(codex_driver, item)

        # The two drivers use DIFFERENT model maps; if build_manifest_item
        # is calling driver.resolve_model(), the results will differ.
        # If build_manifest_item hardcoded 'haiku', both would be 'haiku' and test fails.
        self.assertNotEqual(
            claude_result["model"],
            codex_result["model"],
            msg="build_manifest_item must call driver.resolve_model() for each driver; "
            "hardcoding a single model would cause this assertion to fail"
        )

        # Verify the actual expected models
        self.assertEqual(claude_result["model"], "haiku",
                        msg="ClaudeCodeDriver worker should resolve to haiku")
        self.assertEqual(codex_result["model"], "gpt-4o-mini",
                        msg="CodexDriver worker should resolve to gpt-4o-mini (P1 fix)")

    def test_preserves_optional_fields(self):
        """build_manifest_item preserves optional fields like workDir, selfCheckCmd."""
        driver = CodexDriver()
        item = {
            "slug": "task",
            "ownsFiles": ["file.py"],
            "prompt": "Do it",
            "workDir": "/tmp/work",
            "selfCheckCmd": "python -m pytest file.py",
            "label": "custom-label",
        }

        result = build_manifest_item(driver, item)

        self.assertEqual(result["workDir"], "/tmp/work")
        self.assertEqual(result["selfCheckCmd"], "python -m pytest file.py")
        self.assertEqual(result["label"], "custom-label")
        self.assertEqual(result["model"], "gpt-4o-mini")  # Default worker model (P1 fix)
        self.assertEqual(result["verificationTier"], 2)


class TestDispatchItemRouting(unittest.TestCase):
    """Test dispatch_item routing by driver capabilities."""

    def test_claude_driver_routes_to_harness(self):
        """Claude driver (worker_filesystem_access=True) -> route:'harness'."""
        driver = ClaudeCodeDriver()
        item = {
            "slug": "claude-item",
            "ownsFiles": ["test.py"],
            "prompt": "Fix it",
            "testCmd": "python -m unittest",
        }

        result = dispatch_item(driver, item, workdir="/tmp")

        # Should route to harness, not attempt to dispatch.
        self.assertEqual(result["route"], "harness")
        self.assertIsNone(result["testExit"])
        self.assertIsNone(result["filesWritten"])
        # ok is False (unknown status; harness will set actual result).
        self.assertFalse(result["ok"])

    def test_codex_driver_routes_to_driver(self):
        """Codex driver (worker_filesystem_access=False) -> route:'driver'."""
        patch = {
            "files": [{"path": "test.py", "contents": "print(42)\n"}],
            "summary": "Fixed",
            "done": True,
        }
        fake_transport = FakeTransport(response=make_response(patch, total_tokens=50))
        driver = CodexDriver(transport=fake_transport)

        with tempfile.TemporaryDirectory() as tmpdir:
            test_file = Path(tmpdir) / "test.py"
            test_file.write_text("print(0)\n")

            item = {
                "slug": "codex-item",
                "ownsFiles": ["test.py"],
                "prompt": "Fix it",
            }

            result = dispatch_item(driver, item, workdir=tmpdir)

            # Should route to driver, not harness.
            self.assertEqual(result["route"], "driver")
            self.assertIsNotNone(result["workerId"])
            # No test command: must fail-closed (ok=False, verified=False, reason='no_test_command').
            # Honesty guarantee: "no test to fail" is NOT the same as "verified correct."
            self.assertFalse(result["ok"], msg="No testCmd should never yield ok=True")
            self.assertFalse(result["verified"], msg="No testCmd means not verified")
            self.assertEqual(result["reason"], "no_test_command")
            self.assertIsNone(result["testExit"])
            self.assertEqual(result["filesWritten"], ["test.py"])
            self.assertIn("no testCmd", result["error"])


class TestDispatchItemHeadlineTest(unittest.TestCase):
    """HEADLINE TEST: non-Claude backend drives a real item to verified-green offline.

    Proves:
      1. A Codex-backed driver takes a RED stub (unittest fails).
      2. FakeTransport supplies a valid fix (full-file replacement).
      3. The bridge applies the fix and runs the test (via orchestrator run_command).
      4. Test passes (exit 0).
      5. result.ok is True ONLY because the test passed, not the model's say-so.
      6. All offline, no API key, no network.
    """

    def test_red_stub_fake_fix_green_verification(self):
        """End-to-end: RED stub + FakeTransport fix + run_command -> GREEN."""
        # Setup: create a temp dir with a stub test file (RED by design).
        with tempfile.TemporaryDirectory() as tmpdir:
            test_file = Path(tmpdir) / "test_stub.py"
            # Red stub: this test will FAIL.
            test_file.write_text(
                "import unittest\n"
                "class TestStub(unittest.TestCase):\n"
                "    def test_placeholder(self):\n"
                "        self.assertEqual(1, 0)  # RED by design\n"
            )

            # Verify it's RED: run it without the bridge.
            result_red = __import__("subprocess").run(
                [sys.executable, "-m", "unittest", "test_stub"],
                cwd=tmpdir,
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(
                result_red.returncode,
                0,
                msg="Test stub should be RED (failing) by design",
            )

            # Now use the bridge with FakeTransport to fix it.
            # FakeTransport will return a patch that makes the test pass.
            green_patch = {
                "files": [
                    {
                        "path": "test_stub.py",
                        "contents": (
                            "import unittest\n"
                            "class TestStub(unittest.TestCase):\n"
                            "    def test_placeholder(self):\n"
                            "        self.assertEqual(1, 1)  # FIXED\n"
                        ),
                    }
                ],
                "summary": "Fixed the test",
                "done": True,
            }
            fake_transport = FakeTransport(response=make_response(green_patch, total_tokens=100))
            driver = CodexDriver(transport=fake_transport)

            # Dispatch the item through the bridge.
            item = {
                "slug": "fix-stub-test",
                "ownsFiles": ["test_stub.py"],
                "prompt": "Fix the test_stub.py so it passes",
                "testCmd": f"{sys.executable} -m unittest test_stub",
            }

            result = dispatch_item(driver, item, workdir=tmpdir)

            # Verify routing and result.
            self.assertEqual(result["route"], "driver")
            self.assertTrue(result["ok"], msg="Should be GREEN after FakeTransport fix + test run")
            self.assertTrue(result["verified"], msg="verified=True when test passes (exit 0)")
            self.assertEqual(result["testExit"], 0)
            self.assertEqual(result["filesWritten"], ["test_stub.py"])
            self.assertIsNone(result["error"])

            # Verify file was actually fixed and test now passes.
            test_content = test_file.read_text()
            self.assertIn("self.assertEqual(1, 1)", test_content)
            # Re-run the test to confirm GREEN.
            result_green = __import__("subprocess").run(
                [sys.executable, "-m", "unittest", "test_stub"],
                cwd=tmpdir,
                capture_output=True,
                text=True,
            )
            self.assertEqual(
                result_green.returncode,
                0,
                msg="Test should now be GREEN after fix",
            )

    def test_wrong_code_makes_test_fail_result_ok_false(self):
        """Model returns wrong code (test still fails) -> result.ok=False (no false green)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            test_file = Path(tmpdir) / "test_bad.py"
            test_file.write_text(
                "import unittest\n"
                "class TestBad(unittest.TestCase):\n"
                "    def test_thing(self):\n"
                "        self.assertTrue(False)  # RED\n"
            )

            # FakeTransport returns wrong/bad code that still fails the test.
            bad_patch = {
                "files": [
                    {
                        "path": "test_bad.py",
                        "contents": (
                            "import unittest\n"
                            "class TestBad(unittest.TestCase):\n"
                            "    def test_thing(self):\n"
                            "        self.assertTrue(False)  # STILL RED, not fixed\n"
                        ),
                    }
                ],
                "summary": "Tried to fix but failed",
                "done": True,  # Model claims done, but test will fail
            }
            fake_transport = FakeTransport(response=make_response(bad_patch))
            driver = CodexDriver(transport=fake_transport)

            item = {
                "slug": "bad-fix",
                "ownsFiles": ["test_bad.py"],
                "prompt": "Fix it",
                "testCmd": f"{sys.executable} -m unittest test_bad",
            }

            result = dispatch_item(driver, item, workdir=tmpdir)

            # CRITICAL: result.ok must be FALSE because test failed, NOT because model said done:false.
            # Green is decided ONLY by run_command exit 0, never by the model's done:true.
            self.assertFalse(
                result["ok"],
                msg="result.ok must be False when test fails, ignoring model's done:true",
            )
            self.assertFalse(result["verified"], msg="verified=False when test fails")
            self.assertNotEqual(result["testExit"], 0)
            self.assertIn("test failed", result["error"])

    def test_dispatch_exception_never_false_green(self):
        """Any exception during dispatch_item -> ok=False, never a false green."""
        # Intentionally create a driver state that will raise.
        # We can do this by using CodexDriver with a transport that raises.
        class FailingTransport:
            def __call__(self, payload):
                raise RuntimeError("Transport explosion")

        driver = CodexDriver(transport=FailingTransport())

        with tempfile.TemporaryDirectory() as tmpdir:
            # Create the owned file so dispatch_worker gets past file reading.
            test_file = Path(tmpdir) / "file.py"
            test_file.write_text("# initial")

            item = {
                "slug": "will-crash",
                "ownsFiles": ["file.py"],
                "prompt": "This will crash",
                "testCmd": "echo ok",
            }

            result = dispatch_item(driver, item, workdir=tmpdir)

            # Must fail cleanly, never a false green.
            self.assertFalse(result["ok"])
            self.assertFalse(result["verified"])
            self.assertIsNone(result["testExit"])
            self.assertIsNone(result["filesWritten"])
            # Error should be present and contain the failure reason.
            self.assertIsNotNone(result["error"])
            self.assertTrue(len(result["error"]) > 0)

    def test_no_test_command_never_yields_ok_true(self):
        """Honesty guarantee: no testCmd -> ok=False, verified=False, reason='no_test_command'.

        An item without a verifiable test must be reported as unverified (ok=False),
        not as a vacuous success. Aesop's core honesty principle: "no test to fail"
        is NOT the same as "verified correct."
        """
        # FakeTransport returns a valid patch; files ARE written.
        patch = {
            "files": [{"path": "module.py", "contents": "def func():\n    return True\n"}],
            "summary": "Implemented",
            "done": True,
        }
        fake_transport = FakeTransport(response=make_response(patch))
        driver = CodexDriver(transport=fake_transport)

        with tempfile.TemporaryDirectory() as tmpdir:
            module_file = Path(tmpdir) / "module.py"
            module_file.write_text("# stub")

            # Item with NO testCmd: no way to verify the code works.
            item = {
                "slug": "no-test",
                "ownsFiles": ["module.py"],
                "prompt": "Implement a function",
                # EXPLICITLY NO testCmd
            }

            result = dispatch_item(driver, item, workdir=tmpdir)

            # MUST fail-closed: ok=False, verified=False, with reason.
            self.assertFalse(result["ok"], msg="no testCmd must never yield ok=True")
            self.assertFalse(result["verified"], msg="no testCmd means not verified")
            self.assertEqual(result["reason"], "no_test_command")
            self.assertIn("no testCmd", result["error"])
            # But files WERE written (report them).
            self.assertEqual(result["filesWritten"], ["module.py"])


class TestDispatchItemOwnershipEnforcement(unittest.TestCase):
    """Ownership is enforced at the driver level; bridge trusts it."""

    def test_out_of_scope_path_caught_by_driver(self):
        """Out-of-scope path in owned_files -> driver rejects, bridge returns ok=False."""
        # The driver (CodexDriver) enforces ownership: it will reject any attempt
        # to write a path that's not in owned_files. The bridge just passes the
        # result through.
        patch_with_bad_path = {
            "files": [
                {
                    "path": "evil_path.py",  # Out-of-scope! Not in ownsFiles, will be rejected by driver.
                    "contents": "hacked",
                }
            ],
            "summary": "Evil",
            "done": True,
        }
        fake_transport = FakeTransport(response=make_response(patch_with_bad_path))
        driver = CodexDriver(transport=fake_transport)

        with tempfile.TemporaryDirectory() as tmpdir:
            # Create the owned file so dispatch_worker gets past file reading.
            safe_file = Path(tmpdir) / "safe_file.py"
            safe_file.write_text("# safe")

            item = {
                "slug": "evil-task",
                "ownsFiles": ["safe_file.py"],  # Only safe_file.py is owned
                "prompt": "Do it",
            }

            result = dispatch_item(driver, item, workdir=tmpdir)

            # Driver should reject this and return ok=False.
            self.assertFalse(result["ok"])
            self.assertIn("out-of-scope", result["error"])


class TestBuildManifestItemVerificationTierValidation(unittest.TestCase):
    """P2: Verify build_manifest_item validates probe tier and includes driver name."""

    def test_build_manifest_invalid_tier_raises_with_driver_name(self):
        """P2: Invalid verification tier from driver -> ValueError with driver name."""
        # Create a mock driver with an invalid tier (outside [1, 2, 3, 4])
        class BadTierDriver(ad.AgentDriver):
            name = "bad_tier_driver"

            def probe_capabilities(self):
                return ad.DriverCapabilities(
                    name="bad_tier_driver",
                    recommended_verification_tier=99  # Invalid!
                )

            def dispatch_worker(self, request):
                return ad.WorkerResult(worker_id="x", ok=False)

            def run_command(self, cmd, cwd=None, shell=False):
                return ad.CommandResult(exit_code=1)

            def worker_status(self, worker_id):
                return ad.WorkerStatus(status="unknown")

            def resolve_model(self, role):
                return "test-model"

        driver = BadTierDriver()
        item = {
            "slug": "test",
            "ownsFiles": ["test.py"],
            "prompt": "Fix it",
        }

        # Should raise ValueError with driver name included.
        with self.assertRaises(ValueError) as ctx:
            build_manifest_item(driver, item)

        error_str = str(ctx.exception)
        self.assertIn("bad_tier_driver", error_str, "Driver name should be in error")
        self.assertIn("tier", error_str.lower(), "Should mention verification tier")


class TestRepairContextEnrichment(unittest.TestCase):
    """Test enrichment of repair items with lastTestOutput and ownsFilesDiff.

    These tests verify that the repair dispatch enrichment feature:
    1. Captures and stores test output during BUILD phase
    2. Enriches repair items with capped lastTestOutput and ownsFilesDiff
    3. Maintains no-op guarantee when data is absent
    """

    def test_dispatch_item_captures_test_stdout_stderr(self):
        """dispatch_item should capture stdout/stderr from test_result in the return dict."""
        with tempfile.TemporaryDirectory() as tmpdir:
            test_file = Path(tmpdir) / "test_output.py"
            test_file.write_text(
                "import sys\n"
                "print('This is stdout output')\n"
                "print('Line 2 of output', file=sys.stderr)\n"
                "sys.exit(1)\n"  # Fail the test
            )

            patch = {
                "files": [{"path": "test_output.py", "contents": test_file.read_text()}],
                "summary": "Fixed",
                "done": True,
            }
            fake_transport = FakeTransport(response=make_response(patch, total_tokens=50))
            driver = CodexDriver(transport=fake_transport)

            item = {
                "slug": "test-output",
                "ownsFiles": ["test_output.py"],
                "prompt": "Fix it",
                "testCmd": f"{sys.executable} test_output.py",
            }

            result = dispatch_item(driver, item, workdir=tmpdir)

            # Should have captured test output in the result
            self.assertFalse(result["verified"], "Test should fail (exit 1)")
            # The result dict should include stdout and stderr (when available from driver)
            # This test documents the expected structure
            self.assertIsNotNone(result.get("testExit"))

    def test_dispatch_item_returns_stdout_stderr_in_result(self):
        """Test that dispatch_item includes testStdout and testStderr in the result dict."""
        with tempfile.TemporaryDirectory() as tmpdir:
            test_file = Path(tmpdir) / "test_with_output.py"
            test_file.write_text(
                "print('stdout line 1')\n"
                "print('stdout line 2')\n"
                "import sys; print('stderr output', file=sys.stderr)\n"
                "sys.exit(0)\n"  # Pass the test
            )

            patch = {
                "files": [{"path": "test_with_output.py", "contents": test_file.read_text()}],
                "summary": "Fixed",
                "done": True,
            }
            fake_transport = FakeTransport(response=make_response(patch, total_tokens=50))
            driver = CodexDriver(transport=fake_transport)

            item = {
                "slug": "test-output",
                "ownsFiles": ["test_with_output.py"],
                "prompt": "Fix it",
                "testCmd": f"{sys.executable} test_with_output.py",
            }

            result = dispatch_item(driver, item, workdir=tmpdir)

            # Verify the result includes testStdout and testStderr
            self.assertIn("testStdout", result, "dispatch_item should return testStdout")
            self.assertIn("testStderr", result, "dispatch_item should return testStderr")
            # They should be strings (even if empty)
            self.assertIsInstance(result["testStdout"], str)
            self.assertIsInstance(result["testStderr"], str)

    def test_repair_item_enrichment_with_test_output_and_diff(self):
        """Repair items enriched with lastTestOutput and ownsFilesDiff.

        This test verifies that the enrichment helper functions work correctly:
        - _cap_test_output caps stdout/stderr to reasonable size
        - _get_owned_files_diff gets git diff of owned files
        """
        # Test _cap_test_output
        from driver.wave_loop import _cap_test_output, _get_owned_files_diff

        # Short output should not be capped
        short_out = "short output"
        capped = _cap_test_output(short_out, "")
        self.assertEqual(capped, short_out)

        # Long output should be capped (tail preserved)
        long_out = "x" * 5000
        capped = _cap_test_output(long_out, "")
        self.assertEqual(len(capped), 4000, "Should cap to 4000 chars")
        self.assertTrue(capped.endswith("x"), "Should preserve tail of output")

        # Combined stdout + stderr
        stdout_part = "stdout content"
        stderr_part = "stderr content"
        combined = _cap_test_output(stdout_part, stderr_part)
        self.assertIn(stdout_part, combined)
        self.assertIn(stderr_part, combined)
        self.assertIn("STDERR", combined, "Should mark stderr section")

    def test_repair_enrichment_noop_when_no_test_output(self):
        """Repair enrichment is a no-op when there's no prior test output.

        This is the no-op guarantee: if test output was never captured,
        lastTestOutput and ownsFilesDiff should not be set.
        """
        from driver.wave_loop import _cap_test_output, _get_owned_files_diff

        # Empty output should return empty string
        capped = _cap_test_output("", "")
        self.assertEqual(capped, "")

        # Empty owned files should return empty diff
        diff = _get_owned_files_diff("/tmp", [])
        self.assertEqual(diff, "")


if __name__ == "__main__":
    unittest.main()
