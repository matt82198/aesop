# Test Strategy

Aesop's test strategy is built on one principle: **behavioral proof over code inspection**. A test that runs the gate command and checks its exit code is worth more than an agent reading the source and declaring "looks correct."

---

## Three-Harness Architecture

Aesop runs three independent test harnesses, each covering different system layers. All three must pass before any merge.

### Shell (13 suites)

**Run:** `bash tools/run_shell_tests.sh` or `npm run test:sh`

**Covers:** Infrastructure that runs as shell scripts -- backup/recovery, watchdog daemon lifecycle, git hooks (pre-push policy), state reconciliation, daemon self-healing, wave enforcement. These test the actual scripts that run in production, not mocked equivalents.

**Discovery:** Dynamic from `tests/*.test.sh`, `tests/test_*.sh`, `tests/test-*.sh` plus `hooks/pre-push-policy.sh --test`.

### Node (24 suites)

**Run:** `npm run test:node` or `node --test --test-force-exit --test-timeout=60000 tests/*.test.mjs`

**Covers:** CLI scaffolding, config management, signal collection, drift detection, dashboard UI rendering, fleet/MCP APIs, test templating, orchestration core logic. These test the Node.js layer that powers the CLI (`npx @matt82198/aesop`) and the MCP server.

**Discovery:** Dynamic from `tests/*.test.mjs`.

### Python (181 suites)

**Run:** `npm run test:py` or `python -m unittest discover -s tests`

**Covers:** The largest surface -- API state/tracker, UI/SSE endpoints, benchmark harness, security gates (CSRF, secret scan, symlink guard, agent prompt hygiene), state store (SQLite WAL), StateAPI/WriteAPI facades, all tools (cost ceiling, halt, health score, CI workflow lint, defect escape analysis, subprocess guard, watcher linter, spec-contract validator), AgentDriver/OrchestratorDriver (Claude Code, Codex, OpenAI-compatible backends), wave engine cross-repo, agents/monitoring, config/launch, and decision schemas.

**Discovery:** Dynamic from `tests/test_*.py`.

### Totals

| Harness | Suite count | Run command |
|---------|-------------|-------------|
| Shell   | 13          | `npm run test:sh` |
| Node    | 24          | `npm run test:node` |
| Python  | 181         | `npm run test:py` |
| **All** | **218**     | `npm run test:all` |

Suite counts are gate-verified by `python tools/verify_test_suite_count.py --check` (CI blocking gate). Live inventory: `python tools/list_test_suites.py`.

---

## Adversarial Verification Layer

Tests alone are not enough when agents grade their own homework. Aesop adds an adversarial verification layer on top of the test harnesses:

**The problem:** Wave-24's all-Haiku audit reported 4 P0 findings. Independent verification found zero real -- 2 were hallucinated, 2 were severity-inflated. Agents self-grading produces false positives at a measured ~30% rate.

**The fix:** Independent agents validate findings before they count. The convergence loop (52 findings to zero across 5 rounds) used three lens families:
- **Analyst lens** -- structured review against acceptance criteria
- **Adversarial lens** -- independent agent attempts to refute the finding
- **Delta-audit lens** -- diff-based review of changes since last clean pass

A finding only counts as verified when an independent verifier (not the original reporter) confirms it survives re-run. This discipline caught ~30% false positives across the convergence loop.

---

## Testing Philosophy

### Gap-Centric, Not Coverage-Centric

Tests document actual gaps found in rounds of refactoring and audit:
- Each real finding produces a test case that reproduces the gap (failing first, TDD)
- Once fixed, the test stays as a regression guard
- No hypothetical tests; no "might fail someday" placeholders
- Flaky CI (e.g., state_store SQLite deadlocks under parallel shards) is recorded as a real gap with remediation notes, not skipped

### Behavioral Proof over Code Inspection

Nine fake-green findings were discovered during the v0.5.0 arc when agents graded their own homework by reading source code. The fix: require behavioral evidence.

- Run the exact CI gate command, not a proxy (e.g., `vite build` is not `tsc`; local green is not headless-CI green)
- Subprocess-level proof: tools that claim to write files must be tested by checking the filesystem, not by asserting the function returned `True`
- Platform-conditioned repro: a Windows-only failure is not fixed until reproduced under Windows runner conditions (8.3 short paths via FSO ShortPath), not just local-green

### Fixture Isolation

- Shell tests use `mktemp` or `$TMPDIR` with `trap` cleanup
- Python tests use `tempfile.TemporaryDirectory()` with `setUp`/`tearDown`
- No persistent side effects; all tests run independently on any branch
- Dummy secrets are runtime-concatenated (e.g., `"prefix" + "suffix"`), never literal strings, to avoid tripping the pre-push secret gate
- Tests never pollute cwd (`os.chdir` without `try/finally` restoration) or global git config

---

## CI Integration

- **GitHub Actions:** Each harness runs independently; one failure blocks merge
- **Windows CI:** 4-shard split (~3 min wall-clock, down from ~11 min serial), promoted to required check after 6 consecutive green main runs
- **Self-test mode:** Hooks and tools include `--test` flags for inline validation (`pre-push-policy.sh --test`, `tools/secret_scan.py --staged`)
- **Concurrency-safe:** Tests use file locks to prevent races in parallel CI shards

---

## See Also

- [tests/CLAUDE.md](../tests/CLAUDE.md) -- full suite map, hygiene rules, per-category details
- [bench/README.md](../bench/README.md) -- held-out benchmark design (deterministic scoring, no LLM in the grading loop)
- [ARCHITECTURE.md](./ARCHITECTURE.md) -- system diagram showing where tests fit in the wave cycle
- [INCIDENTS.md](./INCIDENTS.md) -- classified incident log (many incidents led directly to new test suites)
