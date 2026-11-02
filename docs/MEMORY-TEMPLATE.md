# Memory Index Template

This is the canonical index of team facts. Each fact is **one file, one truth**.

## Format

### Index line
```
- [Title](file.md) — [1-line hook]
```

### Fact frontmatter
Every memory file opens with:
```
---
name: [Fact name]
description: [One sentence]
type: [user|feedback|project|reference]
---
```

**Types**: `user` (who you are), `feedback` (decisions/learnings), `project` (repo-specific), `reference` (how-tos/patterns).

## Examples

### Memory file: `~/.claude/memory/dispatch-principles.md`

```
---
name: Dispatch cost lever
description: Why Haiku subagents are the single most important cost optimization.
type: reference
---

Subagents are ALWAYS Haiku (the lowest-priced tier). This is the cost lever.
Six to eight Haiku agents in parallel cost less than an all-Opus fleet...
[Full context here]
```

### Memory file: `~/.claude/memory/team-goals.md`

```
---
name: 2026 Q2 goals
description: Ship [feature] by end of June, maintain <200ms latency on [metric].
type: feedback
---

Primary goal: [...]
Secondary goal: [...]
Constraints: [...]
```

### Index entry (in MEMORY.md)

```
- [Dispatch principles](memory/dispatch-principles.md) — Haiku is the lowest-priced tier, default subagent tier
- [Team goals](memory/team-goals.md) — Q2 2026 roadmap and constraints
```

Create new facts by adding a file and an index line. The orchestrator reads the index at session start.
