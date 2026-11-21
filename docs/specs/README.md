# docs/specs: frozen planned-feature specs

Durable, testable specs for aesop's planned features, derived from C:/Users/Mattt/Desktop/aesop-chat-plan.md (Matt 2026-11-21). They exist so maker/checker loops can measure drift against the frozen original plan.

| Feature | Spec | Frozen original | Requirements |
|---|---|---|---|
| F1 chat-orchestrator-backend | `chat-orchestrator-backend.md` | `chat-orchestrator-backend.original.md` | F1-R1..R11 |
| F2 chat-intent-approve | `chat-intent-approve.md` | `chat-intent-approve.original.md` | F2-R1..R12 |
| F3 aesop-owns-state | `aesop-owns-state.md` | `aesop-owns-state.original.md` | F3-R1..R16 |

## Convention

- `<feature>.md` is the live spec: goal, non-goals, numbered requirements `F<k>-R<n>` (each with an offline-testable Check, mapped to plan criteria A1-A19 where one applies), risks, and the open questions for Matt (marked BLOCKING or not).
- `<feature>.original.md` is a byte-identical frozen copy made in the commit that introduced the spec. It must never change. Verify at creation with `cmp`, and `git log --follow` on an original must show exactly one commit.
- Amend only the live spec; the drift number then shows how far it moved.

## Drift measures

1. Plan drift = `100 * (1 - LCS_similarity(spec, original))`, where LCS similarity is line-level: `2 * LCS_lines / (lines(spec) + lines(original))`. Pure diff from the original; no judgment.
2. Implementation drift = `unmet requirement tests / total requirement tests * 100`, measured like `tools/chat_spec_drift.mjs` on branch `feat/chat-tui-improve-loop`.

Exit ceiling (Matt, 2026-11-21): drift must stay at or below 86%. Above it, the loop does not exit and the divergence is escalated. <!-- metrics-verified: Matt 2026-11-21 stated threshold, not a measured stat -->

(Sibling: C:/Users/Mattt/Desktop/aesop-chat-plan.md)

These specs are text only; code and tests that enforce them belong to later PRs.
