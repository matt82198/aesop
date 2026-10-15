# Audit backlog — refinement loop (live)

> **OBSOLETE (2026-07-17, wave-rc.2):** this file froze at wave-8 and does not track waves 9+.
> The live backlog is the tracker (`state/tracker.json`, dashboard Work view) plus the ranked
> wave plans under `~/conductor3/plans/`. Kept for history only — do not dispatch from it.

Durable handoff for the continuous refinement loop: survives terminal kills / model
switches. A resuming session (Fable) reads STATE.md → this file → dispatches one Haiku
per unclaimed item on its own branch (branch-per-item, worktree isolation), TDD-first,
ACCEPTANCE = the test gate; flip the box per landed PR.

**Status legend:** ⬜ unclaimed · 🔵 dispatched/in-flight · ✅ merged to main · ⏸ needs user call

---

## Reconciliation note (wave-25 checkpoint, 2026-07-16)

**As of wave-25 (commit 53212d9, 2026-07-16), the per-item checkboxes for waves 6–8 are STALE
and unreliable.** Many items were shipped across waves 9–24 without checkbox flips. The
**authoritative ledger is now `~/conductor3/AUDIT-PRIMER.md`** (rolling audit baseline, delta
audits per wave, full audits only on trigger conditions). Treat this file's older ⬜ boxes as
historical context, not open work. Wave-25 findings are listed at the end of this file.

---

## Cleared history (collapsed)

- **Waves 1–4 (2026-07-12): 26/26 items ✅** — 8 P0 security/correctness, all P1, all P2,
  both user decisions (domain-CLAUDE.md collapse; extended_signals gate). Merged to main
  in PR #16 (`f259c4f`). Full itemized list preserved in git history at `cc708c9`.
- **Wave 5 quickstart docs ✅** — README one-command scaffold primary (PR #18).

## P0 — correctness / security (wave 5, from five-lens re-audit)

- ✅ **[bash] NUL protocol destroyed by `$()` capture** — watchdog-repos.json fields corrupted
  in the shipped path; test must drive the REAL loop. Branch fix/backup-fleet-nul-protocol
  (+ control-char json_escape, portable date).
- ✅ **[bash] release_lock lacked ownership check** — slow holder deleted reclaimer's live lock;
  + stale-test fixture fix + array CYCLE_CMD. PR #23.
- ✅ **[js] emitProposal never takes the PROPOSALS lock + AESOP_MONITOR_FORCE=0 bypasses the
  heartbeat gate** — branch fix/monitor-proposals-races (+ stale-lock reclaim, EPERM-safe renames).
- ✅ **[meta] CI never executes the shell/python suites** — bash -n only; defective tests shipped
  unnoticed. Branch fix/ci-run-all-suites.
- ✅ **[sec] /submit CSRF — drive-by write into orchestrator inbox** — origin check + session
  token. PR #21.
- ⬜ **[sec] Scanner size/binary skip bypassed ALL rules** — >1MB or NUL-byte file with real key
  scanned CLEAN; now bounded fatal-rule scan + SKIPPED-* notes. PR #22. **REOPENED 2026-10-14
  (audit 2026-10-14-r1, F-02, confirmed by refuter):** the PR #22 residual is still open. A single
  NUL byte, a UTF-16 BOM, or a file past 2 MiB downgraded the file to FATAL-only (latin-1) or
  skipped its tail, so generic and UTF-16 secrets passed CLEAN. Evidence: `python -I
  tools/secret_scan.py` on a NUL-prefixed `password = "..."` file, a UTF-16LE (BOM) `ghp_` token,
  a BOM-less UTF-16LE `DB_PASSWORD=` line, and a token after 3 MiB all returned CLEAN rc=0 before
  the fix. Fix lane fix/secret-scan-blind-spots: BOM-decoded UTF-16 and NUL-tolerant text get every
  rule; BOM-less UTF-16 is scanned at both alignments; over-cap (16 MiB) files fail closed, never
  skipped.
- ✅ **[sec] reconstitute validates URL but not TARGET path** — arbitrary-path repo planting;
  branch fix/reconstitute-target-validation (+ real-script e2e, legacy space fallback).

## P1 — hardening (wave 5)

- ✅ **[sec+arch+bash] Audit-log hardening bundle** — write lock around read-hash+append,
  tail-truncation anchor (sidecar tail hash + seq), tty/newline-safe stdin, external Test 6
  drives the real function, control-char escapes, sha256sum fallback. Branch fix/audit-log-hardening.
- ✅ **[arch] Config trim deleted live key cardinal_rules.subagent_model** — restored + config/doc
  drift tripwire test. PR #19.
- ✅ **[js] dash-extra dir-mtime pruning hid ACTIVE agents ("fleet idle" bug) + hardcoded state
  path** — file-level activity now decides; state_root honored. PR #20.
- ✅ **[js] cli targetDir parser hijacked flag values (--repos path scaffolded INTO) + symlinked
  .git/hooks escape** — single-pass parse + lstat guard. PR #24.

## Features (user-requested)

- ✅ **[feat] Agents panel rewrite** — clickable rows: status, runtime, tokens used, task label,
  full dispatch prompt (parsed from agent-*.jsonl, XSS-safe). Branch feat/dash-agents-panel.

## Cross-repo ports (fleet machinery)

- ✅ **[port] ~/scripts/secret_scan.py: size/binary skip fix** (same hole as PR #22; pragma-scoping
  port already landed as claude-scripts 314cb25).
- ✅ **[port] conductor3 run-watchdog: release-lock ownership check** onto branch
  fix/watchdog-atomic-lock (inherits PR #23's P0; branch must not merge without it).

## Wave 5b — UI realtime (user-reported, in flight)

- ✅ **[feat] Realtime SSE dashboard rebuild** — polling full re-render replaced with /events
  SSE push + keyed in-place DOM patching; clicks/expansion survive; playwright-proven in a
  real browser (console-error-free, live update without reload). PR #35 (`491f4af`).
- ✅ **[test] Post-merge monitor test reconcile** — heartbeat-guard fixture + stale FORCE test
  names; revert-proof assertions. PR #32.

---

# Wave 6 — audit #2 (seven-lens: +frontend-engineer +design-analyst)

Deduped from 40 raw findings across 7 lenses → **26 unique items**. Priority list built
BEFORE any WIP (per standing order). Branch-per-item; UI items → frontend specialist +
playwright acceptance gate. Overlaps folded (annotated `[lenses: …]`). All HIGH/P0 items
below were empirically reproduced by the reporting lens, not just read.

## P0 — correctness / security (reproduced exploits; do first, 2026-07-12 all merged)

- ✅ **[js+bash] Lock release has no ownership check (3 files)** — `releaseLock()` in
  `tools/proposals.mjs:87-95` + `monitor/collect-signals.mjs:603-611` and `release_audit_lock`
  in `hooks/pre-push-policy.sh:53-57` all `rm -rf` unconditionally; a slow-but-alive holder
  reclaimed as stale deletes the reclaimer's LIVE lock → mutual exclusion broken, concurrent
  writers into PROPOSALS.md/SIGNALS.json/audit-log. Same class PR #23 fixed in run-watchdog.sh.
  [lenses: js#1 P0, shell#B]. PR #37.
- ✅ **[sec] Path traversal / arbitrary file read via `GET /agent?id=`** — id spliced unescaped
  into glob `**/{id}*.output`; no `..`/metachar reject, no `is_relative_to` check, no token.
  PoC: `id=../outside_secret/leaked` → 200 + file content; `id=*` enumerates every transcript.
  `ui/serve.py:524-601,1474-1501`. [lenses: security#1]. PR #38.
- ✅ **[bash+sec] reconstitute.sh validate_target symlink/junction-blind** — logical `cd+pwd`
  (no `-P`) → junction inside fleet root escapes containment; real `git clone` landed OUTSIDE
  fleet root (defeats PR #27). SAME function also false-REJECTS a legit target when its parent
  dir doesn't exist yet. `tools/reconstitute.sh:111-124`. [lenses: shell#A HIGH + shell#F MED].
  PR #40.
- ✅ **[sec] bin/cli.js scaffold: symlinked `.git` escapes hooks guard** — PR #24 guarded
  `.git/hooks` + `pre-push` but not `.git` itself; symlinked `.git` in a shared starter folder
  → hook written OUTSIDE targetDir (PoC write-through). `bin/cli.js:75-97,243-267`.
  [lenses: security#3]. PR #39.
- ✅ **[test] test-run-watchdog.sh not hermetic — P0 regression test asserts nothing** — runs
  against the REAL checkout (races the live daemon); Test 3 (guards the PR #23 lock-ownership
  P0) builds its decoy lock at an unused tmp path, so staleness-aging is a silent no-op and
  every hard assert degrades to a warning. `tests/test-run-watchdog.sh:8-9,171-174,205`.
  [lenses: shell#E HIGH]. PR #41.
- ✅ **[ui] /submit inbox write encoding corruption breaks INBOX pipeline (Windows)** —
  header `write_text()` (no `encoding=`) → cp1252 em-dash `0x97`, appends use utf-8 → file not
  valid UTF-8; strict-utf-8 readers throw. Breaks the "orchestrator reads INBOX each turn" model
  on this exact OS. Repro'd end-to-end. `ui/serve.py:1591-1598`. [lenses: frontend#1 P0].
  PR #36.

## CI-repair wave (infrastructure, 2026-07-12 all merged)

**Root cause**: CI had never executed any test/scan suite (bash -n fast-failed on .mjs files
every run). Fixing the gate exposed + fixed 5 pre-existing Linux-only defects. Main push-CI
now fully green (all 8 test/scan steps).

- ✅ **[ci] bash -n → node --check for .mjs syntax step** — syntax gate now accurate. PR #42 (folded into #45).
- ✅ **[ci] node --test hang-proof** — job timeout-minutes, --test-force-exit/--test-timeout,
  per-spawn timeouts; fixed listener-attach race in proposals.test.mjs concurrent-race test. PR #43 (folded into #45).
- ✅ **[shell] Suite green on Linux** — pre-push-policy.sh sourceable (BASH_SOURCE guard) + plain
  source in test; fixed scaffold test symlink truncation; repo-local git identity in reconstitute
  tests. PR #44 (folded into #45).
- ✅ **[py] secret_scan.py skip __pycache__/.pyc** — compiled artifacts no longer scanned. PR #45.
- ✅ **[shell] test_pre_push_policy.sh branch-policy test isolation** — isolated from ambient git HEAD
  so push-on-main CI passes. PR #46.

**Follow-ups (open for wave 7):**
- 🔵 **[hardening] proposals.mjs acquireLock fail-open** — proceeds unlocked after ~500ms under real
  lock contention; data-loss candidate.
- 📝 **[meta] CI-never-ran root cause (noted for forensics)** — bash -n only; CI gate never executed
  any suite for the repo's life. Fixed in this wave; now documented in landing log.

## P1 — hardening / real bugs

- ⬜ **[sec+arch] `/events` resource exhaustion** — unbounded per-client `queue.Queue()` (no
  maxsize), no connection cap, no Origin/Referer check; PoC 25 conns→28 threads, 50k events→
  ~200MB for one stalled client. `ui/serve.py:663-698,1509-1561,1609-1630`.
  [lenses: security#2 HIGH + architect#2 — DUPLICATE, high confidence]. ACC: conn cap→503,
  bounded queue drop-oldest, write timeout; N+1-rejected + flood-capped tests.
- ⬜ **[perf] SSE `data` section rebroadcasts every collector tick** — embedded `age` counter
  ticks every second, defeating the change-hash gate; every client gets ~1 snapshot/s
  regardless of real change (root cause amplifying the exhaustion above).
  `ui/serve.py:339-341,372-374,626-635,689-698`. [lenses: architect#1]. ACC: exclude age (or
  bucket it) from the hash; freeze-inputs-3-ticks → ≤1 `data` emit test.
- ⬜ **[bash+arch] verify_audit_log reads log+sidecar without the write lock** — false-positive
  TRUNCATION vs an in-flight two-write appender. `hooks/pre-push-policy.sh:93-167,681-685`.
  [lenses: architect#3 + shell#D — DUPLICATE]. ACC: acquire lock in --verify-audit-log;
  concurrent write-vs-verify test asserts no false report.
- ⬜ **[bash] sha256sum fallback covers only 1 of 5 call sites** — lines 121,155,269,306
  hardcode `sha256sum`, errors swallowed `2>/dev/null` → empty hash on shasum-only hosts
  (macOS/BSD) → spurious chain-broken/truncation. `hooks/pre-push-policy.sh`. [lenses: shell#C].
  ACC: single hash_bin helper across all 5 sites + masked-sha256sum test.
- ⬜ **[js] dash-extra.mjs tokensUsed undercounts >60% on long transcripts** — 50+50 line cap
  sums only the sampled subset (45000 true → 14700 reported). `dash/dash-extra.mjs:87-94,127-131`.
  [lenses: js#2 P1]. ACC: full-file token scan (cap only prompt/label extraction) + exact-total
  test — AND fix the tautological guard `tests/dash-agents-panel.test.mjs:304` [js#5] in the same PR.
- ⬜ **[ui] dash-extra.mjs hard 8-agent cap silently truncates + header misreports count** —
  `.slice(0,8)` hides extra agents AND "Fleet Agents (N active)" shows 8 not the true total
  (15 active→8) — exactly at the burst scale that matters. `dash/dash-extra.mjs:187-197`,
  `ui/serve.py:379-419,1035-1039`. [lenses: frontend#4 P1]. ACC: true total count or "+N more".
- ⬜ **[ui] Malformed SSE payload → uncaught JSON.parse throw, section stalls silently** — no
  guard on the three listeners; a mangled frame drops that tick with zero user signal.
  `ui/serve.py:1347-1359`. [lenses: frontend#2 P1 + design#7 — merge with a page-wide staleness
  banner]. ACC: try/catch + `#connection-degraded` indicator; bad-frame dispatch test.
- ⬜ **[arch] test_reconstitute_fixes.sh wired into nothing** — the P0 target-path-validation
  regression suite (PR #27) is absent from `package.json` test:sh, test:all, and ci.yml — dead
  test guarding a security fix. `package.json:53`, `.github/workflows/ci.yml:40-50`.
  [lenses: architect#4]. ACC: add to test:sh; regress validate_target → suite fails.
- ⬜ **[docs] Root CLAUDE.md domain map omits `ui/` entirely** — the largest, most
  concurrency/security-sensitive new surface has no domain-map line and no contract section
  (only a bare setup command). `CLAUDE.md:5-14,273`. [lenses: architect#5]. ACC: `ui/` map line
  + contract section (CSRF/session-token model, SSE event names, collector-thread lifecycle,
  config precedence) + a drift-test for dirs-with-code-lacking-a-map-entry.

## P2 — UX / polish / test-debt

- ⬜ **[ui] UX: alarm states aren't alarming + color language contradicts** — header alert-count
  hardcoded gray at any severity; green = "done" in backlog but "running" in agents; alerts
  panel container styled identical to neutral panels. Route to frontend specialist.
  `ui/serve.py:872,910,1032,1144,856,962`. [lenses: design#1,#2,#4]. ACC: alarm color at
  count>0; one color per semantic state across panels; alarm container treatment — screenshot-verified.
- ⬜ **[ui] UX: layout order ≠ urgency + done items hog space** — Fleet Agents + Security Alerts
  buried below the Queue-Work box and backlog; done ✅ rows same height as active work.
  `ui/serve.py:925-970,881,1336`. [lenses: design#3,#6]. ACC: Agents+Alerts in top third;
  done rows dense/collapsed — screenshot-verified.
- ⬜ **[ui] UX: weak affordances + truncation cues + header responsive** — 11px gray chevron is
  the only click cue on agent rows; 200px tier scroll boxes have no "+N more"; header flex row
  has no wrap below 900px. `ui/serve.py:829-830,1118,876,805`. [lenses: design#5,#8,#9].
  ACC: stronger affordance visible in static screenshot; count badge/fade; graceful wrap.
- ⬜ **[ui] Live patches destroy scroll position + text selection** — `renderAgentDetails`
  rebuilds children (`textContent=''`) every ~1s for the active expanded agent (wipes scroll in
  the prompt box); `textContent=` in keyed panels tears down Text nodes (kills selection).
  `ui/serve.py:1042-1088,1152-1155,1185-1235,1337-1339`. [lenses: js#4 + frontend#5 — DUPLICATE].
  ACC: field-level patch, leave unchanged nodes untouched; scroll + selection survive a live change.
- ⬜ **[ui] promptCache Map never evicted on row removal** — grows with every agent ever expanded
  (120 cached / 3 visible). `ui/serve.py:1090-1105,1138-1141`. [lenses: frontend#3 + js#4-minor].
  ACC: evict alongside patchAgents newIds diff; bounded-size test.
- ⬜ **[js] collect-signals.mjs summary line prints `undefined` every cycle (default config)** —
  junk/strayRepo/respawnWatch are `{skipped:true}` when extended off, read with `.length`/
  `.quarantinable`. `monitor/collect-signals.mjs:947`. [lenses: js#3]. ACC: normalize to 0;
  test asserts no "undefined" substring in default-config summary.
- ⬜ **[test] Expand verify_dash.py coverage** — add reconnect (2.66s resync, benign
  ERR_CONNECTION_RESET allowance), malformed-SSE, scale (30/100), promptCache eviction,
  selection-preservation, /submit encoding, 8-cap-vs-header cases. [lenses: frontend#6 test-debt].
  ACC: each becomes a failures.append case.
- ⬜ **[bash] backup-fleet.sh has no cleanup trap** — mktemp temp_json/temp_result leak on
  interrupt over the daemon's long life. `daemons/backup-fleet.sh:185,209`. [lenses: shell#G].
  ACC: `trap 'rm -f' EXIT INT TERM`, hoist vars.
- ⬜ **[bash] watchdog-gui.sh `printf '%b'` mangles backslash content** — repo names / log tails
  with literal `\t`/`\n`/`\U` tear lines or throw `printf: missing unicode digit for \U` on
  Windows/Git-Bash paths. `dash/watchdog-gui.sh:135-137`. [lenses: shell#H]. ACC: `printf '%s'`
  (ESC codes are already real bytes); backslash-content render test.
- ⬜ **[sec] CSRF token file world-readable window** — `write_text()` then `chmod(0600)` TOCTOU,
  first run + POSIX only. `ui/serve.py:120-137`. [lenses: security#4 LOW].
  ACC: `os.open(O_CREAT|O_EXCL,0o600)`.

## P3 — docs / strategic (may fold into a single docs PR)

- ⬜ **[docs] Document the audit-log truncation-anchor trust model** — hash chain = in-band
  tamper detection, anchor = accidental truncation, git history = final authority; adversary
  with `--no-verify` / `state/` write already owns it (confirmed structurally unfixable locally,
  don't expand it). [lenses: honest#2 + security "verified" note]. ACC: NOTES.md section.
- ⬜ **[arch] Pillar 4 (forensic replay) is the weakest — wire it into the loop** — agent-
  forensics.sh is untested and not used by the refinement loop; add test coverage + an
  auto-bisect hook so a future refactor can catch regressions at old commits. [lenses: honest#3].
  ACC: forensics test + a documented loop step. (Strategic — schedule, don't rush.)

## Needs a user decision (⏸)

- ⏸ **[arch] serve.py embedded-UI split (~850 lines HTML/CSS/JS in a Python string)** — extract
  `ui/static/dashboard.{html,js,css}`; serve.py → ~400-line backend. High maintainability ROI
  but a large refactor that touches every UI item above — **user call on sequencing**: do it
  LAST (after the P0–P2 UI bug fixes land on the current single file) to avoid rebasing every UI
  branch onto a moved target. [lenses: honest#1]. Recommendation: defer to end of wave 6.

---

# Wave 7 — audit #3 (four-lens: security+correctness, architecture, frontend/UX, honest)

Audit #3 ran post-wave-6: NOT clean (2 quick-wins fixed in PR #64, rest deferred to wave 7). Four-lens findings deduplicated and ranked below (all ⬜ unstarted). Branch-per-item model; TDD-first acceptance gate.

## P1

- ⬜ **[js] proposals.mjs + collect-signals.mjs acquireLock is FAIL-OPEN → silent data loss under load.** After maxAttempts (50×~10ms ≈ 500ms) the lock gives up and proceeds UNLOCKED (`tools/proposals.mjs:82-84`, and the same acquireLock in `monitor/collect-signals.mjs`). Under real concurrent writes (monitor + UI emitting proposals, or slow I/O), an append can interleave with an accept/reject rebuild and be lost. This is ALSO the root of the intermittently-flaky `tests/proposals.test.mjs` concurrent-race test. **DECISION NEEDED:** fail-closed (log + abort the op, retry next cycle) vs a single-writer queue serializing emit/accept/reject. Recommendation: fail-closed for the integrity-critical PROPOSALS.md/audit writes. [lenses: honest#1, security]. ACC: lock timeout no longer proceeds unlocked; a concurrent emit+accept test shows zero data loss deterministically (not timing-tuned).

## P2

- ⬜ **[ui] Accessibility: status/severity are color-only + rows not keyboard-operable (WCAG 1.4.1, 2.1.1).** Agent status emoji and alert severity (header count, alerts box, alert lines) carry meaning by color alone with no text/ARIA label; `.agent-row` has a click handler but no `tabindex`/`role="button"`/`aria-expanded`/keydown. `ui/serve.py` (agent-status render, alert count/box/line render, agent-row). [frontend#1-5]. ACC: text/ARIA labels for status+severity; rows focusable + Enter/Space operable.
- ⬜ **[ui] Color semantics incomplete: agent "running" still GREEN, not blue.** PR #55 set green=done / blue=running / red-amber=alarm, but the agents panel still shows running as green (`ui/serve.py` agent-status + `dash/dash-extra.mjs` running color `c.G`). [frontend#6]. ACC: running renders blue across serve.py + dash-extra; verify_dash asserts it.
- ⬜ **[py] serve.py has 12+ bare `except:` handlers masking bugs.** e.g. `generate_session_token` config load silently swallows errors (`ui/serve.py:68` and ~11 more). [honest#3]. ACC: typed excepts + at least a debug log; no silent config-load failures.
- ⬜ **[arch] ThreadingHTTPServer spawns a thread per request — unbounded.** The SSE fix caps SSE *clients* but a flood of ordinary requests still creates unbounded threads → memory exhaustion (`ui/serve.py`). [honest#4]. ACC: bounded worker pool or a total-connection limit.
- ⬜ **[docs] tools/CLAUDE.md documents only 3 of 8 tools.** Missing rotate_logs.py, reconstitute.sh, proposals.mjs, verify_dash.py, verify_submit_encoding.py (or move the proof tools to tests/ docs). [architecture]. ACC: every shipped tool has a domain-map/contract entry; domain-map-drift test still green.
- ⬜ **[bash] acquire_audit_lock leaks the lock if the pid-file write fails.** On disk-full, mkdir succeeds but `echo $$ > pid` fails, leaving a lock with no pid; release's ownership check then never frees it → 10s hangs cascade (`hooks/pre-push-policy.sh:42`). [security#3]. ACC: verify the pid write / write pid before returning success.

---

---

# Wave 8 — refinement sprint (in planning)

Prioritized backlog for the refinement loop post-wave-7. Branch-per-item; TDD-first acceptance gate.

## P0 — blocking / top-tier fixes

- ⬜ **[needs-decision] proposals.mjs acquireLock fail-closed vs. single-writer queue** — Monitor emit/accept/reject coordination; must not proceed unlocked after timeout. Decision: fail-closed (log + abort, retry next cycle) or enqueue for ordered processing. (monitor/collect-signals.mjs:536 + tools/proposals.mjs:19)
- ⬜ **[test] verify_dash.py browser proof wired into CI** — Verify_dash browser tests run in CI; /submit UTF-8 encoding + malformed SSE; local skip→fail gate. Plus verify_submit_encoding.py proof.
- ✅ **[docs] /power adoption quickstart in README** — PR #67 ships skills/power/SKILL.md; README section on priming orchestrator, skill setup, /power invocation (this PR).
- ⬜ **[ui] dashboard a11y contrast + keyboard nav** — WCAG 1.4.1 color contrast, 2.1.1 keyboard operability for agent rows, status/severity text labels + ARIA (in flight fix/wave8-ui-serve).

## USER-FLAGGED FEATURES

- ⬜ **backlog tracker on dashboard** — Live backlog rendering from state/tracker.json; /api/tracker endpoint; multi-lane UI; agent inbox integration.
- ⬜ **orchestrator main-thread status panel** — Real-time orchestrator heartbeat + next steps; state/orchestrator-status.json + SSE feed.

## P1 — hardening / correctness

- ⬜ **[ui] /submit content-length DoS guard** — Bounded request size before body parse (in flight).
- ⬜ **[bash] run-watchdog.sh cycle exit-code visibility** — Daemon cycle success/failure reporting (in flight).
- ⬜ **[py] serve.py collector silent-except hardening** — Typed exception handlers; no swallowed errors (in flight).
- ⬜ **[docs] tests/CLAUDE.md + tools/CLAUDE.md regeneration** — tests half this PR (22 files, 3 suites); tools half after PR #68 merge.
- ⬜ **[config] example-config transcripts_root fix** — Root path resolution corrected (in flight).
- ⬜ **[tests] 9 new tool tests from PRs #67/#68** — power_selftest.py, inbox_drain.py, + updated tools coverage after PRs merge.

## P2 — polish / UX / test-debt

- ⬜ **[sec] ui-inbox symlink TOCTOU** — File creation atomicity for inbox (in flight).
- ⬜ **[error] /agent error-message leak** — Path/stack info in agent error responses (in flight).
- ⬜ **[bash] backup-fleet.sh sed portability** — GNU vs. BSD sed (in flight).
- ⬜ **[arch] collect-signals corrupted-state handling** — Graceful recovery from truncated JSON (in flight).
- ⬜ **[git] .gitignore heartbeat narrowing** — Scope heartbeat exclusions to ~/.claude/loops (in flight).
- ⬜ **[tools] heartbeat-staleness logic consolidation** — Move stale-check logic to tools/heartbeat.py after PR #68.
- ⬜ **[ui] monitor STALE threshold mismatch in GUI** — Align STALE timing across serve.py and dash-gui (in flight).
- ⬜ **[ui] UI polish bundle** — Done-item styling, connection badge, auto-scroll, truncation, aria-live (fold into tracker rework).
- ⬜ **[arch] serve.py monolith split** — Extract ui/static/{dashboard.html,js,css}; serve.py backend only (deferred decision).

## P3 — deferred / strategic

- ⬜ **[py] rotate_logs keep_count guard** — Verify keep_count logic (in flight).
- ⬜ **[dash] GUI double-grep optimization** — Reduce grep calls in watchdog-gui.sh.
- ⬜ **[test] test naming convention unification** — Standardize test file naming across suites.
- ⬜ **[fleet] prune 35 merged agent worktrees** — Clean up old .claude/worktrees/.

---

## Landing log
- 2026-07-12: five-lens re-audit (audit #1) → 9 item-branches + 2 ports, all ✅ merged (PRs
  #17–#35). Honest lens CLEAN.
- 2026-07-12: **seven-lens re-audit (audit #2)** — architect 5, security 4, shell 8, js 5,
  frontend-eng 5+1, design 9, honest CLEAN+4-docs = 40 raw → **26 unique** after dedupe
  (ownership-check ×3-files, /events-exhaustion ×2, verify-audit-lock ×2, scroll/selection ×2,
  path-resolution family ×2). NOT clean → this is wave 6.
- 2026-07-12: **Wave-6 P0 + CI-repair landed on main, push-CI fully green.** Wave 6 P0: 6/6 merged
  (#36–#41); CI-repair wave (#42–#46) fixed 5 Linux-only defects exposed by enabling the gate.
  Root cause: bash -n-only CI never ran any suite for repo's life. All 8 test/scan steps now green.
- Audit cadence: audit #2 found real work → loop continues. Loop ends after 2 consecutive clean
  audits; the next audit after wave 6 lands is audit #3.
- 2026-07-12: **Four-lens re-audit (audit #3)** — security+correctness, architecture, frontend/UX, honest. NOT clean → 7 items (1 P1, 6 P2); 2 quick-wins fixed in PR #64 (already merged); rest deferred to wave 7 backlog. Loop continues.
- 2026-07-13: **Wave-7 work in progress** — Multiple PR branches active; /power adoption docs (PR #67), tools regen + tests/CLAUDE.md docs (this PR), and P0–P3 fixes landing incrementally.
- 2026-07-13: **Wave-8 backlog planned** — P0 blocking decisions (acquireLock, CI wiring, a11y) + user-flagged features (backlog tracker, orchestrator status panel) + P1–P3 refinements. Ready for prioritization after wave-7 lands.

---

# Wave 25 — credibility & safety instrumentation (2026-07-16, closed)

**Opus audit findings:** 18/18 confirmed. 16 unique fixed, merged in PR #166 (commit 53212d9).
No P0/P1 findings.

## Findings merged (✅ PR #166)

- ✅ **[sec] secret_scan gate has blob/worktree bypasses** — `get_git_blob` escapes to shell,
  worktree can scan unvetted .git; fail-closed on git-show error; blob filters verified.
- ✅ **[test] CI gate logic wired but not executed** — metrics_gate runs but result not gated; add
  exit-code propagation + test isolation (metrics_gate scans repo at cwd, test_metrics_gate stops
  chdir leakage).
- ✅ **[py] Python tools missing portability for non-UTF8 envs** — rotate_logs, verify_dash,
  verify_submit_encoding hardened for latin-1 fallback + codec errors.
- ✅ **[config] gitattributes missing shell/script LF enforcement** — Add root .gitattributes to
  enforce LF for shell and script files (avoid CRLF creep on Windows).
- ✅ **[docs] monitor/CLAUDE.md + bin/CLAUDE.md drift from implementation** — Update monitor and
  bin domain docs for accuracy; domain-map-drift test confirmed.
- ✅ **[docs] README + CHANGELOG stale for wave-25 changes** — Update docs for 16 fixes landed.
- ✅ **[arch] rotate_logs concurrency guarantee not enforced** — Lock around non-atomic O_APPEND;
  verify pid write succeeds before returning; test: concurrent append under load.

No findings in backlog (audit clean on remaining items; defer credibility-layer work to wave-26).
