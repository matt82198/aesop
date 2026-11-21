# Spec: chat-intent-approve (F2)

Source plan: C:/Users/Mattt/Desktop/aesop-chat-plan.md sections 3.3, 4 (C3). Authorization: Matt 2026-11-21, "all state currentlyy lives in conductor3 which is awful, aesop is literally the manager".
Frozen original: `chat-intent-approve.original.md` (byte-identical, never edited).

## Goal

Make "dispatch fleet" typed in chat a real action, but only behind an explicit approval and the existing safety gates. The chat process never executes a dispatch itself; it emits `intent.proposed`, the human types `/approve <id>`, and a separate executor runs only approved intents after `.HALT`, `cost_ceiling`, `wave_preflight` and the hard stops pass. WHY: chat is the lowest-friction surface and therefore the easiest place to cause an unauthorized fleet run; fail-closed gating with an auditable event trail keeps Cardinal Rules 1 and 6 intact (spend and deploy always wait for Matt).

## Decisions

- Approval authority (RESOLVED 2026-11-21, Matt): `/approve <id>` in chat runs anything inside Matt's decide-and-report bounds after the `.HALT`, `cost_ceiling` and `wave_preflight` gates pass. Deploys, spending, deletes and external messages are always refused from chat. See `docs/specs/README.md` section Decisions.

## Non-goals

- No execution from the chat process; no auto-approval.
- No spend, deploy, delete or external-message intent is ever executed from chat (rejected, not merely discouraged).
- No loosening or bypass of any hook, gate, or threshold.
- No free-text model classification of the verb; the intent table is fixed and versioned.
- Transport and streaming are F1; state migration is F3.

## Requirements

- F2-R1: The bridge classifies a turn with a fixed versioned intent table (`dispatch fleet`, `halt`, `status`, `drain inbox`); anything else is `kind: chat`. Check: table-driven test over each verb plus free text and a near-miss verb (unknown means chat or refusal, never an intent).
- F2-R2: `status` is answered read-only from `state_store` projections with no orchestrator. Check: with no bridge/orchestrator running, `status` returns a projection reply and writes no intent event.
- F2-R3: "dispatch fleet" emits `intent.proposed {intent, args, turn_id}` and replies with a plan ("Proposed: wave <n>, lanes <k>, est cost $X, ceiling $Y. Reply `/approve <id>` to run."). With no approval, spawns nothing. Check: executor spy 0 calls; reply contains the proposal and id (A16).
- F2-R4: `parseSlash` gains `/approve <id>` and `/deny <id>`, producing `intent.approved` / `intent.denied`; a missing or malformed id is refused with a visible reply and writes no approval event. Check: parser unit tests for valid, missing-id and malformed-id inputs.
- F2-R5: The executor (`tools/chat_bridge.py exec`) runs only intents with a matching approval event whose class is inside the decide-and-report bounds in `docs/grants.md`. Check: approval for id A does not run intent B; no approval runs nothing; an approved out-of-bounds intent runs nothing (three fixture cases).
- F2-R6: Before executing, gates run in order: `.HALT` absent, `tools/cost_ceiling.py`, `wave_preflight.py`, `multi_dispatch` preflight, grants.md hard stops. Check: stub each gate to fail one at a time; each yields refusal plus `intent.rejected` and no execution.
- F2-R7: `/approve <id>` with `.HALT` present is refused and logged `intent.rejected`; after HALT is removed in a fixture, re-approve executes exactly once, and a duplicate approve is idempotent (key = intent id). Check: A17 with fixture execution counts 0, then 1, then still 1.
- F2-R8: Spend-class, deploy-class, delete-class and external-message intents are refused from chat regardless of approval and never executed. Check: A18, four classes each with approval present, executor 0 calls, refusal reply plus `intent.rejected`.
- F2-R9: Fail closed: unknown verb, missing approval, HALT present, or any gate error produces a visible refusal reply and an `intent.rejected` event; never a silent drop. Check: one test per cause asserting both reply and event.
- F2-R10: Every intent, approval, rejection and result is an event with actor `chat:<session>`. Check: after a full propose/approve/execute run, the event log lets a query answer "who dispatched" with the session actor.
- F2-R11: Mutation proof: stub the approval check to always true and A16 goes RED; restore and it is GREEN (A19). Check: mutation test in the suite, red evidence recorded per LANE-CONTRACT.
- F2-R12: Chat issues no provider/CLI calls. Check: grep test that chat and bridge code contain no `claude -p`/headless invocation.

## Risks

- Chat approval is the accepted authority within bounds (Matt 2026-11-21); the residual risk is a replayed or stale approval (mitigated by the intent-id idempotency key and by F2-R6 re-checking gates at execution time).
- Gate stubs diverging from real gate semantics (`cost_ceiling`, `wave_preflight` interfaces change).
- Duplicate or replayed approve events double-dispatching (idempotency key = intent id).
- Cost estimate shown in the proposal could be stale at approval time; executor must re-check the gates, not trust the proposal.

## Open questions for Matt (verbatim from the plan)

1. (BLOCKING, RESOLVED 2026-11-21) "Gate for "dispatch fleet": is `/approve <id>` typed in chat sufficient authority for a fleet dispatch within the cost ceiling, or must approval always come from the seated orchestrator or Matt outside chat?"
   Matt's answer: "Within stated bounds". `/approve <id>` in chat runs anything inside Matt's decide-and-report bounds after the `.HALT`, `cost_ceiling` and `wave_preflight` gates pass. Deploys, spending, deletes and external messages are always refused from chat.
