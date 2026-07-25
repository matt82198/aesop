# Orchestrator S2 Decision Catalog

The orchestrator's "seat 2" (S2) responsibility is to perform recurring judgment calls that keep a wave running safely and coherently. This directory documents the six core decision types with their input/output contracts as JSON Schema draft-07 files.

## Core Decision Types

### 1. `rank_backlog`

**Purpose**: Intake audit findings, feature ideation, fleet-ops recommendations, and existing backlog items; produce a prioritized, scoped backlog for the current wave.

**Trigger Point**: Phase 0 (wave setup). Runs once per wave after audit lenses complete.

**Input**: 
- Audit findings from multiple lenses (security, correctness, test-integrity, architecture, UI/UX, ideation, docs, fleet-ops)
- Existing backlog items (tracker.json)
- Fleet-ops monitor recommendations
- Wave constraints (cost ceiling, max items)

**Output**:
- Verdict (ranked, completed, undetermined)
- Ranked list of items selected for this wave (per-item rank, priority, reason, estimated cost)
- Total estimated cost

**Schema**: [`rank_backlog.schema.json`](rank_backlog.schema.json)

**Example I/O** (SANITIZED):

*Input*:
```json
{
  "audit_findings": [
    {
      "lens": "correctness",
      "category": "defect",
      "description": "Worker dispatch fails when owned files include symlinks",
      "impact": "high"
    },
    {
      "lens": "ideation",
      "category": "enhancement",
      "description": "Add cost-attribution per audit lens (user insight: know what each scanner costs)",
      "impact": "medium"
    }
  ],
  "backlog_items": [
    {
      "id": "feat/x",
      "description": "Implement multi-model backend routing",
      "priority": "p1"
    }
  ],
  "wave_constraints": {
    "cost_ceiling_dollars": 50,
    "max_items": 8
  }
}
```

*Output*:
```json
{
  "verdict": "ranked",
  "confidence": 0.92,
  "evidence": [
    "conductor3/AUDIT-PRIMER.md lines 120-135: correctness lens flagged symlink defect as P0"
  ],
  "ranked_items": [
    {
      "rank": 1,
      "item_id": "fix/symlink-dispatch",
      "priority": "p0",
      "reason": "Correctness defect blocking worker dispatch",
      "estimated_cost": 8.50
    },
    {
      "rank": 2,
      "item_id": "feat/x",
      "priority": "p1",
      "reason": "High-value feature enabling multi-model routing",
      "estimated_cost": 12.00
    }
  ],
  "total_estimated_cost": 20.50
}
```

---

### 2. `adjudicate_finding`

**Purpose**: Given an audit finding (potential defect, false positive, or enhancement), render a verdict: is it real? actionable? what priority? Should it go in the backlog?

**Trigger Point**: Phase 0 (audit review). Runs per-finding when audit briefs arrive.

**Input**:
- Finding text, category, claimed severity
- Reproduction steps (if defect)
- Source audit lens and file/line
- Related findings (duplicates/blocked-by)
- Prior verdicts on similar findings (for consistency)

**Output**:
- Verdict (real_defect, false_positive, enhancement_opportunity, undetermined)
- Actionable? (yes/no)
- Recommended priority if actionable
- Suggested fix approach

**Schema**: [`adjudicate_finding.schema.json`](adjudicate_finding.schema.json)

**Example I/O** (SANITIZED):

*Input*:
```json
{
  "finding": {
    "id": "sec-2026-07-23-001",
    "description": "Secret token 'DEMO_KEY_abc' appears in test fixture, not marked as runtime-concat",
    "category": "security",
    "severity_claimed": "p1",
    "reproduction_steps": "Run: grep -r 'DEMO_KEY' tests/ — visible in test_xyz.py line 42"
  },
  "finding_source": {
    "lens": "security",
    "file": "tests/test_xyz.py",
    "line": 42
  },
  "prior_verdicts": [
    {
      "wave": 25,
      "verdict": "real",
      "reasoning": "Dummy secret without concat marker blocks push gate"
    }
  ]
}
```

*Output*:
```json
{
  "verdict": "real_defect",
  "confidence": 0.98,
  "evidence": [
    "tests/test_xyz.py lines 40-45: literal secret string ('DEMO_KEY = ...') blocks push gate; prior wave had the same pattern"
  ],
  "actionable": true,
  "priority": "p1",
  "suggested_fix_approach": "Mark the dummy secret with runtime-concat (concat at runtime to defeat push-gate scanning)"
}
```

---

### 3. `review_diff`

**Purpose**: Given a code diff from a worker agent, determine: does it correctly implement the requested change? Are there defects, oversights, or style issues? Should it be merged or sent for revision?

**Trigger Point**: Phase 1 (build) and Phase 2 (repair). After each worker completes, before merge.

**Input**:
- Unified diff (files changed, additions/deletions, patch)
- Original dispatch prompt (intent)
- Domain contract/spec the change should fulfill
- Test results (pass/fail, exit code)
- Worker model and owned files

**Output**:
- Verdict (approve, reject, needs_changes, undetermined)
- Implements intent? (yes/no)
- Defects found (count)
- Revision notes (if revision required)

**Schema**: [`review_diff.schema.json`](review_diff.schema.json)

**Example I/O** (SANITIZED):

*Input*:
```json
{
  "diff": {
    "branch": "feat/decision-catalog",
    "commit_sha": "a1b2c3d",
    "files_changed": [
      {
        "path": "driver/decisions/rank_backlog.schema.json",
        "status": "added",
        "additions": 150,
        "deletions": 0
      }
    ],
    "tests_passing": true,
    "test_exit_code": 0
  },
  "original_intent": {
    "prompt_excerpt": "Create JSON Schema for rank_backlog decision type",
    "contract_hint": "input must have audit_findings and backlog_items; output must require evidence field"
  }
}
```

*Output*:
```json
{
  "verdict": "approve",
  "confidence": 0.95,
  "evidence": [
    "Test output: schema validation passes; example I/O pairs parse correctly against schema"
  ],
  "implements_intent": true,
  "defects_found": 0
}
```

---

### 4. `synthesize_briefs`

**Purpose**: Consume multiple audit briefs (from independent lenses) and produce a consolidated context pack: unified findings list, relationships, priority ordering, and clarifications for the PM/orchestrator.

**Trigger Point**: Phase 0 (wave setup). After all audit lenses complete, before PM planning.

**Input**:
- Multiple audit briefs (security, correctness, UX, ideation, docs, etc.)
- Each brief contains findings with IDs, categories, severity
- Prior synthesis (if re-synthesizing)
- Known duplicates list

**Output**:
- Verdict (synthesized, completed, undetermined)
- Consolidated findings (deduplicated; per-finding source lenses, category, priority)

**Schema**: [`synthesize_briefs.schema.json`](synthesize_briefs.schema.json)

**Example I/O** (SANITIZED):

*Input*:
```json
{
  "briefs": [
    {
      "lens": "correctness",
      "findings": [
        {
          "id": "corr-001",
          "description": "Verification policy struct missing cost-per-tier data",
          "severity": "medium"
        }
      ]
    },
    {
      "lens": "docs",
      "findings": [
        {
          "id": "docs-042",
          "description": "README lacks example of orchestrator decision flow",
          "severity": "low"
        }
      ]
    }
  ]
}
```

*Output*:
```json
{
  "verdict": "synthesized",
  "confidence": 0.88,
  "evidence": [
    "Brief 'correctness' finding corr-001: verification policy struct missing cost-per-tier data"
  ],
  "consolidated_findings": [
    {
      "id": "consolidated-001",
      "description": "Verification policy struct missing cost-per-tier data",
      "source_lenses": ["correctness"],
      "category": "defect",
      "priority": "p2"
    }
  ]
}
```

---

### 5. `decide_repair`

**Purpose**: A wave item failed its test. Analyze the failure and test output, then decide: is it worth a repair attempt? What strategy? Should we escalate or defer?

**Trigger Point**: Phase 1 (build) and Phase 2 (repair). When an item's test fails.

**Input**:
- Failed item (slug, branch, model, owned files)
- Test output (test command, exit code, stdout/stderr)
- Failure pattern detected (assertion_error, timeout, import_error, etc.)
- Repair context (which round, repair cap, prior attempts)
- Verification policy (repair_cap, spot_check_frac)

**Output**:
- Verdict (repair, escalate, abandon, undetermined)
- Repair strategy (root_cause_analysis, incremental_fix, full_rewrite, skip_item)
- Root cause hypothesis
- Escalation reason (if not repairing)

**Schema**: [`decide_repair.schema.json`](decide_repair.schema.json)

**Example I/O** (SANITIZED):

*Input*:
```json
{
  "failed_item": {
    "slug": "feat/xy-backend",
    "branch": "feat/xy-backend",
    "model_used": "claude-haiku-4",
    "owned_files": ["driver/backend_xy.py", "tests/test_backend_xy.py"]
  },
  "test_output": {
    "test_command": "python -m pytest tests/test_backend_xy.py -v",
    "exit_code": 1,
    "failure_pattern": "assertion_error",
    "stdout": "FAILED test_backend_xy.py::test_response_format - AssertionError: expected key 'status' in response"
  },
  "repair_context": {
    "round": 1,
    "repair_cap": 2,
    "prior_rounds": []
  }
}
```

*Output*:
```json
{
  "verdict": "repair",
  "confidence": 0.87,
  "evidence": [
    "Test output: \"expected key 'status' in response\" — clear assertion error; testable with a quick fix"
  ],
  "repair_strategy": "root_cause_analysis",
  "root_cause_hypothesis": "Response format missing 'status' key; likely worker misread contract"
}
```

---

### 6. `final_catch`

**Purpose**: Pre-merge safeguard. Before shipping (merging to main), perform a last sanity check: does the item pass all gates? Are there any last-minute red flags? Should it be held or escalated?

**Trigger Point**: Phase 3 (wave close). Before merging to main, after all repairs complete.

**Input**:
- Item ready to merge (slug, branch, PR number, commit SHA, files changed)
- Verification results (test passed? secret-scan passed? CI green? branch protection?)
- Adversarial review results (if performed)
- Branch protection check (required checks passing, reviews, strict-up-to-date)
- Gate history (prior attempts)

**Output**:
- Verdict (merge, block, escalate, undetermined)
- Hold reason (if not safe)
- Escalation needed? (yes/no)

**Schema**: [`final_catch.schema.json`](final_catch.schema.json)

**Example I/O** (SANITIZED):

*Input*:
```json
{
  "item": {
    "slug": "fix/worker-dispatch-symlink",
    "branch": "fix/worker-dispatch-symlink",
    "pr_number": 999,
    "commit_sha": "f1e2d3c",
    "files_changed": 3,
    "additions": 120,
    "deletions": 45
  },
  "verification_results": {
    "test_passed": true,
    "test_exit_code": 0,
    "secret_scan_passed": true,
    "ci_status": "success",
    "branch_protection_check": {
      "required_checks_passing": true,
      "strict_up_to_date": true
    },
    "adversarial_review": {
      "completed": true,
      "defects_found": 0
    }
  }
}
```

*Output*:
```json
{
  "verdict": "merge",
  "confidence": 1.0,
  "evidence": [
    "Gate test_output: passed — all tests pass; no timeouts or flakes",
    "Gates secret_scan, ci_green, branch_protection, adversarial_review: all passed (0 defects)"
  ],
  "escalation_needed": false
}
```

---

## Schema Structure (Common to All)

Each schema file enforces a contract:

### Required Fields (all decision types)
Schema-REQUIRED (a response missing either fails validation → retry → DECISION_FAILED):
- **`verdict`** (string): one value from the decision type's verdict enum
- **`evidence`** (array of >=1 non-empty strings): citations supporting this decision

Documented but optional in responses:
- **`decision_type`** (const): e.g., "rank_backlog" (set by the driver if absent)
- **`input`** (object): documents the context fields this decision consumes (contract documentation, not an output field)
- **`confidence`** (number 0.0-1.0): how sure is this decision? Optional in schema, but the AdjudicationGate treats a missing confidence as 0.0 and escalates — challengers should always emit it

### Input Fields (varies by type)
Each decision type documents which control files or findings it consumes (e.g., `rank_backlog` reads STATE.md, AUDIT-PRIMER.md, tracker.json; `adjudicate_finding` reads a finding text + source).

### Output/Verdict Fields (varies by type)
Each decision carries type-specific optional fields alongside the verdict enum:
- `rank_backlog`: ranked_items + total_estimated_cost
- `adjudicate_finding`: actionable + priority + suggested_fix_approach
- `review_diff`: implements_intent + defects_found + revision_notes
- `synthesize_briefs`: consolidated_findings
- `decide_repair`: repair_strategy + root_cause_hypothesis + escalation_reason
- `final_catch`: hold_reason + escalation_needed

### Evidence Requirement
**All verdicts MUST include an `evidence` array with at least one citation.** Each citation should reference:
- **File**: control file path (e.g., STATE.md, AUDIT-PRIMER.md, findings.json) or source file
- **Lines**: line range or number if applicable
- **Excerpt**: quoted text supporting the verdict
- **Description**: how this evidence informs the decision

Verdicts without evidence citations do not count in the system; this enforces traceability and prevents hallucination.

---

## Sanitization Notes

Example I/O pairs in this README have been sanitized:
- No verbatim user prose or session transcripts
- No tokens, API keys, or credential-like strings
- No machine-identifying values beyond repo-relative paths
- Paraphrased examples distilled from real BUILDLOG entries and documented orchestrator behavior
- Runtime-concatenated dummy-secret-looking strings (to defeat the push gate and serve as examples)

---

## Tooling & Tests

See [`tests/test_decision_schemas.py`](../../tests/test_decision_schemas.py) for:
- Schema validation (all `.schema.json` files parse as valid JSON Schema draft-07)
- Presence check (README documents exactly the schema files present, drift gate)
- Example syntax check (fenced JSON examples in this README must parse; full schema conformance of examples is maintained by hand, not machine-enforced)

Run tests with (from the repo root):
```bash
python -m unittest tests.test_decision_schemas -v
```

---

## Next Steps

**Increment 1** (OrchestratorDriver seam): Mirror AgentDriver to create OrchestratorDriver, with a `decide(decision_type, context_pack, schema)` method. Implement backends for Claude, OpenAI-compatible, and Codex.

**Increment 2** (Shadow mode): Run all S2 decisions on both Claude and a challenger backend, zero behavior change, measure agreement rate.

**Increment 3** (Live swap): Swap one decision class (e.g., `adjudicate_finding`) to the challenger, while Claude spot-checks a sample.

**Increment 4** (Full headless): Run an entire wave with all S2 decisions on a challenger backend.

**Increment 5** (Micro-kernel formalization): Formalize the complete S2 decision interface, syscall table, and capability probing in docs/MICROKERNEL.md.

---

## References

- **Plan**: [`conductor3/plans/orchestrator-swap-microkernel.md`](../../../conductor3/plans/orchestrator-swap-microkernel.md)
- **Driver architecture**: [`driver/README.md`](../README.md)
- **Wave loop**: [`driver/wave_loop.py`](../wave_loop.py)
- **Verification policy**: [`driver/verification_policy.py`](../verification_policy.py)
