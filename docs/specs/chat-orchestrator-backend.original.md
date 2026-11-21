# Spec: chat-orchestrator-backend (F1)

Source plan: C:/Users/Mattt/Desktop/aesop-chat-plan.md sections 3.1, 3.2, 3.4, 4 (C1, C2). Authorization: Matt 2026-11-21, "all state currentlyy lives in conductor3 which is awful, aesop is literally the manager".
Frozen original: `chat-orchestrator-backend.original.md` (byte-identical, never edited).

## Goal

A line typed in `aesop chat` must reach a real orchestrator through aesop's own inbox, and the orchestrator's reply must stream back into the chat transcript. Today `Session` echoes bytes through `pipeSource`, so chat talks to nothing. WHY: aesop is the manager; its chat surface must be a real channel to the orchestrator, and a silent orchestrator must be an explicit, bounded outcome (Cardinal Rule 3: silence = hang), never a stuck prompt.

## Non-goals

- No provider calls from chat, no `claude -p`, no headless CLI, no direct API (Matt HARD RULE).
- No change to `Session` or `render.mjs`; the seam is the `source` argument.
- No gated actions (see F2 chat-intent-approve) and no state migration (see F3 aesop-owns-state).
- No new dependencies; Node stdlib only, no `package.json` in `chat/`.

## Requirements

Each requirement = observable behavior; "Check" = offline-testable acceptance.

- F1-R1: `chat/backends/orchestrator_source.mjs` exports `orchestratorSource({stateRoot, inboxPath, onIntent})` returning `(line) => AsyncIterable<string>`. Check: unit test imports it and iterates the result as an async generator of strings.
- F1-R2: `runChat` selects the backend via `AESOP_CHAT_BACKEND=orchestrator|pipe`; unset means `pipe`. Check: with the var unset, existing chat tests pass byte-identical (plan A13).
- F1-R3: Each non-slash turn gets a `turn_id` and appends `- [ISO-ts] chat(turn_id): <text>` to the aesop-owned `ui-inbox.md`. Check: temp `AESOP_STATE_ROOT`, submit "hello", inbox file contains exactly one matching line.
- F1-R4: Reply chunks appended by the bridge (C1: `state/chat/<turn_id>.jsonl`) are yielded in order, and `final: true` ends the turn with `streaming` false. Check: fake bridge writes two chunks and a final; transcript shows both in order (A11).
- F1-R5: With no bridge running, the turn ends within the timeout (default 120 s, env override) with the system line "no orchestrator is listening; message left in inbox", and the inbox line exists. Check: set timeout to ~1 s in test; assert line, return, and inbox line (A12).
- F1-R6: Replies for a different `turn_id` are never shown in the current turn. Check: interleave a second turn's chunks in the reply stream; transcript of turn one excludes them (A14).
- F1-R7: C2 transport: submit also writes event `inbox/turn.submitted {turn_id, text}` via `tools/chat_bridge.py append` called with `execFile` (no shell, utf-8); replies tail `get_since('inbox', last_version)` with strictly increasing versions. Check: test asserts event written, versions strictly increasing (A14).
- F1-R8: Inbox line and event are both written and a drain marks them processed exactly once. Check: run drain twice, second pass reports zero pending (A15).
- F1-R9: `tools/chat_bridge.py serve` is a bounded foreground loop (finite max duration/iterations) that drains pending items and routes text via the `OrchestratorBackend` seam or the `inbox_drain pending` path. Check: run with max-iterations=1 in a temp root; it exits 0 and emits at least one reply event for a seeded turn.
- F1-R10: The tail loop is bounded with backoff inside the generator (not an unbounded shell poll). Check: test with no replies terminates by timeout, and a source-grep test confirms no `while true` shell polling and no `claude -p`/CLI invocation in `chat/backends/`.
- F1-R11: Zero new dependencies. Check: `chat/` contains no `package.json`; imports limited to node builtins (grep check).

## Risks

- The inbox is shared with dashboard, Codex and agy; malformed chat lines could corrupt `inbox_drain` parsing (mitigate: strict single-line escaping of text).
- Polling latency vs. bounded-loop rule; backoff tuning.
- Answering seat (live session vs always-on backend) changes whether replies ever arrive (see Q1).
- Before F3 M1 the aesop-owned inbox path differs from conductor3's; foreign agents still write the old path.

## Open questions for Matt (verbatim from the plan)

1. (BLOCKING) "For chat replies: is the answering orchestrator the live Claude Code session (replies appear only when that session processes the inbox), or do you want an always-on `OrchestratorBackend` (for example a local/openai-compatible seat) that answers chat even with no session open?" Blocks F1-R9 design (which side `serve` binds to). Not blocking F1-R1..R6 (C1 file transport).
2. (not blocking) "Are foreign agents (Codex, agy) allowed to be told a new inbox path (AGENTS.md currently hardcodes `conductor3/state/ui-inbox.md`), or must the old path keep accepting appends indefinitely?" Default until answered: keep accepting appends.
