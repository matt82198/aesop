#!/usr/bin/env python3
"""ClaudeCodeDriver -- reference AgentDriver for the Claude Code backend.

This is the PARITY reference: it maps the five AgentDriver operations onto what
Claude Code's Workflow harness already provides. It is intentionally a thin,
well-documented adapter, because in Claude Code the actual dispatch does NOT run
inside this Python process -- it runs inside the harness's Workflow context
(the `agent()`, `parallel()`, Read/Write/Bash tools, `budget.spent()`).

WHAT MAPS WHERE
---------------
  Operation            Claude Code mechanism            Lives where
  -------------------  -------------------------------  ------------------------
  probe_capabilities   static, known-good facts         this file (concrete)
  dispatch_worker      Workflow agent()/Task tool       HARNESS (conceptual)
  worker_status        heartbeat + harness liveness     HARNESS + state files
  run_command          Bash tool                        HARNESS
  resolve_model        Anthropic model-name passthrough this file (concrete)

So two of the five ops (probe_capabilities, resolve_model) are fully concrete
Python here -- they are pure data/mapping and need no harness. The other three
(dispatch_worker, worker_status, run_command) are documented adapters: in a live
Claude Code wave they are serviced by the harness's own tools, not by this
process. This class exists so the SEAM is real and testable -- the wave loop can
be written against AgentDriver and this reference proves the Claude Code backend
satisfies the contract. When the wave-flat-dispatch template is refactored onto
the driver (spike Phase 1), these three methods become the documented handoff
points to the harness.

For out-of-harness use (tests, tooling, local scripts) run_command is given a
real subprocess implementation so it is not a dead stub; dispatch_worker and
worker_status raise a clear, explained error rather than pretending to spawn a
Claude agent from plain Python.

stdlib-only, ASCII-only, Windows + Linux safe.
"""

import subprocess
from typing import Optional

from agent_driver import (
    AgentDriver,
    CommandResult,
    DriverCapabilities,
    ROLE_SETUP,
    ROLE_VERIFY,
    ROLE_WORKER,
    WorkerRequest,
    WorkerResult,
    WorkerStatus,
    WORKER_UNKNOWN,
)


# Default abstract-role -> Anthropic model mapping. Workers are Haiku by policy
# (aesop cardinal rule: subagents are always Haiku); setup/verify may lift to
# Sonnet. resolve_model() reads this and is overridable via the constructor.
_DEFAULT_MODEL_MAP = {
    ROLE_WORKER: "haiku",
    ROLE_SETUP: "sonnet",
    ROLE_VERIFY: "haiku",
}

# Marker error text shared with tests: the ops that only a live harness can do.
_HARNESS_ONLY = (
    "ClaudeCodeDriver.{op} is serviced by the Claude Code Workflow harness "
    "(agent()/Task tool), not by this Python process. In a live wave the "
    "orchestrator dispatches through the harness; this reference adapter marks "
    "the seam. See driver/README.md 'What maps where'."
)


class ClaudeCodeDriver(AgentDriver):
    """Reference adapter mapping AgentDriver onto Claude Code."""

    name = "claude-code"

    def __init__(self, model_map: Optional[dict] = None, timeout_s: float = 120.0):
        # Copy so callers cannot mutate our defaults; fall back per-role.
        self._model_map = dict(_DEFAULT_MODEL_MAP)
        if model_map:
            self._model_map.update(model_map)
        self._timeout_s = timeout_s

    # -- Operation 1: capability probe (concrete) --------------------------
    def probe_capabilities(self) -> DriverCapabilities:
        """Claude Code is the self-contained, high-accuracy reference backend.

        Every capability is native and instant; tool-use accuracy is ~0.99, so
        the recommended verification tier is 1 (light spot-check). These facts
        are static and known-good -- no probing round-trip required.
        """
        return DriverCapabilities(
            name=self.name,
            parallel_dispatch=True,          # native parallel() in the harness
            worker_filesystem_access=True,   # workers use Read/Write tools
            worker_shell_access=True,        # workers use the Bash tool
            structured_output=True,          # agent(schema=...) is near-perfect
            worktree_isolation=True,         # orchestrator manages git worktrees
            native_cost_tracking=True,       # budget.spent() real-time API
            native_stall_detection=True,     # harness + heartbeat liveness
            tool_use_accuracy=0.99,
            recommended_verification_tier=1,
            available_models=("haiku", "sonnet", "opus"),
            notes=(
                "Reference backend. Self-contained: no external harness. "
                "dispatch_worker/worker_status/run_command are serviced by the "
                "Claude Code Workflow harness in a live wave."
            ),
        )

    # -- Operation 2: dispatch (harness-serviced) --------------------------
    def dispatch_worker(self, request: WorkerRequest) -> WorkerResult:
        """Documented seam: real dispatch happens in the harness.

        In a live Claude Code wave the orchestrator spawns the worker via the
        harness's agent()/Task tool with the resolved model, the owned-files
        contract, and (optionally) request.result_schema. From plain Python
        there is no Claude agent to spawn, so we raise a clear, explained error
        rather than fake a result.
        """
        raise NotImplementedError(_HARNESS_ONLY.format(op="dispatch_worker"))

    # -- Operation 3: stall detection (harness-serviced) -------------------
    def worker_status(self, worker_id: str) -> WorkerStatus:
        """Documented seam: liveness comes from the harness + heartbeat files.

        A live wave reads worker liveness from the harness and the fleet's
        heartbeat/state files. Out of harness there is nothing to observe, so
        we report UNKNOWN honestly.
        """
        return WorkerStatus(
            worker_id=worker_id,
            state=WORKER_UNKNOWN,
            stalled=False,
            age_s=0.0,
            detail=_HARNESS_ONLY.format(op="worker_status"),
        )

    # -- Operation 4: orchestrator-side command (real) ---------------------
    def run_command(
        self,
        command: str,
        cwd: Optional[str] = None,
        shell: Optional[str] = None,
    ) -> CommandResult:
        """Run a command on the orchestrator host.

        In a live wave this is the Bash tool; out of harness we back it with a
        real subprocess so tooling/tests get genuine behavior. `shell` is
        advisory -- we always execute through the platform shell so the same
        call works on Windows and Linux.

        Timeouts are enforced: if the command exceeds timeout_s, the process is
        terminated and a non-zero exit code is returned.
        """
        try:
            completed = subprocess.run(
                command,
                cwd=cwd,
                shell=True,
                capture_output=True,
                text=True,
                timeout=self._timeout_s,
            )
            return CommandResult(
                exit_code=completed.returncode,
                stdout=completed.stdout or "",
                stderr=completed.stderr or "",
            )
        except subprocess.TimeoutExpired:
            # Return a clear timeout status: non-zero exit code.
            # Note: On Windows, the process may not be fully killed due to shell process
            # tree limitations, but we return immediately with timeout status.
            return CommandResult(exit_code=124, stdout="", stderr="Command timed out")
        except OSError as exc:
            return CommandResult(exit_code=127, stdout="", stderr=str(exc))

    # -- Operation 5: model selection (concrete) ---------------------------
    def resolve_model(self, role: str) -> str:
        """Map an abstract role to an Anthropic model name.

        Unknown roles fall back to the worker (Haiku) mapping so a mis-typed
        role can never silently escalate cost.
        """
        return self._model_map.get(role, self._model_map[ROLE_WORKER])

    # -- Optional: cost tracking -------------------------------------------
    def get_tokens_spent(self) -> Optional[int]:
        """Return None: per-instance spend is not observable from the harness-serviced driver.

        CONTRACT:
          The orchestrator-side driver cannot directly observe driver instance spend
          because dispatch happens inside the harness's Workflow context (agent()/Task
          tool), not in this Python process. In a live wave the harness-side budget.spent()
          API is not exposed to this adapter.

        DESIGN:
          Cost enforcement is delivered by the wave loop passing None here, which causes
          cost_ceiling.check() to perform its own windowed ledger fallback. This ensures
          that period windowing (e.g., daily caps) is respected: cost_ceiling reads the
          ledger directly and filters rows by the requested period. Returning a lifetime
          sum would bypass that windowing logic and cause false trips after wave 2+.

        See cost_ceiling.py:read_ledger_total_tokens() for the authoritative windowing
        implementation.
        """
        return None
