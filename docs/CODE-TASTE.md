# Code Taste Walkthrough
<!-- secretscan: allow-pattern-docs -->

Design rationale for key architectural decisions in aesop, with annotated examples.

## Overview

This document walks through the **why** behind three load-bearing pieces of aesop's architecture:

1. **AgentDriver** — Backend-portability seam for the wave loop
2. **secret_scan.py** — Security gate that catches credentials before they leak
3. **Verification gates** — Hardening layer ensuring worker output integrity

Each section explains the problem, the chosen design, and trade-offs against alternatives.

---

## 1. AgentDriver: Backend Portability Seam

### The Problem

Aesop's orchestration core (the wave loop) must run on multiple backends:
- Claude Code (reference implementation)
- Codex (OpenAI)
- Open-model runners (Ollama, LLaMA)
- Future providers (Azure, etc.)

If the wave loop called backend-specific APIs directly (`agent()`, `parallel()`, Bash tool, cost tracking), porting to a new backend would mean rewriting the entire orchestration logic.

### The Solution: Abstract Interface

Instead, **AgentDriver** (driver/agent_driver.py) is an ABC with **five operations** that encapsulate EVERYTHING the wave loop needs from any backend:

```python
class AgentDriver(ABC):
    def probe_capabilities() -> DriverCapabilities:
        """What can this backend do? Parallel? File access? Cost tracking?"""

    def dispatch_worker(request) -> WorkerResult:
        """Spawn ONE isolated worker."""

    def worker_status(worker_id) -> WorkerStatus:
        """Is the worker still alive?"""

    def run_command(cmd, cwd) -> CommandResult:
        """Run orchestrator-side verification/tests."""

    def resolve_model(role) -> str:
        """Map abstract role to concrete model ID."""
```

### The Design Trade-offs

**Why 5 operations, not 10 or 2?**

- **Too few**: A 2-operation interface (just dispatch + status) forces backends to shoehorn capabilities into generic "context" objects. Verifying a result becomes blind guesswork.
- **Too many**: Each operation added doubles the test surface for every backend adapter. Cost of porting explodes.
- **Five** balances completeness (orchestrator has all the hooks it needs) against maintainability (new backends stay tractable).

**Why report capabilities at all?**

A backend that claims parallel dispatch but doesn't deliver it corrupts every downstream verification decision. Instead:

```python
@dataclass
class DriverCapabilities:
    parallel_dispatch: bool           # Can backend spawn N workers concurrently?
    worker_filesystem_access: bool    # Can worker read/write files itself?
    tool_use_accuracy: float          # Honest success rate [0.0, 1.0]
    recommended_verification_tier: int  # 1 (light) ... 4 (exhaustive)
```

If a backend is **weaker** (lower accuracy), it RAISES the verification tier, not lowers it. Cheap backends are MORE expensive to verify, not less.

**Why separate `dispatch_worker` from `run_command`?**

Workers are isolated speculative units (may read/write files, run shell commands). Orchestrator commands (running tests, git operations, verification) are trusted execution on the main thread.

A backend like Codex might sandbox workers heavily but let the orchestrator run commands natively. Another backend might run both through the same LLM. The split allows each backend to report honest capabilities and the orchestrator to adapt.

### Concrete Example: Claude Code vs. Codex

**Claude Code adapter** (reference implementation):
- `dispatch_worker()` spawns an Agent with Workflow tools (Read, Write, Bash)
- Reports `worker_filesystem_access=True, worker_shell_access=True` (Claude Code provides these natively)
- Reports `tool_use_accuracy=0.97` (empirically measured)
- Recommends `verification_tier=2` (light spot-check sufficient)

**Codex adapter** (honest stub):
- `dispatch_worker()` sends LLM the prompt + file contents as text; parses JSON response
- Reports `worker_filesystem_access=False, worker_shell_access=False` (Codex has no tools)
- Reports `tool_use_accuracy=0.78` (lower, because parsing free-text JSON is flaky)
- Recommends `verification_tier=4` (exhaustive validation needed)

Same wave loop, two backends, two different verification burdens — *automatic*, *honest*, *auditable*.

### Invariant: Single Interface, Never Direct Calls

Every call in the wave loop goes through AgentDriver. The orchestrator never calls:
- `agent()` directly
- Claude Code's `parallel()` directly
- Bash tool directly
- Budget tracking directly

This invariant is enforced by design: AgentDriver is the ONLY public interface the orchestration core imports.

---

## 2. secret_scan.py: Defense-in-Depth Security Gate

### The Problem

Developers push credentials to git every day. Static scanning catches most (hardcoded API keys, PEM headers), but false positives (test fixtures, example configs, legitimate env-var assignments) need human review. The gate must be:

1. **Accurate**: Minimize false positives so reviewers don't fatigue and ignore real findings
2. **Pragmatic**: Allow legitimate patterns (test fixtures, documentation) without weakening the gate
3. **Honest**: Never silently permit a real credential; fail-closed is non-negotiable

### The Solution: Layered Pattern Matching + Narrowly-Scoped Pragma

The scanner uses **two layers**:

1. **Fatal patterns** (always block, no exceptions):
   - PEM private key headers
   - AWS access key format (AKIA prefix)
   - GitHub token prefixes (ghp_, gho_, etc.)
   - OpenAI/Anthropic key format (sk- prefix)
   - Connection strings with credentials

2. **Doc-shaped patterns** (allow-pragmaed, but documented):
   - Generic assignment patterns: `password = "..."`
   - Environment variable access: `os.getenv('SECRET')`

These patterns are high false-positive rate in test files, so they can be allowed by pragma. Fatal patterns are high-precision (PEM headers, AWS key format) and are NEVER pragmaed.

**Why separate fatal from doc-shaped?**

- **Fatal patterns** have high precision (PEM headers, AWS key format) — zero false positives
- **Doc-shaped patterns** are heuristic (any `password = "..."` assignment looks suspicious) — high false positive rate in test files

The pragma (`secretscan: allow-pattern-docs`) appears in diffs and is reviewable; it says "this file has legitimate doc-shaped patterns." But fatal patterns are ALWAYS blocked, even if the pragma is present.

### Concrete Example: Test Fixture

```python
# tests/fixtures/credentials_test.py
# secretscan: allow-pattern-docs

class TestAuthEndpoint:
    FAKE_API_KEY = "test_key_abcdef123456789"  # doc-shaped, allowed by pragma
    # WRONG: real AWS key would STILL be caught and blocked:
    # AWS_KEY would match AWS_ACCESS_KEY_ID pattern <- fatal, pragma doesn't save this
```

### Invariant: Self-Scan Must Pass

`secret_scan.py` is its own test data. It must scan CLEAN with NO pragma. Any pattern literal that would match its own regex is runtime-assembled from fragments:

```python
# In secret_scan.py itself:
"pem_private_key": (r"-----BEGIN .* " + "PRIVATE" + " KEY-----", ...)
#                        ^                                 ^
#                        Never contiguous in source
```

If someone accidentally adds a literal PEM header or credential to the scanner code, the gate catches it.

### Trade-off: Pragma Scope

**Alternative**: One global pragma disables all checks for a file.
**Chosen**: Pragma disables ONLY doc-shaped rules, never fatal patterns.

**Why?** If developers can opt-out of all checks, the gate becomes advisory. Narrowing pragma scope keeps the gate fail-closed while admitting that test data legitimately contains placeholder secrets.

---

## 3. Verification Gates: Quality Assurance Layer

### The Problem

Workers (LLM agents dispatched by the orchestrator) produce code that may be:
- **Syntactically invalid** (JSON parsing fails, Python won't compile)
- **Semantically wrong** (passes schema but logic is broken)
- **Integrity violations** (wrote to files outside owned_files, git state corruption)

The orchestrator must catch these BEFORE they merge to main.

### The Solution: Tiered Verification

Based on the backend's reported accuracy (`tool_use_accuracy` from `probe_capabilities()`), the orchestrator runs different verification strategies:

```
tool_use_accuracy >= 0.95  =>  Tier 1 (light spot-check)
                            - Compile/syntax check only
                            - Spot-check 10% of outputs

tool_use_accuracy 0.85-0.95  =>  Tier 2 (moderate)
                            - Compile + run unit tests
                            - Spot-check 30% of outputs

tool_use_accuracy 0.75-0.85  =>  Tier 3 (thorough)
                            - Compile + full test suite
                            - Spot-check 50% of outputs

tool_use_accuracy < 0.75   =>  Tier 4 (exhaustive)
                            - Compile + full tests + linting
                            - Verify 100% of outputs
                            - Mandatory human review on fixes
```

### Concrete Example: Codex Backend

If a backend reports `tool_use_accuracy=0.78`, the orchestrator automatically enforces Tier 4:

1. **Every output** runs through linting/type-checking
2. **All tests** execute (not a sample)
3. A **human in the loop** must approve fixes before merge

This is NOT a punishment — it's an honest adaptation. The cheaper backend gets more scrutiny because it needs it.

### Trade-off: Accuracy Must Be Honest

A backend that inflates its accuracy (reports 0.95 when it's really 0.80) will:
- Get Tier 1 verification (light spot-check)
- Produce broken code that merges
- Corrupt the repository

The solution: **Backend honesty is non-negotiable**. Tier assignments are based on **measured** accuracy, not reported capability. After a backend's first wave:

```python
measured_accuracy = count_first_pass_results / total_results
if measured_accuracy < reported_accuracy - 0.05:
    alert("Backend misrepresented accuracy")
    force_tier_increase()
```

### Why Gates, Not Filters?

**Alternative**: Automatically fix worker output (reformat JSON, add missing imports).
**Chosen**: Gate + reject, with detailed error for human fix.

**Why?** If the orchestrator silently "fixes" broken code, it masks deeper problems:
- Is the backend really understanding the task?
- Are prompts ambiguous?
- Is the verification tier too low?

Rejecting with clarity (e.g., "JSON invalid: unexpected field 'extra_key'") forces the loop to either rephrase the prompt or increase verification — both improve the system.

---

## Summary: Design Principles

1. **Portability through abstraction** (AgentDriver): ONE narrow interface handles multiple backends. Backends adapt to the interface, not vice versa.

2. **Security through layered scanning**: Separate fatal patterns (always block) from doc-shaped (pragma-able). Keep the gate fail-closed.

3. **Quality through honest capability reporting**: Weaker backends get MORE verification, not less. Accuracy is measured, not assumed. Honesty is non-negotiable.

4. **Clarity over silence**: Reject with detail, don't silently fix. Let humans decide the next move.
