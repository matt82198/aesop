# Spec: chat-intent-approve (F2)

Source plan: C:/Users/Mattt/Desktop/aesop-chat-plan.md sections 3.3, 4 (C3). Authorization: Matt 2026-11-21, "all state currentlyy lives in conductor3 which is awful, aesop is literally the manager".
Frozen original: `chat-intent-approve.original.md` (byte-identical, never edited).

## Goal

Make "dispatch fleet" typed in chat a real action, but only behind an explicit approval and the existing safety gates. The chat process never executes a dispatch itself; it emits `intent.proposed`, the human types `/approve <id>`, and a separate executor runs only approved intents after `.HALT`, `cost_ceiling`, `wave_preflight` and the hard stops pass. WHY: chat is the lowest-friction surface and therefore the easiest place to cause an unauthorized fleet run; fail-closed gating with an auditable event trail keeps Cardinal Rules 1 and 6 intact (spend and deploy always wait for Matt).

## Non-goals

- No execution from the chat process; no auto-approval.
- No spend or deploy intents ever executed (rejected, not merely discouraged).
- No loosening or bypass of any hook, gate, or threshold.
- No free-text model classification of the verb; the intent table is fixed and versioned.
- Transport and streaming are F1; state migration is F3.

## Requirements

- F2-R1: The bridge classifies a turn with a fixed versioned intent table (`dispatch fleet`, `halt`, `status`, `drain inbox`); anything else is `kind: chat`. Check: table-driven test over each verb plus free text and a near-miss verb (unknown means chat or refusal, never an intent).
- F2-R2: `status` is answered read-only from `state_store` projections with no orchestrator. Check: with no bridge/orchestrator running, `status` returns a projection reply and writes no intent event.
- F2-R3: "dispatch fleet" emits `intent.proposed {intent, args, turn_id}` and replies with a plan ("Proposed: wave <n>, lanes <k>, est cost $X, ceiling $Y. Reply `/approve <id>` to run."). With no approval, spawns nothing. Check: executor spy 0 calls; reply contains the proposal and id (A16).
- F2-R4: `parseSlash` gains `/approve <id>` and `/deny <id>`, producing `intent.approved` / `intent.denied`. Check: parser unit tests, including missing or malformed id (refused with a visible reply).
- F2-R5: The executor (`tools/chat_bridge.py exec`) runs only intents with a matching approval event. Check: approval for id A does not run intent B; no approval runs nothing.
- F2-R6: Before executing, gates run in order: `.HALT` absent, `tools/cost_ceiling.py`, `wave_preflight.py`, `multi_dispatch` preflight, grants.md hard stops. Check: stub each gate to fail one at a time; each yields refusal plus `intent.rejected` and no execution.
- F2-R7: `/approve <id>` with `.HALT` present is refused and logged `intent.rejected`; after HALT removed in a fixture, re-approve executes exactly once, and a duplicate approve is idempotent. Check: A17.
- F2-R8: Spend-class and deploy-class intents are rejected regardless of approval. Check: A18, both classes, approval present, executor 0 calls.
- F2-R9: Fail closed: unknown verb, missing approval, HALT present, or any gate error produces a visible refusal reply and an `intent.rejected` event; never a silent drop. Check: one test per cause asserting both reply and event.
- F2-R10: Every intent, approval, rejection and result is an event with actor `chat:<session>`. Check: after a full propose/approve/execute run, the event log lets a query answer "who dispatched" with the session actor.
- F2-R11: Mutation proof: stub the approval check to always true and A16 goes RED; restore and it is GREEN (A19). Check: mutation test in the suite, red evidence recorded per LANE-CONTRACT.
- F2-R12: Chat issues no provider/CLI calls. Check: grep test that chat and bridge code contain no `claude -p`/headless invocation.

## Risks

- Approval typed in the same channel as the proposal may be weaker authority than an out-of-band one (Q1).
- Gate stubs diverging from real gate semantics (`cost_ceiling`, `wave_preflight` interfaces change).
- Duplicate or replayed approve events double-dispatching (idempotency key = intent id).
- Cost estimate shown in the proposal could be stale at approval time; executor must re-check the gates, not trust the proposal.

## Open questions for Matt (verbatim from the plan)

1. (BLOCKING) "Gate for "dispatch fleet": is `/approve <id>` typed in chat sufficient authority for a fleet dispatch within the cost ceiling, or must approval always come from the seated orchestrator or Matt outside chat?" Blocks F2-R4, R5, R7 (who may approve). F2-R1..R3 and R8..R9 can proceed because they only refuse.
