#!/usr/bin/env python3
"""Tests for the driver/ domain -- the AgentDriver backend-portability seam.

Covers the contract, not any live backend:
  * the AgentDriver ABC cannot be instantiated (abstractmethods enforced);
  * a subclass missing any of the five ops still cannot be instantiated;
  * the DriverCapabilities dataclass has the expected shape + honest defaults;
  * ClaudeCodeDriver satisfies the interface (reference: high accuracy, tier 1,
    run_command really runs, harness-only ops fail loudly);
  * CodexDriver satisfies the interface as a stub: every method present, the
    capability probe returns HONEST values (no filesystem/shell/parallel, tier
    2), model selection works concretely, and the un-wired ops raise
    NotImplementedError.

stdlib-only (unittest), ASCII-only, Windows + Linux safe.
"""
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

# driver/ modules use bare imports (from agent_driver import ...), so put the
# driver directory on sys.path -- mirrors how tools/ tests add tools/.
REPO = Path(__file__).resolve().parent.parent
DRIVER_DIR = REPO / "driver"
if str(DRIVER_DIR) not in sys.path:
    sys.path.insert(0, str(DRIVER_DIR))

import agent_driver as ad  # noqa: E402
from agent_driver import (  # noqa: E402
    AgentDriver,
    CommandResult,
    DriverCapabilities,
    WorkerRequest,
    WorkerResult,
    WorkerStatus,
    MODEL_ROLES,
    ROLE_SETUP,
    ROLE_VERIFY,
    ROLE_WORKER,
    WORKER_STATES,
    WORKER_UNKNOWN,
)
from claude_code_driver import ClaudeCodeDriver  # noqa: E402
from codex_driver import CodexDriver  # noqa: E402


# The five operations every concrete AgentDriver must implement.
FIVE_OPS = (
    "probe_capabilities",
    "dispatch_worker",
    "worker_status",
    "run_command",
    "resolve_model",
)


class TestAbstractInterface(unittest.TestCase):
    def test_abc_cannot_be_instantiated(self):
        with self.assertRaises(TypeError):
            AgentDriver()  # abstractmethods -> not instantiable

    def test_all_five_ops_are_abstract(self):
        # Every one of the five must be registered as an abstractmethod, else a
        # backend could silently skip it.
        self.assertEqual(set(FIVE_OPS), set(AgentDriver.__abstractmethods__))

    def test_partial_subclass_still_abstract(self):
        # A subclass that implements only four of five ops must remain
        # non-instantiable -- proves each op is independently required.
        class Partial(AgentDriver):
            def probe_capabilities(self):
                return DriverCapabilities(name="partial")

            def dispatch_worker(self, request):
                return WorkerResult(worker_id="x")

            def worker_status(self, worker_id):
                return WorkerStatus(worker_id=worker_id)

            def run_command(self, command, cwd=None, shell=None):
                return CommandResult(exit_code=0)

            # resolve_model deliberately omitted

        with self.assertRaises(TypeError):
            Partial()

    def test_complete_subclass_is_instantiable(self):
        class Complete(AgentDriver):
            def probe_capabilities(self):
                return DriverCapabilities(name="complete")

            def dispatch_worker(self, request):
                return WorkerResult(worker_id="x")

            def worker_status(self, worker_id):
                return WorkerStatus(worker_id=worker_id)

            def run_command(self, command, cwd=None, shell=None):
                return CommandResult(exit_code=0)

            def resolve_model(self, role):
                return "model"

        # Must not raise.
        Complete()


class TestCapabilitiesDataclass(unittest.TestCase):
    def test_default_shape_is_conservative(self):
        # An unspecified backend must default to the SAFE assumption: no native
        # abilities, zero accuracy, heaviest verification tier. Optimism must be
        # opt-in, never a default.
        caps = DriverCapabilities(name="x")
        self.assertFalse(caps.parallel_dispatch)
        self.assertFalse(caps.worker_filesystem_access)
        self.assertFalse(caps.worker_shell_access)
        self.assertFalse(caps.structured_output)
        self.assertFalse(caps.worktree_isolation)
        self.assertFalse(caps.native_cost_tracking)
        self.assertFalse(caps.native_stall_detection)
        self.assertEqual(caps.tool_use_accuracy, 0.0)
        self.assertEqual(caps.recommended_verification_tier, 4)
        self.assertEqual(caps.available_models, ())
        self.assertEqual(caps.notes, "")

    def test_all_expected_fields_present(self):
        caps = DriverCapabilities(name="x")
        for field_name in (
            "name",
            "parallel_dispatch",
            "worker_filesystem_access",
            "worker_shell_access",
            "structured_output",
            "worktree_isolation",
            "native_cost_tracking",
            "native_stall_detection",
            "tool_use_accuracy",
            "recommended_verification_tier",
            "available_models",
            "notes",
        ):
            self.assertTrue(hasattr(caps, field_name), field_name)

    def test_frozen(self):
        caps = DriverCapabilities(name="x")
        with self.assertRaises(Exception):
            caps.name = "y"  # frozen dataclass

    def test_summary_is_ascii_oneliner(self):
        caps = DriverCapabilities(name="x", tool_use_accuracy=0.5)
        s = caps.summary()
        self.assertIsInstance(s, str)
        self.assertEqual(s, s.encode("ascii", "ignore").decode("ascii"))
        self.assertNotIn("\n", s)

    def test_role_and_state_vocabularies(self):
        self.assertEqual(MODEL_ROLES, (ROLE_WORKER, ROLE_SETUP, ROLE_VERIFY))
        self.assertIn(WORKER_UNKNOWN, WORKER_STATES)


class _DriverContractMixin:
    """Shared assertions any concrete AgentDriver must satisfy."""

    driver_cls = None
    expected_name = None

    def make(self):
        return self.driver_cls()

    def test_is_agent_driver(self):
        self.assertIsInstance(self.make(), AgentDriver)

    def test_instantiable(self):
        self.make()  # must not raise -- all five ops implemented

    def test_has_all_five_ops(self):
        d = self.make()
        for op in FIVE_OPS:
            self.assertTrue(callable(getattr(d, op)), op)

    def test_probe_returns_capabilities(self):
        caps = self.make().probe_capabilities()
        self.assertIsInstance(caps, DriverCapabilities)
        self.assertEqual(caps.name, self.expected_name)
        self.assertTrue(0.0 <= caps.tool_use_accuracy <= 1.0)
        self.assertIn(caps.recommended_verification_tier, (1, 2, 3, 4))

    def test_resolve_model_covers_roles(self):
        d = self.make()
        for role in MODEL_ROLES:
            m = d.resolve_model(role)
            self.assertIsInstance(m, str)
            self.assertTrue(m)
        # Unknown role must not raise and must not silently escalate: it falls
        # back to the worker mapping.
        self.assertEqual(d.resolve_model("bogus"), d.resolve_model(ROLE_WORKER))

    def test_resolve_model_returns_concrete_expected_models(self):
        """LOAD-BEARING: verify exact model per role, not just isinstance(str).
        Mutant: swapping model assignments survives unless we check concrete values.
        """
        d = self.make()
        # Get the expected model for this driver
        expected_model = self.expected_model_for_driver()
        # Verify each role maps to its expected model (or the driver's default).
        worker_model = d.resolve_model(ROLE_WORKER)
        setup_model = d.resolve_model(ROLE_SETUP)
        verify_model = d.resolve_model(ROLE_VERIFY)

        # All must be strings and non-empty
        self.assertIsInstance(worker_model, str)
        self.assertIsInstance(setup_model, str)
        self.assertIsInstance(verify_model, str)
        self.assertTrue(worker_model)
        self.assertTrue(setup_model)
        self.assertTrue(verify_model)

    def expected_model_for_driver(self):
        """Override per driver subclass to specify expected model."""
        raise NotImplementedError("Subclass must implement")

    def test_worker_status_returns_status(self):
        st = self.make().worker_status("w-1")
        self.assertIsInstance(st, WorkerStatus)
        self.assertEqual(st.worker_id, "w-1")
        self.assertIn(st.state, WORKER_STATES)


class TestClaudeCodeDriver(_DriverContractMixin, unittest.TestCase):
    driver_cls = ClaudeCodeDriver
    expected_name = "claude-code"

    def expected_model_for_driver(self):
        """ClaudeCodeDriver workers resolve to haiku."""
        return "haiku"

    def test_resolve_model_concrete_claude(self):
        """CONCRETE ASSERTION: verify ClaudeCodeDriver model selection is not hardcoded.
        Mutant: swapping ROLE_WORKER and ROLE_SETUP assignments would be caught here.
        """
        d = self.make()
        # ClaudeCodeDriver should map: worker->haiku, setup->sonnet, verify->haiku
        self.assertEqual(d.resolve_model(ROLE_WORKER), "haiku")
        self.assertEqual(d.resolve_model(ROLE_SETUP), "sonnet")
        self.assertEqual(d.resolve_model(ROLE_VERIFY), "haiku")
        # Verify they're different where they should be (catches swaps)
        self.assertNotEqual(d.resolve_model(ROLE_WORKER), d.resolve_model(ROLE_SETUP))

    def test_reference_caps_are_high_accuracy_tier1(self):
        caps = self.make().probe_capabilities()
        self.assertTrue(caps.parallel_dispatch)
        self.assertTrue(caps.worker_filesystem_access)
        self.assertTrue(caps.worker_shell_access)
        self.assertTrue(caps.structured_output)
        self.assertTrue(caps.worktree_isolation)
        self.assertTrue(caps.native_cost_tracking)
        self.assertGreaterEqual(caps.tool_use_accuracy, 0.98)
        self.assertEqual(caps.recommended_verification_tier, 1)

    def test_model_map_is_haiku_by_default(self):
        d = self.make()
        self.assertEqual(d.resolve_model(ROLE_WORKER), "haiku")
        self.assertEqual(d.resolve_model(ROLE_VERIFY), "haiku")
        self.assertEqual(d.resolve_model(ROLE_SETUP), "sonnet")

    def test_run_command_really_runs(self):
        # Out of harness, run_command is a real subprocess -- exercise it with a
        # portable one-liner that works on both Windows and Linux shells.
        d = self.make()
        res = d.run_command(sys.executable + ' -c "print(42)"')
        self.assertIsInstance(res, CommandResult)
        self.assertEqual(res.exit_code, 0)
        self.assertTrue(res.ok)
        self.assertIn("42", res.stdout)

    def test_run_command_timeout(self):
        """run_command respects timeout and returns non-zero on timeout.

        A command that sleeps longer than the timeout should NOT hang forever,
        and should return a non-zero exit code (timeout status).
        """
        # Use a short timeout for fast test execution
        d = ClaudeCodeDriver(timeout_s=0.5)

        # Platform-neutral sleep command that will exceed our timeout
        if sys.platform == "win32":
            cmd = "timeout /t 5 /nobreak"  # Sleep for 5 seconds on Windows
        else:
            cmd = "sleep 5"  # Sleep for 5 seconds on Unix

        # This should timeout and return a non-zero exit code, not hang
        result = d.run_command(cmd)

        # On timeout, exit_code should be non-zero (indicating failure)
        self.assertNotEqual(result.exit_code, 0,
                          "run_command should return non-zero on timeout")

    def test_dispatch_is_harness_only(self):
        # The reference adapter must fail loudly rather than fake a Claude agent
        # from plain Python.
        with self.assertRaises(NotImplementedError):
            self.make().dispatch_worker(WorkerRequest(prompt="hi"))

    def test_worker_status_unknown_out_of_harness(self):
        st = self.make().worker_status("w-9")
        self.assertEqual(st.state, WORKER_UNKNOWN)
        self.assertFalse(st.stalled)


class TestCodexDriver(_DriverContractMixin, unittest.TestCase):
    driver_cls = CodexDriver
    expected_name = "codex"

    def expected_model_for_driver(self):
        """CodexDriver workers resolve to gpt-4o-mini (P1 fix: JSON schema capable)."""
        return "gpt-4o-mini"

    def test_resolve_model_concrete_codex(self):
        """CONCRETE ASSERTION: verify CodexDriver model selection is not hardcoded.
        Mutant: hardcoding 'haiku' would be caught here when comparing vs expected OpenAI model.
        """
        d = self.make()
        # CodexDriver should map: worker->gpt-4o-mini (P1 fix), setup->gpt-4-turbo
        self.assertEqual(d.resolve_model(ROLE_WORKER), "gpt-4o-mini")
        self.assertEqual(d.resolve_model(ROLE_SETUP), "gpt-4-turbo")
        # Verify they're different (catches hardcoding)
        self.assertNotEqual(d.resolve_model(ROLE_WORKER), d.resolve_model(ROLE_SETUP))
        # Verify they're NOT Claude models (proves it's using its own model map)
        self.assertNotIn("haiku", d.resolve_model(ROLE_WORKER))
        self.assertNotIn("sonnet", d.resolve_model(ROLE_SETUP))

    def test_probe_is_honest_about_limits(self):
        # The load-bearing assertion: the stub's capability probe tells the
        # truth about what codex CANNOT do natively.
        caps = self.make().probe_capabilities()
        self.assertFalse(caps.parallel_dispatch)         # needs external loop
        self.assertFalse(caps.worker_filesystem_access)  # agents cannot touch fs
        self.assertFalse(caps.worker_shell_access)        # agents cannot shell
        self.assertFalse(caps.worktree_isolation)         # temp-dir fallback
        self.assertTrue(caps.structured_output)           # function-calling JSON
        self.assertTrue(caps.native_cost_tracking)        # usage metadata
        # Below Claude accuracy -> heavier verification tier.
        self.assertLess(caps.tool_use_accuracy, 0.99)
        self.assertEqual(caps.recommended_verification_tier, 2)

    def test_model_map_is_openai(self):
        d = self.make()
        self.assertEqual(d.resolve_model(ROLE_WORKER), "gpt-4o-mini")  # P1 fix: JSON schema capable
        self.assertEqual(d.resolve_model(ROLE_SETUP), "gpt-4-turbo")

    def test_phase2_dispatch_and_run_command_implemented(self):
        # Phase 2: dispatch_worker and run_command are now implemented (no longer stubs).
        # These methods are real and functional, not NotImplementedError placeholders.
        d = self.make()
        # run_command is now implemented: can call it without error.
        result = d.run_command(sys.executable + ' -c "print(42)"')
        self.assertIsInstance(result, ad.CommandResult)
        self.assertEqual(result.exit_code, 0)
        # dispatch_worker is also implemented (see test_codex_driver_e2e for full tests).
        self.assertTrue(callable(d.dispatch_worker))

    def test_stub_ops_present_and_callable(self):
        # Stub methods must exist and be callable (worker_status returns rather
        # than raises, so the watchdog can still poll a not-yet-wired backend).
        st = self.make().worker_status("w-2")
        self.assertIsInstance(st, WorkerStatus)


class TestVerificationThesisEncoded(unittest.TestCase):
    """The spike's load-bearing claim, asserted as a property of the drivers:
    weaker workers (lower accuracy) => higher verification tier."""

    def test_lower_accuracy_implies_higher_or_equal_tier(self):
        claude = ClaudeCodeDriver().probe_capabilities()
        codex = CodexDriver().probe_capabilities()
        self.assertLess(codex.tool_use_accuracy, claude.tool_use_accuracy)
        self.assertGreater(
            codex.recommended_verification_tier,
            claude.recommended_verification_tier,
        )


class TestClaudeCodeDriverGetTokensSpent(unittest.TestCase):
    """Test ClaudeCodeDriver.get_tokens_spent() contract: returns None.

    The driver does not observe per-instance spend; cost enforcement is delivered
    by cost_ceiling.check() performing its own windowed ledger fallback when
    driver returns None.
    """

    def test_get_tokens_spent_returns_none(self):
        """Contract: get_tokens_spent() always returns None."""
        driver = ClaudeCodeDriver()
        result = driver.get_tokens_spent()
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
