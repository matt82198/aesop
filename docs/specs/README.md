# docs/specs: frozen planned-feature specs

Durable, testable specs for aesop's planned features, derived from C:/Users/Mattt/Desktop/aesop-chat-plan.md (Matt 2026-11-21). They exist so maker/checker loops can measure drift against the frozen original plan.

| Feature | Spec | Frozen original | Requirements |
|---|---|---|---|
| F1 chat-orchestrator-backend | `chat-orchestrator-backend.md` | `chat-orchestrator-backend.original.md` | F1-R1..R12 |
| F2 chat-intent-approve | `chat-intent-approve.md` | `chat-intent-approve.original.md` | F2-R1..R12 |
| F3 aesop-owns-state | `aesop-owns-state.md` | `aesop-owns-state.original.md` | F3-R1..R16 |

## Convention

- `<feature>.md` is the live spec: goal, non-goals, numbered requirements `F<k>-R<n>` (each with an offline-testable Check, mapped to plan criteria A1-A19 where one applies), risks, and the open questions for Matt (marked BLOCKING or not).
- `<feature>.original.md` is a byte-identical frozen copy made in the commit that introduced the spec. It must never change. Verify at creation with `cmp`, and `git log --follow` on an original must show exactly one commit.
- Amend only the live spec; the drift number then shows how far it moved.

## Decisions

Matt's answers to the blocking open questions (2026-11-21). Each is recorded verbatim in its spec's open-questions section and reflected in the rewritten requirements. Labels: plan Q1, Q3, Q4 as numbered in the lane brief; the spec-local labels are F3 Q1, F1 Q1 and F2 Q1.

1. **State root (plan Q1, spec F3 Q1)**: "aesop repo state/". The single state root is the aesop repo's `state/` directory (git-ignored, the default `AESOP_STATE_ROOT`). Affects `aesop-owns-state.md` F3-R3 onward.
2. **Chat answerer (plan Q3, spec F1 Q1)**: "configured orchestrator...". Chat lines are answered by whichever orchestrator backend is configured in the `driver/orchestrator-swap` seat (Claude Code session, Codex, etc.), selected by config rather than hardcoded. When none is configured or seated, chat shows "no orchestrator listening". Affects `chat-orchestrator-backend.md` F1-R5, F1-R9, F1-R12.
3. **/approve authority (plan Q4, spec F2 Q1)**: "Within stated bounds". `/approve <id>` in chat runs anything inside Matt's decide-and-report bounds after the `.HALT`, `cost_ceiling` and `wave_preflight` gates pass. Deploys, spending, deletes and external messages are always refused from chat. Affects `chat-intent-approve.md` F2-R4, R5, R7, R8.

## Drift measures

1. Plan drift = `100 * (1 - LCS_similarity(spec, original))`, where LCS similarity is line-level: `2 * LCS_lines / (lines(spec) + lines(original))`. Pure diff from the original; no judgment.
2. Implementation drift = `unmet requirement tests / total requirement tests * 100`, measured like `tools/chat_spec_drift.mjs` on branch `feat/chat-tui-improve-loop`.

Exit ceiling (Matt, 2026-11-21): drift must stay at or below 86%. Above it, the loop does not exit and the divergence is escalated. <!-- metrics-verified: Matt 2026-11-21 stated threshold, not a measured stat -->

These specs are text only; code and tests that enforce them belong to later PRs.
