# Spec: chat-orchestrator-backend (F1)

Source plan: C:/Users/Mattt/Desktop/aesop-chat-plan.md sections 3.1, 3.2, 3.4, 4 (C1, C2). Authorization: Matt 2026-11-21, "all state currentlyy lives in conductor3 which is awful, aesop is literally the manager".
Frozen original: `chat-orchestrator-backend.original.md` (byte-identical, never edited).

## Goal

A line typed in `aesop chat` must reach a real orchestrator through aesop's own inbox, and the orchestrator's reply must stream back into the chat transcript. Today `Session` echoes bytes through `pipeSource`, so chat talks to nothing. WHY: aesop is the manager; its chat surface must be a real channel to the orchestrator, and a silent orchestrator must be an explicit, bounded outcome (Cardinal Rule 3: silence = hang), never a stuck prompt.

## Decisions

- Answering orchestrator (RESOLVED 2026-11-21, Matt): chat lines are answered by whichever orchestrator backend is configured in the `driver/orchestrator-swap` seat (Claude Code session, Codex, etc.), selected by config rather than hardcoded. When none is configured or seated, chat shows "no orchestrator listening". See `docs/specs/README.md` section Decisions.

## Non-goals

- No provider calls from chat of its own: no `claude -p`, no headless CLI, no direct API (Matt HARD RULE). The configured seat is reached only through its existing seam or the aesop inbox.
- No change to `Session` or `render.mjs`; the seam is the `source` argument.
- No gated actions (see F2 chat-intent-approve) and no state migration (see F3 aesop-owns-state).
- No new dependencies; Node stdlib only, no `package.json` in `chat/`.

## Requirements

Each requirement = observable behavior; "Check" = offline-testable acceptance.

- F1-R1: `chat/backends/orchestrator_source.mjs` exports `orchestratorSource({stateRoot, inboxPath, onIntent})` returning `(line) => AsyncIterable<string>`. Check: unit test imports it and iterates the result as an async generator of strings.
- F1-R2: `runChat` selects the backend via `AESOP_CHAT_BACKEND=orchestrator|pipe`; unset means `pipe`. Check: with the var unset, existing chat tests pass byte-identical (plan A13).
- F1-R3: Each non-slash turn gets a `turn_id` and appends `- [ISO-ts] chat(turn_id): <text>` to the aesop-owned `ui-inbox.md`. Check: temp `AESOP_STATE_ROOT`, submit "hello", inbox file contains exactly one matching line.
- F1-R4: Reply chunks appended by the bridge (C1: `state/chat/<turn_id>.jsonl`) are yielded in order, and `final: true` ends the turn with `streaming` false. Check: fake bridge writes two chunks and a final; transcript shows both in order (A11).
- F1-R5: With no orchestrator configured or seated, the turn ends within the timeout (default 120 s, env override) with the system line "no orchestrator listening", and the inbox line exists. Check: set timeout to ~1 s in test; assert line, return, and inbox line (A12).
- F1-R6: Replies for a different `turn_id` are never shown in the current turn. Check: interleave a second turn's chunks in the reply stream; transcript of turn one excludes them (A14).
- F1-R7: C2 transport: submit also writes event `inbox/turn.submitted {turn_id, text}` via `tools/chat_bridge.py append` called with `execFile` (no shell, utf-8); replies tail `get_since('inbox', last_version)` with strictly increasing versions. Check: test asserts event written, versions strictly increasing (A14).
- F1-R8: Inbox line and event are both written and a drain marks them processed exactly once. Check: run drain twice, second pass reports zero pending (A15).
- F1-R9: `tools/chat_bridge.py serve` is a bounded foreground loop (max iterations or duration) that drains pending turns and routes each to the orchestrator seat selected in `driver/orchestrator-swap` config. Check: with max-iterations=1, a temp root, and a fake configured seat, `serve` exits 0 and emits one reply event for the seeded turn.
- F1-R10: The tail loop is bounded with backoff inside the generator (not an unbounded shell poll). Check: test with no replies terminates by timeout, and a source-grep test confirms no `while true` shell polling and no `claude -p`/CLI invocation in `chat/backends/`.
- F1-R11: Zero new dependencies. Check: `chat/` contains no `package.json`; imports limited to node builtins (grep check).
- F1-R12: The answering backend is chosen by config, never hardcoded; with no backend configured or seated, `serve` emits the reply "no orchestrator listening" and exits 0. Check: swapping the config value routes the same seeded turn to a second fake seat with no code change; with config empty, the reply text matches exactly.

## Risks

- The inbox is shared with dashboard, Codex and agy; malformed chat lines could corrupt `inbox_drain` parsing (mitigate: strict single-line escaping of text).
- Polling latency vs. bounded-loop rule; backoff tuning.
- Replies arrive only while the configured seat is live; otherwise the turn ends at the timeout with "no orchestrator listening" and the message stays in the inbox.
- Before F3 M1 the aesop-owned inbox path differs from conductor3's; foreign agents still write the old path.

## Open questions for Matt (verbatim from the plan)

1. (BLOCKING, RESOLVED 2026-11-21) "For chat replies: is the answering orchestrator the live Claude Code session (replies appear only when that session processes the inbox), or do you want an always-on `OrchestratorBackend` (for example a local/openai-compatible seat) that answers chat even with no session open?"
   Matt's answer: "configured orchestrator...". Chat lines are answered by whichever orchestrator backend is configured in the driver/orchestrator-swap seat (Claude Code session, Codex, etc.), selected by config rather than hardcoded. When none is configured or seated, chat shows "no orchestrator listening".
2. (not blocking) "Are foreign agents (Codex, agy) allowed to be told a new inbox path (AGENTS.md currently hardcodes `conductor3/state/ui-inbox.md`), or must the old path keep accepting appends indefinitely?" Default until answered: keep accepting appends. Open.
