# Tiered Cognition/Execution — spike + staged wave-12 artifact

> **STATUS: CANCELLED 2026-07-14** after A/B testing measured 4.6× weighted cost for identical quality (`docs/ab-cost-dataset.md:15`; see MEMORY.md wave-11 entry). <!-- metrics-verified: docs/ab-cost-dataset.md:15 --> Kept for reference; do not activate. Nothing in this directory is wired into `~/.claude/settings.json` or `~/.claude/hooks/`. The live model-policy hook (`force-haiku-subagents.mjs`) is untouched and still governs the fleet.

## Contents

| File | Status | What |
|---|---|---|
| `DESIGN.md` | wave-11 spike | Architecture, hook mechanism, WORK-ORDER/EXEC-BRIEF schemas, executed round-trip |
| `FINDINGS.md` | wave-11 spike | GO (conditional) verdict, risks, phased rollout plan |
| `strip-tools-hook.mjs` | wave-11 prototype | Tier policy alone (superseded by the merged file below) |
| `aesop-cognition.example.md` | example, not installed | Message-only shim agent definition the merged hook rewrites to |
| **`force-model-policy.merged.mjs`** | **wave-12 STAGED CANDIDATE** | Model policy + tier policy merged into ONE hook — the single `updatedInput` owner for the `Agent\|Task` matcher. Self-test: 51/51 PASS (`node force-model-policy.merged.mjs --self-test`) |
| `ACTIVATION.md` | wave-12 runbook | Exact swap-in steps (backup, copy, verify, restart) and rollback |

## Why the merged file exists

FINDINGS.md risk 1 (top risk): the live `force-haiku-subagents.mjs` and the
spike's `strip-tools-hook.mjs` both emit `updatedInput` on the same
`Agent|Task` matcher, with undefined merge order — either rewrite could be
silently dropped. `force-model-policy.merged.mjs` resolves this by computing
the final model (model policy) first, deriving the cognition tier from that
final model, and emitting BOTH rewrites in a single `updatedInput`.

It is a drop-in replacement candidate for
`~/.claude/hooks/force-haiku-subagents.mjs`. Activation is a deliberate,
user-decided step — follow `ACTIVATION.md` when that decision is made.
