# Deterministic Harness Architecture

Aesop separates reasoning from enforcement. Enforcement, verification, watchdogs, and retry limits execute as deterministic state machines in code rather than model cognition. The model orchestrator only decomposes work, routes bounded tasks, and escalates terminal blocks.

## Architecture overview

The harness is organized into three layers:

```text
+======================================================================+
| Layer 1: Thin Model Orchestrator                                     |
| Decomposes goals -> routes scoped briefs -> escalates BLOCKED tasks  |
+======================================================================+
         |                                                 ^
         | scoped brief                                    | ACCEPT verdict
         v                                                 | or BLOCKED task
+======================================================================+
| Layer 2: Deterministic State-Machine Enforcement                     |
|  * Typed hook denials (gate-denial.mjs) -> GATE-DENY + v1 JSON       |
|  * Retry ledger (retry_ledger.py) -> RUNNING -> RETRY -> BLOCKED (3) |
|  * Pre-push gates + Ed25519 receipts (verify_receipt.py)             |
|  * Hand-back gate (verify_handback.py) -> ACCEPT | REJECT | ERROR    |
|  * Watchdog (run-watchdog.sh) + hash-chained and WAL event stores    |
+======================================================================+
         |                                                 ^
         | isolated spawn                                  | receipt + diff
         v                                                 | + test_run
+======================================================================+
| Layer 3: Stateless Isolated Workers (Linux, macOS, Windows)          |
| One sibling git worktree per lane -> killed on failure, never fixed  |
+======================================================================+
```

1. **Thin model orchestrator.** Holds high-level intent, decomposes changes into single-domain briefs with explicit file-scope globs, routes each brief to a worker, and escalates only when a deterministic gate reports `BLOCKED` or `ERROR`. Because enforcement lives outside the prompt, the orchestrator model is a swappable configuration lever on the orchestrator seat (see [Two swappable seats](./MICROKERNEL.md) and [../driver/wave_loop.py](../driver/wave_loop.py)).
2. **Deterministic state-machine enforcement layer.** Runs before tool execution, at `git push`, at worker hand-back, and on background timers. Every transition is computed from exit codes, cryptographic signatures, git tree hashes, path globs, and durable counters on disk.
3. **Stateless isolated workers.** Execute inside fresh sibling git worktrees across Linux, macOS, and Windows (see [Crash-only operation](./concepts/crash-only.md) and [../LANE-CONTRACT.md](../LANE-CONTRACT.md)). Workers hold no durable memory in-process; a failed lane is terminated and replaced by a fresh dispatch rather than repaired in conversation.

## Deterministic code vs model cognition

| Responsibility | Owner | Aesop mechanism |
| :- | :- | :- |
| Task decomposition and scope slicing | Model cognition | Orchestrator seat splits a goal into bounded briefs with owned path globs ([Two swappable seats](./MICROKERNEL.md)). |
| Routing and model tier selection | Model cognition + config | Seat configuration in [../driver/backend_config.py](../driver/backend_config.py) and policy enforcement in [../hooks/claude/force-model-policy.mjs](../hooks/claude/force-model-policy.mjs). |
| Escalation and contract updates | Model cognition | Triggered only after deterministic gates emit `BLOCKED` or `ERROR`. |
| Pre-tool policy denials | Deterministic code | Hook guards emit a `GATE-DENY` line plus a typed v1 JSON remedy (`schemas/gate-denial.v1.json`, `hooks/claude/gate-denial.mjs`). |
| Push gates and signed receipts | Deterministic code | [../hooks/pre-push-policy.sh](../hooks/pre-push-policy.sh), [../tools/emit_receipt.py](../tools/emit_receipt.py), and [../tools/verify_receipt.py](../tools/verify_receipt.py) verify Ed25519 signatures (or HMAC-SHA256) against a recomputed git tree hash. |
| Hand-back acceptance | Deterministic code | `tools/verify_handback.py` checks receipt validity, worktree cleanliness, diff scope globs, and `test_run` exit status before the orchestrator reads the report. |
| Retry counting and stale-task detection | Deterministic code | `tools/retry_ledger.py` tracks attempts and heartbeats under `AESOP_STATE_ROOT`, capping automatic retries at `3` before returning `BLOCKED`. |
| Tamper-evident history and state | Deterministic code | SHA-256 hash-chained JSONL logs in [../tools/event_store/store.py](../tools/event_store/store.py) and WAL-mode SQLite state in [../state_store/store.py](../state_store/store.py). |
| Fleet backup, secret scan, and halt brake | Deterministic code | [../daemons/run-watchdog.sh](../daemons/run-watchdog.sh), [../daemons/selfheal.sh](../daemons/selfheal.sh), [../tools/secret_scan.py](../tools/secret_scan.py), and [../tools/halt.py](../tools/halt.py). |

## Mechanical interfaces

### 1. Typed hook denials (`schemas/gate-denial.v1.json` and `hooks/claude/gate-denial.mjs`)

Pre-tool hooks refuse disallowed tool shapes before execution. The shared emitter `hooks/claude/gate-denial.mjs` preserves existing denial rules and outputs both the human-readable `GATE-DENY` line and a structured payload conforming to `schemas/gate-denial.v1.json`:

```json
{
  "v": 1,
  "deny": true,
  "gate": "no-heredoc-file-authoring",
  "reason": "Heredocs are banned in shell commands; write a file directly instead.",
  "remedy": {
    "action": "use_tool",
    "args": {
      "tool": "Write"
    }
  },
  "allowed_tools": ["Write", "Edit"]
}
```

Allowed `remedy.action` values are closed under v1:

- `dispatch_reader`: delegate file or log inspection to a scoped reader worker.
- `retry_with`: re-issue the call using the compliant shape supplied in `remedy.args`.
- `use_tool`: switch to a dedicated tool named in `allowed_tools` and `remedy.args`.
- `stop_and_report`: halt the current attempt immediately and return the literal gate output.

### 2. Hand-back verifier (`tools/verify_handback.py`)

`tools/verify_handback.py` runs as a deterministic gate between worker completion and orchestrator review. It rejects a lane hand-back before the orchestrator reads it unless all four checks pass:

1. **Signed receipt verifies:** invokes [../tools/verify_receipt.py](../tools/verify_receipt.py) as a library to confirm the Ed25519 or HMAC-SHA256 signature and recomputed git tree hash (`exit 0`).
2. **Clean worktree:** confirms the worker left no uncommitted or untracked residue in its worktree.
3. **Scoped diff:** verifies every changed path between base and head matches the lane's declared scope globs from the brief or hand-back.
4. **Passing test run:** confirms the recorded `test_run` command finished with `exit_code` equal to `0`.

The verifier emits a typed JSON verdict (`ACCEPT|REJECT|ERROR`):

```json
{
  "verdict": "ACCEPT",
  "checks": {
    "receipt_valid": true,
    "worktree_clean": true,
    "scope_clean": true,
    "test_run": {
      "exit_code": 0
    }
  },
  "errors": []
}
```

`ACCEPT` advances the hand-back to the orchestrator, `REJECT` routes a structured failure to the retry ledger when a check fails, and `ERROR` fails closed when inputs, git state, or receipt material cannot be evaluated.

### 3. Mechanical retry ledger (`tools/retry_ledger.py`)

`tools/retry_ledger.py` replaces prose retry conventions with a durable state machine stored under `AESOP_STATE_ROOT` (defaulting to the `state` subdirectory of `AESOP_ROOT`). It exposes five deterministic operations over task identifiers:

- `start(task)`: initializes or resumes `task` in `RUNNING` state and records its initial heartbeat timestamp.
- `heartbeat(task)`: updates the latest liveness timestamp for `task` without changing its failure count.
- `fail(task, reason)`: increments the durable failure count for `task` and appends `reason`.
- `status(task)`: returns `RUNNING`, `RETRY`, or `BLOCKED` (`RUNNING|RETRY|BLOCKED`), transitioning deterministically to `BLOCKED` once failures reach the configured maximum (default `3`).
- `stale(threshold_s)`: returns tasks whose last recorded heartbeat is older than `threshold_s` seconds so the watchdog ([../daemons/run-watchdog.sh](../daemons/run-watchdog.sh)) can surface stalled work without polling model transcripts.

## Related pages

- [The receipts loop](./concepts/receipts-loop.md): why an iteration counts only when a gate verifies its signed receipt.
- [Signed receipts](./concepts/signed-receipts.md): envelope fields, Ed25519 keys, and tree-hash binding.
- [Event store](./concepts/event-store.md): hash-chained append-only JSONL storage in [../tools/event_store/store.py](../tools/event_store/store.py).
- [Gates and hooks](./concepts/gates-and-hooks.md): local pre-push enforcement and hosted verification boundaries.
