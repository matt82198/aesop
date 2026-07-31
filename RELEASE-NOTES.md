# aesop 0.7.0 — Guardrails & State Consolidation

**Headline**: Comprehensive guardrail enforcement (11 linters, 8 automated gates) codifies design rules into fail-closed machinery. Multi-instance coordination MVP via lease-based SQLite enables team-scale orchestration. Test suite accountability (254 tests across 3 harnesses) verified by drift-detection gate. State consolidation complete: ReadAPI/WriteAPI facades unify all state readers/writers through one façade.

## Shipping

### 5-Bullet Summary

1. **Guardrail enforcement suite**: 11 new linters + gates (dispatch_lint, CLAUDE.md sync, workflow model pin, git-stash guard, encoding validator, commit linter, docstring checker, dead-code detector, import-cycle detector, TODO tracker, file-size linter, test-coverage gap finder) eliminate design escape routes through automated fail-closed checking.
2. **Multi-instance coordination MVP**: Lease-based SQLite claims enable multi-machine orchestration without consensus machinery; atomic check-and-insert prevents split-brain, TOCTOU race resolution via path normalization, tracker migration fixes prevent zombie resurrection.
3. **Test suite accountability**: 254 tests across 3 harnesses (25 Node + 13 Shell + 216 Python) verified by new drift-detection gate (`verify_test_suite_count.py`); test counts reconciled across README/docs/CLAUDE.md to single source of truth.
4. **State consolidation complete**: ReadAPI/WriteAPI facades ship; all state I/O routed through unified entry points. StateAPI migration ratchet prevents new direct reads; stateapi_lint enforces compliance via committed baseline.
5. **Production tooling**: Init-project scaffolder, auto-merge batch tool, cost forecasting, tracker reconciliation (zombie detection), wave-history temporal analyzer, health-score subcommand, dependency graph generator, port-fidelity validator.

### Everything Else

- Fixture-intent manifest validator (deliberate fixture breakage tracking).
- Dashboard tooling panel (guardrail/tool metrics summary).
- Benchmark results cache and display panel.
- REST API for state-query time-travel (temporal filtering, stream replay).
- Architecture decision records (ADR-1 through ADR-6).
- Encoding validation gate (Windows cp1252 trap prevention).
- Bash exec-guard validator (`bash_guard_check`).
- Multi-instance: lease-claim split-brain fix, TOCTOU resolution, tracker migration marker/completion separation, acceptanceCriteria preservation on write.
- CI hardening: workflow action env vars, BASH_SOURCE guards, Windows cp1252 encoding fix, instance_manager race fixes.
- CI gates: removed fail-open continue-on-error from cross-OS drift, verified gate runability on unknown flags, repaired Windows temp-dir exemption.
- Documentation: cross-file drift fixes, domain CLAUDE.md updates, test count corrections, multi-instance reconciliation.

---

## Prior releases

### aesop 0.6.0 — Credibility & Proof Depth

**Headline**: Hiring roadmap credibility repairs: README hero rewrite with git-verified self-build numbers, stats consistency fix (455→588 PR recount), named comparison table vs competitors, demo mode (zero API key, full dashboard). Proof depth: non-Claude backend live recording (GPT-4o-mini end-to-end), crash-only whitepaper, "How I Built Aesop" narrative, raw cost dataset. EventStore connection pooling + Windows cleanup. Hosted dashboard on GitHub Pages with embedded demo data.

### aesop 0.4.0 — AI Micro-Kernel: Two Swappable Seats

**Headline**: Unified two-seat configuration enables swapping both worker AND orchestrator models (Claude, Codex, OpenAI-compatible backends) without code changes. Live orchestrator seat-swap gate proven in gate-2 audit. Hardened over two audit passes and a six-round refinement loop before release.

## Shipping

- **Two-seat unified config (HS-1)**: New `seats` block swaps worker + orchestrator models from config alone. No-op default: existing installs unchanged.
- **Live orchestrator seat-swap (HS-2)**: Final-catch gate enables mid-wave model swap; crash-only degradation on failure (quarantine + incident log).
- **Microkernel architecture docs (HS-3)**: `docs/MICROKERNEL.md` explains the proof-of-concept seam, multi-model verification bounds, 60-second quickstart for adopters.
- **Scaffolding completeness** (`npx aesop init`): driver/ directory now scaffolds on first run; --force idempotent re-scaffold; child process timeout protection.
- **Hardening (two audit passes + six-round refine loop)**: IPv6 SSRF + DNS validation, API key environment allowlist, worker model field validation, wave execution timeouts, Windows CI shard concurrency cap, path redaction completeness, JSON-boundary fail-closed gates.

**Single-instance proven**; multi-instance coordination scheduled.

---

## Prior releases

### aesop 0.3.1 — Multi-core waves

> 0.3.1 ships as the release tag for the 0.3.0 milestone (a defective v0.3.0 tag was burned by an automation error: an agent created an empty release at the wrong commit before the release PR merged; content shipped unchanged as 0.3.1).

0.2.0 shipped the seams; 0.3.0 ships the proof: **a non-Claude model core ran a full
supervised wave (single-item pilot) — intake → build → verify → ship — through the same engine, with the
same gates**, and the release was preceded by a fresh adversarial hardening loop that
exited clean.

### Headline: the wave engine is core-agnostic (WS3)

- **wave_scheduler.py (WS3a pilot)**: deterministic single-cycle orchestration — tracker
  intake with fail-closed validation (empty/missing ownership rejected; paths normalized
  platform-independently; absolute/traversal paths rejected), HALT + cost-ceiling gates
  that abort on module failure (never fail open), manifest via the driver bridge, one
  run_wave call, stop-before-merge Report. Atomic tracker claim (mkstemp + os.replace,
  content-hash conflict abort) prevents double-dispatch across runs.
- **Gate-1 handoff kit**: `--driver claude|codex` CLI injection; per-item Report
  observability `{slug, backend, tier, verified, testExit}`; documented orchestrator
  REPORT-CONTRACT; offline FakeTransport codex route proven in CI.
- **LIVE PROOF (gate 1, DONE)**: a supervised codex wave (gpt-4o-mini via CodexDriver)
  took a real backlog item (`wave_templates validate --json`), implemented it, passed the
  real 25-test suite (testExit 0, tier 2), and the ship phase committed and pushed —
  human-reviewed and merged as PR #325. Two supervised corrections were applied (unicode
  glyphs ASCII-coerced by full-file replacement), and four scheduler Report-plumbing
  defects the live run exposed were fixed with real-shape regression tests.
- Survived two adversarial review rounds pre-merge (12 verified defects fixed, including
  a dead-code tracker write and a symlink TOCTOU) — see the hardening section.

### Measured, not asserted

- **Live structured-output accuracy**: Single run, N=32 curated tasks — gpt-4o-mini **32/32 (100%)** composite
  (valid-JSON / schema-exact / ownership-respect) under the driver-faithful payload
  (bench/results/accuracy-live-2026-07-22.json).
  Supports the probe's conservative 0.92 assertion; not a transfer claim. The path to
  this number (33% → 0% → 4% → 100%, each step a real harness defect fixed and
  regression-guarded) is documented in the bench history.
- **Frontier discrimination slice**: 20 hard judgment tasks with per-task discrimination
  rationales, deterministic scoring, live runs cost-gated behind `--confirm-spend`
  (exit 2 USER-GATED otherwise).
- **Transcript-sampled judgment set**: N=150 sanitized tasks from real fleet transcripts.
- **Cross-OS drift measurement**: tools/crossos_drift.py quantifies windows-vs-ubuntu CI
  divergence from real run history. Baseline at introduction: windows 0/6 where present;
  after the parity campaign (env-tunable child timeouts, eod_sweep repo-delimiter root
  cause, 8.3 containment fixes) the windows job went GREEN on main — the promote-to-
  required streak is counting from run 29955999466.

### State consolidation (WS4)

- **ReadAPI facade** (state_store/read_api.py): one read seam over tracker / orchestrator
  status / heartbeats / ledger — delegates to existing parsers, never forks logic.
- **WriteAPI seam** (state_store/write_api.py): event-append + atomic projection with
  conflict detection; first two tracker write ops behind one facade (caller migration
  is the 0.4 track).
- **StateAPI ratchet in CI**: stateapi_lint gate live — new direct state reads outside
  the facade fail CI against a committed, posix-normalized baseline (currently 33
  entries: a visible migration worklist that can only shrink).
- **Agent lifecycle events**: dispatched/working/done/stalled event types + projection
  with transition history, feeding the Activity view live.

### Cost: observed, projected, bounded, unified

- **cost_projection.py**: burn-rate from a ledger window, end-of-wave projection,
  idempotent 70%/90% ceiling alerts (honest fired_alert semantics under partial failure).
- **One window contract**: projection and ceiling share a single window helper — they can
  no longer disagree about "spent".
- **Cost Analytics dashboard panel**: spend per wave, per-model split with the all-Opus
  counterfactual, burn vs ceiling — with honest DATA-UNAVAILABLE states and a Playwright
  proof (verify_cost_panel.py).

### Operability

- **`aesop reproduce`**: offline verification suite from a clean clone/install; doctor
  failure classification is exact-match (a real missing dependency can no longer be
  mistaken for a pre-init condition).
- **docs/PORTING.md**: step-by-step adopter port with the 10 likeliest failure modes and
  recoveries, sourced from this repo's real incident history.
- **Windows CI job**: node+python on windows-latest; parity fixes for
  file-locking, SSE disconnect noise (WinError 10053/10054 as normal lifecycle), and
  eod_sweep failing CLOSED on git errors (the 8.3 short-path fail-open root cause). Windows was promoted to a required check on 2026-07-22 after 6 consecutive green main runs.
- **Monitor stall detection**: stall_check.py active-task predicate + advisory recovery
  emission, surfaced as a monitor signal.
- **Wave preflight**: backlog validation flags (missing ownership, stale refs, overlaps,
  ledger-aggregate retry rate with DATA-UNAVAILABLE honesty).

### Security

- **Redaction hardening**: URL-credential patterns are scheme-agnostic, consume
  embedded-@ userinfo to the last @, handle IPv6 hosts, and refuse to over-redact
  letterless ratios; over-redaction is the documented failure direction.
- **Scanner exemption, done in the open**: connection_string stays fatal everywhere;
  ONE file (the redaction-pattern source) downgrades to a reported, never-silent
  ALLOWED-REDACTION-SOURCE — a user-approved, single-rule, test-pinned exemption.
  Notable property it surfaced: the pre-push hook runs main's scanner, so a branch
  cannot weaken its own gate.
- **Ship-phase hygiene**: git-add failures unstage their residue; per-repo ship errors
  carry stderr detail in the Report.

### Hardening (the release gate)

- 0.3.0's release condition was a full /refinesystem loop: expert + adversarial lens
  fleets with regression re-verification, every P1 deterministically verified by the
  orchestrator before any fix was paid for. This cycle's honest ledger:
  - Round 1 (7 lenses): ~14 verified defects fixed pre-merge (incl. a symlink TOCTOU,
    a dead-code double-dispatch guard, and 4 redaction under/over-redaction defects);
    5 findings refuted with evidence.
  - Round 2 (6 lenses, full): 12 verified defects fixed (incl. codex broken-by-default,
    write_api OCC contract lie, 16-site dead-client 500 discipline, the eod_sweep ':'
    delimiter root cause) + a LIVE incident caught by the regression lens's README
    canary (fixture escape into the working tree — contained, guarded, two long-lived
    identity polluters eliminated); 4 severities corrected downward.
  - Round 3 (3 lenses): 3 small findings fixed; the new identity tripwire caught a
    polluter predating the entire cycle (hook self-test rewriting git identity on every
    run — active for months, invisible until instrumented).
  - Round 4: exit verification (fix re-attack + tripwires) — clean.
  Net: ~30 verified defects fixed across 4 rounds, ~10 lens claims refuted with
  evidence, 3 integration trains + 2 solo ships, ending at a fully-green main
  including windows for the first time in the repo's history.
- Test-infrastructure classes fixed this cycle: zero-collection test classes (a gate now
  fails baseless Test* classes), scaffold-test load-sensitivity (shared fixtures,
  env-tunable child timeouts), local-server timeout starvation, stdin-inheritance hangs.

### Breaking / behavior changes

- Mixed git-ship manifests (some items with explicit `repo`, some without) are rejected
  at preflight; pure-legacy and fully-explicit manifests are unchanged.
- `npm run test:sh` no longer invokes `reconstitute.sh --test` directly (its wrapper suite
  exercises `--test` internally); the pre-push hook's own self-test remains an explicit
  invocation because its wrapper suite does not run it.
- NEW CLI surfaces: `frontier_slice.py` exits 2 (USER-GATED) without `--confirm-spend`;
  `cost_ceiling.py` gains `--window` (backward-compatible); `stall_check.py` gains
  `--active-from`, `--emit-recovery`, `--recovery-dir`; `wave_scheduler.py` gains
  `--driver claude|codex`; `test_battery.py` added (parallel local union battery).
- eod_sweep: repo list delimiters are now os.pathsep (';' on Windows); nonexistent or
  non-git explicitly-listed repos are AT-RISK findings (exit 1), never silent skips.

### Honest residuals

- Windows was promoted to a required check on 2026-07-22 after 6 consecutive green main runs.
- StateAPI baseline: 33 direct-read sites remain; burn-down is the 0.4 track alongside
  caller migration to WriteAPI and validation-ownership consolidation.
- Codex live proof is one supervised wave on one small item — the unsupervised loop,
  failure-recovery ownership (WS3b), and multi-item non-Claude waves remain future work.
- Benchmark discrimination slice is authored but not yet live-run (spend-gated).
