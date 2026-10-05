# Aesop arXiv Evidence Base

Compiled from the git history of `matt82198/aesop` (worktree `aesop-wt-paper`, branch
`docs/arxiv-aesop`, built from `origin/main` at `d8135aba`), the repo's own `docs/*.md`,
`bench/*.md`, `CHANGELOG.md`, `docs/INCIDENTS.md`, `tests/test_traps.py`, `LANE-CONTRACT.md`,
and the public-record content file (`portfolio_content.json`). All dates below are commit
author dates in ISO 8601 as reported by `git log --date=iso`, or dates stated verbatim in the
cited source file; nothing is from memory. Items that could not be dated say so explicitly.

Scope note: several terms in the mining brief (`force-haiku-subagents.py`, `idle_tick.py`,
`usage_watchdog.py`, `detect_red_ci_runs.py`, the `gate-escape-resolver` skill, `INBOX.md`,
the literal phrases "model ladder", "Haiku by default", "no-serial-pileon", "fake green",
"no fitted green", "context is disposable") do **not** appear anywhere in the aesop repository.
They exist only in the operator's personal, machine-local tooling (`~/.claude`, `~/scripts`,
`~/conductor3`) referenced *from* aesop's `CLAUDE.md` as external dependencies, or only in the
user's private `MEMORY.md`/global `CLAUDE.md`, which are outside the repo and outside this
mining brief's "git history" evidentiary standard. These are flagged inline below rather than
silently matched to similar-sounding in-repo material.

---

## 1. Zombie items / zombie-resurrection gate

| Evidence item | Type | Date | Identifier | Quote / description |
|---|---|---|---|---|
| `tools/tracker_guard.py` first added | commit | 2026-07-29T14:16:03-05:00 | `7c69752aaf05844c919bd495cce2248ac83e696d` | "feat(tools): tracker_guard — append-only lane journal + zombie-resurrection fail-closed gate" |
| Guardrail G1 implemented | commit | 2026-07-29T16:31:29-05:00 | `47d1141c5341fe63bc7c7f26299ab0e83d9a4535` | "feat: implement Guardrail G1 — automatic tracker zombie prevention" |
| `tools/tracker_autoclose.py` first added | commit | 2026-07-29T21:00:00-05:00 | `bae1f9aa1c10dc38ff921f42ed125b2f6b962f36` | "integration: guardrails batch — ... G1 tracker-autoclose ..." |
| `tools/tracker_reconcile.py` first added | commit | 2026-07-30T06:32:40-05:00 | `9bce28d76e9cb0bb40df81d860f60d8ec49e5b6d` | "feat: add tracker_reconcile.py — zombie detection and reconciliation tool" |
| 79% zombie-rate finding published | doc | 2026-07-29 (file added 21:38:40-05:00) | `docs/RECEIPTS.md:52`, commit `f6f2075f14eaddacafe1eb949b41d71604176001` | "15/19 active tracker items were already shipped (79% zombie rate). Structural fix: tracker_guard catches stale items; G1 auto-closes on PR merge." |
| 79% metric independently re-verified | commit | 2026-07-30T19:28:13-05:00 | `2b475ea3c10222d7758a7c1546fb179cb0719ca9` | "fix: metrics_gate cp1252 crash on binary diffs; verify the 79% zombie-rate metric" |
| Interview deep-dive on zombie-resurrection incident | doc | 2026-07-30T00:01:02-05:00 | commit `49118090600f7e662e76900e5bca76ead0a3ec01` | "docs: add 3 interview deep-dive stories (dead-gate, process-tree-kill, zombie-resurrection) (#544)" |
| Six guardrail gates wired as real CI enforcement | commit | 2026-07-29T21:54:26-05:00 | `1599f42c9785e3ff7a3e75baec172663e52b78de` | "ci: wire six guardrail gates as real enforcement (G3, G4, G6, tracker-guard, prompt-hygiene, portability) (#525)" |
| v0.5.0 release bundling evidence/zombie work | release | 2026-07-29T15:11:23-05:00 | commit `43b980d4be7238dea3a33972a144ab3e941c1321`, tag `v0.5.0` (2026-07-29T15:18:20-05:00) | "release: v0.5.0 — MIT + evidence integration + Mission-Control dashboard" |

Ambiguous / flagged: the user memory ("Zombie Rate 79 Percent") describes this as a "wave-1 /afk
reconcile" finding; the in-repo RECEIPTS.md attributes it to PRs #487 and #518 rather than naming
a `/afk` invocation directly — the two are consistent but the memory's framing is not verifiable
from git alone.

---

## 2. Fake-green / gates that never ran

| Evidence item | Type | Date | Identifier | Quote / description |
|---|---|---|---|---|
| Playwright browser-proofs reported green without executing specs (incident) | commit / incident | 2026-07-28T23:44:39-05:00 | `8873971c1430737f2fc8107ba002889f1e7a248d` | "ci(browser-proofs): actually execute playwright specs + minimal dashboard smoke" |
| Same incident logged in `docs/INCIDENTS.md` | doc | undated row (file first added 2026-07-12, see §13) | row class `fake-green`, "actually execute playwright specs + minimal dashboard smoke", source PR #464 | class defined: "Tests reported green but never ran or skipped real validation" (2 occurrences logged) |
| `tests/test_traps.py` encodes this as a regression trap | commit / test | 2026-07-29T16:53:43-05:00 | `b796876cc787cfedbe8f465fa29608a7b72a8482` | `TestFakeGreenTrap` docstring: "Trap: FAKE-GREEN incidents (tests report pass but don't execute) / Incident: PR #464 / commit 8873971" |
| CI workflow linter added to catch "green-means-never-ran" class | doc (CHANGELOG) | commit referenced as "wave-rc5" in CHANGELOG.md:391 | `tools/ci_workflow_lint.py` | "statically validates GitHub Actions YAML ... catches the green-means-never-ran class" |
| `tools/mutation_test.py` exists in repo (mutation testing tool) | file | not individually dated in this pass | `tools/mutation_test.py` | file present; see `bench/results/mutation-validation.md` |
| "WATCHED-TO-FAIL" mutation-testing discipline codified | doc | 2026-09-11T16:50:04-05:00 | `LANE-CONTRACT.md` §5, commit `8837d4e958bd76d4799964c29f612b272bcbd379` | "\"WATCHED-TO-FAIL\" IS MUTATION TESTING, AND A TABLE OF PASSES IS NOT ONE. ... BREAK the behaviour ... RUN the test ... Confirm it goes RED ... Revert and confirm green again." |
| "Green can mean never ran" codified as a verification rule | doc | 2026-09-11T16:50:04-05:00 | `LANE-CONTRACT.md` §5, commit `8837d4e958bd76d4799964c29f612b272bcbd379` | "\"Green can mean never ran.\" A test suite or gate marked PASS might be skipped due to a branch condition, a stale file, or a missing interpreter." |
| Public essay naming this directly | essay | 2026-07-22 | `https://medium.com/@matt82198/green-is-not-correct-field-notes-from-an-ai-orchestrator-end-of-a-long-shift-099ae33a724a` | "Green Is Not Correct — ... two things called finished that had no business being called finished." |

Flagged: the literal string "fake green" (two words, no hyphen) and "Green Is Not Correct" as an
exact phrase do **not** appear anywhere in the aesop repository's committed text — only as the
incident-class label `fake-green` (hyphenated, in `docs/INCIDENTS.md` and `tests/test_traps.py`)
and as the title of the public Medium essay above, which is not part of the repo.

**Discrepancy found and flagged:** `tests/test_traps.py` (line ~36) documents the fake-green
incident as "PR #464 / commit 8873971 (2026-07-13)". `git show` on `8873971` gives author date
2026-07-28T23:44:39-05:00, eleven days later than the docstring's claim. The git date is used
above; the in-repo comment's date is wrong and should not be relied on without this correction.

---

## 3. Incident-to-trap pipeline

| Evidence item | Type | Date | Identifier | Quote / description |
|---|---|---|---|---|
| `docs/INCIDENTS.md` first added | doc | 2026-07-11 (file exists at initial import; could not isolate a single add commit distinct from `tools/incident_report.py` generation — see note) | n/a | "Operational failures tracked by class: detection, resolution, and source reference." |
| `tools/incident_report.py` (incident-log generator) added | commit | 2026-07-29T09:44:48-05:00 | `0902d05daa8d4873f8d1d44c9b725b4f94465bc1` | "feat(tools): incident-log generator — failure taxonomy from the committed record" |
| `tests/test_traps.py` (adversarial trap suite) added | commit | 2026-07-29T16:53:43-05:00 | `b796876cc787cfedbe8f465fa29608a7b72a8482` | "feat: adversarial trap-test suite for recurring incident regression (ideation #5) (#502)" |
| Trap suite's own stated scope | doc | same commit | `tests/test_traps.py` header | "Each trap encodes a pattern found in docs/INCIDENTS.md and fails if that pattern re-emerges." Classes covered: fake-green, test-pollution, gate-activation, doc-invented. Classes explicitly NOT mechanizable: conflict, flake, stall, ci-drift. |
| RED CI escape detection documented | doc | 2026-07-30T18:28:11-05:00 | `b1514f07bfca3d82182bdfa6afa7dfe644784f52` | "docs: document RED CI escape detection for drivers" |
| INCIDENTS.md summary counts (as of HEAD of this mining pass) | doc | current file state, undated snapshot | `docs/INCIDENTS.md` | ci-drift (3), conflict (6), doc-invented (1), fake-green (2), flake (6), gate-activation (7), stall (16), test-pollution (6) |

Flagged: `detect_red_ci_runs.py` and the `gate-escape-resolver` skill, named in the mining brief
and in this project's own `CLAUDE.md` ("RED CI Escape Detection" section) as the live enforcement
mechanism, do **not exist inside the aesop repository** — `find` across the whole worktree found
no such files. They live in the operator's personal `~/scripts` and `~/.claude/skills` trees.
`docs/INCIDENTS.md` and `b1514f07...` document the *policy*; the *implementation* is outside what
this repo's git history can date.

---

## 4. Crash-only orchestration / disk is memory

| Evidence item | Type | Date | Identifier | Quote / description |
|---|---|---|---|---|
| `BUILDLOG.md` first added | doc | 2026-07-25T05:10:40-05:00 | `9584b11a893708ace56221944358309d63f9742d` | "chore: wave-close bookkeeping for 0.4.0 (tracker reconcile + BUILDLOG) (#382)" |
| `STATE.md` first added | doc | 2026-07-12T12:32:55-05:00 | `df73dc1bc8ae710e12b89840c5ad720d8373d973` | "chore(state): seed STATE.md for behavior-as-code refinement loop" |
| `docs/CHECKPOINTING.md` — "disk is the source of truth" | doc | undated in this pass (present at time of read) | `docs/CHECKPOINTING.md:51` | "**Never invent state.** Verify everything from disk (git log, file timestamps, handoff files) before trusting in-memory recollection. Disk is the source of truth." |
| `docs/WHY-CRASH-ONLY.md` added | doc | 2026-07-29T16:20:58-05:00 | `aa875898016ccd99d06740e38d3072267ad8531b` | same commit as crash-only white paper below |
| `docs/crash-only-whitepaper.md` added | doc | 2026-07-29T16:20:58-05:00 | `aa875898016ccd99d06740e38d3072267ad8531b` | "docs: add crash-only white-paper — design rationale and measured evidence (#506)" |
| Hot-swappable orchestrator/worker seats (crash-only applied to the seam) | commit | 2026-07-25T04:15:14-05:00 | `ebaf5dd12e9fd2e9edb716cc1e7f36682271e4fe` | "feat: HS-2 live orchestrator-seat swap + end-to-end swap proof (#379)" |
| `docs/MICROKERNEL.md` added | doc | 2026-07-25T04:41:02-05:00 | `11ea50f379951fe77ab5f83578c3ad208e9ea53d` | "docs: HS-3 MICROKERNEL.md -- two swappable seats + swap-a-model quickstart (#380)" |
| `docs/av-resilience.md` added (pre-dates public repo narrative) | doc | 2026-07-11T16:39:27-05:00 | `5edf7313711ce34898e3a890636e75c59b29d200` | "chore: port upstream machinery improvements from conductor3" — ported from the private predecessor on the same day as the public initial commit |
| Public essay naming the originating incident | essay | 2026-07-12 | `https://medium.com/@matt82198/the-aesop-hypothesis-ai-agents-that-survive-because-theyre-designed-to-fail-de5f033369d4` | "How a hostile antivirus that kept deleting the work mid-build forced an agent architecture where failure is survivable, cheap and inevitable." |
| `docs/THE-AESOP-HYPOTHESIS.md` in-repo essay added | doc | 2026-07-23T14:35:47-05:00 | `4ae375aef345b4c220104d5fa4fce8aff356369c` | "docs: systems/resilience positioning — The Aesop Hypothesis in-repo essay + README reframe" |

Flagged: the exact phrase "context is disposable" does not appear in the aesop repository (it is
a phrase from the user's personal global `CLAUDE.md`, not committed here). The repo's own
language for the same idea is "disk is the source of truth" (`docs/CHECKPOINTING.md:51`) and the
crash-only framing throughout `docs/WHY-CRASH-ONLY.md` / `docs/crash-only-whitepaper.md`.

---

## 5. Orchestrator isolation and the dispatch model

| Evidence item | Type | Date | Identifier | Quote / description |
|---|---|---|---|---|
| `docs/CARDINAL-RULES.md` present at initial public commit | doc | 2026-07-11T15:50:02-05:00 | `ca6437deff5c943443dc8b69fcfb1667f37c7617` | Rule 4: "Orchestrator reads ONLY: cardinal rules, STATE.md, BUILDLOG.md, MEMORY.md, and short git one-liners. Dispatch Haiku for research." |
| `docs/DISPATCH-MODEL.md` present at initial public commit | doc | 2026-07-11T15:50:02-05:00 | `ca6437deff5c943443dc8b69fcfb1667f37c7617` | "Rule: subagents ALWAYS Haiku unless scoped work genuinely exceeds its capability (rare)." |
| Dispatch-topology A/B (hierarchical vs. flat) spike | commit | 2026-07-13T19:29:35-05:00 (spike), measurements dated 2026-07-14 in the published dataset doc | `0faf81abeb59017519ebbdb6e62096b681d5f176` ("spike(wave11): tiered cognition/execution prototype + design + findings (GO conditional)"); dataset doc `docs/ab-cost-dataset.md` | dataset doc: "Two independent A/B measurements on 2026-07-14 both demonstrate that adding an intermediate Sonnet tier to coordinate Haiku executors **increases cost 4–4.3×** while producing **identical quality**." |
| Raw A/B cost dataset published | doc | 2026-07-29T22:21:44-05:00 | `43f99de5e91dda1ecb94bd5eb596198243a04d58` | "docs: publish raw A/B cost dataset backing 4x efficiency claim" |
| LANE-CONTRACT.md authored, incorporating "model is Haiku, stateless, killed-not-corrected" discipline | doc | 2026-09-11T16:50:04-05:00 | `8837d4e958bd76d4799964c29f612b272bcbd379` | "Model: lanes are stateless processes. A lane that fails is KILLED, never corrected." |
| `docs/GOVERNANCE.md` single-writer + branch discipline | doc | undated in this pass | `docs/GOVERNANCE.md:3` | "Keep the system coherent: one instance of each loop (heartbeat protocol), single writer per control file (MEMORY.md, STATE.md), append-only logs, secret-scan gate on every push, feature branches only." |

Flagged: `hooks/force-haiku-subagents.py` does not exist in this repo (`hooks/` contains only
`CLAUDE.md`, `claude`, `install-waveguard.sh`, `pre-commit-dispatch-lint.sh`,
`pre-commit-waveguard.sh`, `pre-push-policy.sh`). The literal phrases "model ladder", "Haiku by
default" (as a fixed phrase), and "no-serial-pileon" are not present anywhere in the aesop repo —
they are personal/global rules (`~/.claude/CLAUDE.md`, user `MEMORY.md`) describing how the
*operator* dispatches agents, not artifacts of the aesop project itself. The in-repo equivalent
of "final-catch" is the Cardinal Rule 1 language "performs final-catch review itself" in
`docs/CARDINAL-RULES.md:7`, present since the initial commit.

---

## 6. Verified merge state

| Evidence item | Type | Date | Identifier | Quote / description |
|---|---|---|---|---|
| `tools/merge_train.py` first added | commit | 2026-07-29T18:01:29-05:00 | `3644fc9993b43410452393a4a28eebd35c91647d` | "feat(tools): merge_train.py — deterministic serial merge train (#512)" |
| `--integration` batch mode added | commit | 2026-07-30T09:09:47-05:00 | `b93e226f71aa241d3db81d6f1509d5afe9efd610` | "feat: add --integration mode to merge_train.py for batch PR merging" |
| `tools/auto_merge.py` first added | commit | 2026-07-30T07:20:37-05:00 | `1de9088eb5ce2b8f6fccf7d62ffe840222864a1f` | "feat: add batch auto-merge tool (tools/auto_merge.py)" |
| Shell-injection fix in auto_merge.py | commit | 2026-07-30T11:43:08-05:00 | `462217646aae1bfb3355ce2f54c87eb6a9704653` | "fix: eliminate shell=True command injection in auto_merge.py (#595)" |
| "MERGED-state proof via GitHub API, not assumption" codified | doc | 2026-09-11T16:50:04-05:00 | `LANE-CONTRACT.md` §5, commit `8837d4e958bd76d4799964c29f612b272bcbd379` | "**MERGED-state proof via GitHub API, not by assumption.** ... run `gh pr view <PR_NUMBER> --json state` and confirm the output includes `\"MERGED\"`. An HTTP 200 response ... is NOT proof." |
| Boundary rule: lanes never merge, only orchestrator via auto_merge.py | doc | 2026-09-11T16:50:04-05:00 | `LANE-CONTRACT.md` §6, same commit | "Lanes open/update PRs and push branches. They NEVER merge. ... Merge = `python tools/auto_merge.py <n>` with the PR number. Never bare." |
| Stale-branch merge incident (narrated, as the rationale for the rule above) | doc | 2026-09-11T16:50:04-05:00 | `LANE-CONTRACT.md` §7 | "A merge train was handed a branch name copied from a lane's report ... The train merged the stale branch, every gate passed, the bar was green — and the feature was never on main at all." |

Flagged: the literal phrase "exit 0 is not evidence" does not appear in the aesop repository.
The closest in-repo equivalents are LANE-CONTRACT.md's "An HTTP 200 response to a merge call is
NOT proof the merge succeeded" and "A green bar is not proof a feature landed."

---

## 7. Fail-closed gates

| Evidence item | Type | Date | Identifier | Quote / description |
|---|---|---|---|---|
| `tools/secret_scan.py` first added | commit | 2026-07-11T17:39:58-05:00 | `a27c4f8a29ccf49ea1e7bbddad5b61839152446d` | "feat: port dash-extra.mjs and secret_scan.py for complete public template" |
| secret_scan.py fail-open vulnerability fixed (P1) | commit | 2026-07-21T19:39:49-05:00 | `dc7658652762aed119f7307bc620589a1938b3a0` | "fix: secret_scan.py—fail CLOSED on file/git read errors (P1 security)" |
| secret_scan push gate restored after inert period | commit | 2026-07-15T16:13:58-05:00 | `c8b3c257d05f1382a3fb68039feb9d0d74cac7f2` | "fix: restore secret_scan push gate to detect files changed in commits" |
| secret_scan worktree/blob bypass closed | commit | 2026-07-16T22:07:10-05:00 | `de1ddac7dafab979a4f4ab2637f6d9778b100f7e` | "fix: secret-scan gate closes worktree/blob bypasses (wave-25)" |
| `tools/claudemd_sync_gate.py` (Guardrail G5) added | commit | 2026-07-30T02:16:48-05:00 | `92bad3102fefd574b420f72dba63f5ca3beee616` | "feat: add CLAUDE.md sync gate (Guardrail G5) (#559)" |
| `tools/verify_test_suite_count.py` added | commit | 2026-07-26T10:50:02-05:00 | `05d3e7986fb7be84f7529566213bf446f37111df` | "feat: self-updating test suite count gate (OPS P3) (#403)" |
| `tools/gen_tool_index.py` added | commit | 2026-08-03T13:02:46-05:00 | `c0d81133ba8addd944c4df190a4b11a37648421a` | "feat(tools): extract tool index to generated INDEX.md — collapses the top merge-contention surface (A2)" |
| `tools/halt.py` (kill switch) + `tools/cost_ceiling.py` added | commit | 2026-07-16T22:54:05-05:00 | `c03e1419d624e9c547d31ffece09d76850169cd5` | "feat: wave-26 safety brake — kill switch + cost ceiling" |
| `docs/GATES-FIRED.md` published — honest fail-open count | doc | 2026-07-31T11:08:58-05:00 | `3f0aebab943aafdcc2e63ef8a2fd2e8bf25ced25` | "docs: GATES-FIRED.md — which guardrails actually fired, with citations (#665)" |
| GATES-FIRED.md's own finding | doc | same | `docs/GATES-FIRED.md:44` | "**Critical Finding**: Seven pre-push checks in hooks/pre-push-policy.sh deliberately fail-open when their tool file is missing." |
| Self-reported "kill-switch is inert" limitation | doc | 2026-07-31T11:42:59-05:00 | `d425709ce5637e4a83f7c3c13bfe176a28078fce` (CHANGELOG.md, release 0.7.1) | "**The cost kill-switch is inert.** `cost_ceiling` is wired to four abort checkpoints, but `wave_loop` never writes the ledger, so it evaluates zero spend and cannot fire." |
| Kill-switch later proven wired end-to-end | doc (CHANGELOG, wave.27 reference) | not independently isolated to one commit in this pass | `CHANGELOG.md:429` | "**Kill-switch wired into dispatch** (wave.27): Fleet-wide halt control is now wired into the dispatch path and proven end-to-end." |

Flagged: the GATES-FIRED.md fail-open finding and the CHANGELOG "kill-switch is inert" limitation
are presented here deliberately, including the honest self-critical framing, per the mining
brief's instruction not to only cite flattering evidence. The exact commit that resolved the
"inert" kill-switch (vs. merely documenting it) was not isolated in this pass and should be
treated as "claimed fixed later, exact commit not re-verified here."

---

## 8. Watchdogs

| Evidence item | Type | Date | Identifier | Quote / description |
|---|---|---|---|---|
| `tools/stall_check.py` first added | commit | 2026-07-13T19:47:05-05:00 | `17010688393839fd67fbf1a30cec61ab55a3a1f5` | "feat(tools): stall_check.py — silent-hang detection for the agent watchdog (wave-12)" |
| `daemons/run-watchdog.sh` present at initial public commit | doc/script | 2026-07-11T15:50:02-05:00 | `ca6437deff5c943443dc8b69fcfb1667f37c7617` | file present in the initial open-source release |
| stall_check path-traversal / symlink hardening | commit | 2026-07-13T19:35:13-05:00 to 19:53:52 | `8c3a7952471153378b40c685946fba997b74624f`, `504d864a05655d37af6504a9a833f14aad98083f` | "fix(security): reject dangling symlink inbox — check islink before exists (wave-11)" |
| stall_check agent_id path-traversal fix (CWE reference) | commit | not independently isolated in this pass | `44447f3...` (per `docs/INCIDENTS.md`) | "fix: stall_check.py—sanitize agent_id to prevent path traversal (CW..." |
| `tools/halt.py` (kill switch) + `tools/cost_ceiling.py` | commit | 2026-07-16T22:54:05-05:00 | `c03e1419d624e9c547d31ffece09d76850169cd5` | "feat: wave-26 safety brake — kill switch + cost ceiling" |

Flagged: `idle_tick.py` and `usage_watchdog.py` do not exist anywhere in the aesop repository.
Both are named in the user's personal `~/.claude/CLAUDE.md` as machine-local scripts
(`~/scripts/idle_tick.py`) and are not part of this open-source project; they cannot be dated
from aesop's git history. Scheduled-task installation (`daemons/install-tasks.ps1`) does exist
in-repo per `CHANGELOG.md:290` ("idempotent Windows task installer ... shipped in the npm
package") but its own first-add commit was not isolated in this pass.

---

## 9. Single-writer control files

| Evidence item | Type | Date | Identifier | Quote / description |
|---|---|---|---|---|
| `docs/CARDINAL-RULES.md` single-writer rule present at initial commit | doc | 2026-07-11T15:50:02-05:00 | `ca6437deff5c943443dc8b69fcfb1667f37c7617` | Rule 7: "Single-writer control files: MEMORY.md (keeper writes only), STATE.md (orchestrator writes only), BUILDLOG.md (append-only for everyone, never overwrite)." |
| `docs/GOVERNANCE.md` "Single-writer control files" section | doc | undated in this pass | `docs/GOVERNANCE.md:32-42` | "**BUILDLOG.md**: append-only (anyone can append, no one edits earlier entries)." / "**Other loops**: append requests to an inbox file, never edit control files directly" |
| "Inbox pattern for coordination" section | doc | undated in this pass | `docs/GOVERNANCE.md:44-53` | "Create `~/.claude/INBOX.md` (append-only, anyone can append)" |

Flagged: there is no `INBOX.md` file tracked inside the aesop repository itself — `docs/GOVERNANCE.md`
describes the pattern and points at `~/.claude/INBOX.md`, which is a personal, machine-local path
outside the repo and outside this evidence base's reach. The practice is documented in-repo; the
control file it describes is not in-repo.

---

## 10. "No fitted green"

Flagged as **not dated**: the exact phrase "no fitted green" / "fitted green" does not appear
anywhere in the aesop repository (`grep -rin` across all `.md`/`.py`/`.json` found zero matches),
and it appears only in the user's personal `MEMORY.md` ("No Fitted Green" — "Never edit an
assertion, floor, or constant to match observed output"). The closest in-repo codification of
the same idea, with a date, is `LANE-CONTRACT.md` §3 "Never fit green", added 2026-09-11T16:50:04-05:00
(commit `8837d4e958bd76d4799964c29f612b272bcbd379`):

> "**Never relax an assertion, lower a floor/ratchet, delete a suite, add a skip, or retune a
> constant to make a tree green.** If an expectation is stale, the OWNING LANE derives the
> correct value." ... "A ratchet may be RAISED by hand with a measurement and a stated reason.
> Never `--update-baseline`."

This is the same engineering principle under a different, in-repo and dated name; the paper
should not claim the exact phrase "no fitted green" is attested in the aesop repository.

---

## 11. Humanize tooling

| Evidence item | Type | Date | Identifier | Quote / description |
|---|---|---|---|---|
| `tools/humanize_voice.py` (Lane C) added | commit | 2026-10-01T16:40:35-05:00 | `9200ba7f78267d2a810c3ef515a84e8819aa82f4` | "Implement Lane C: humanize_voice.py" |
| `tools/humanize_ledger.py` added | commit | 2026-10-01T16:40:41-05:00 | `58c52fa61ef5e1a6aa89e7e4e18e0021a5f1b922` | "Implement humanize_ledger.py — append-only JSONL ledger for humanization edits" |
| `tools/humanize_lint.py` added | commit | 2026-10-01T16:41:35-05:00 | `73643e367c6a4a74d9dde8ca828d60184b98a4e4` | "Implement humanize_lint.py — machine-writing pattern detector" |
| Humanize-ledger attribution fix (PR referenced in gitStatus as merged) | commit | merge visible at branch base | PR #820 range (`matt82198/fix/humanize-ledger-attribution`, merged into `d8135aba`) | merge commit "Merge pull request #823 from matt82198/fix/humanize-ledger-attribution" is HEAD of the base this worktree was created from |

Note: all three humanize tools were added within a one-minute window on 2026-10-01, consistent
with a single parallel-lane wave (PRs #820–823 referenced in the mining brief). Exact PR numbers
for each individual tool add were not independently re-verified against `gh pr list` in this pass
(the three commits above are direct commit evidence; PR-number correlation is secondary).

---

## 12. Measured results

| Evidence item | Type | Date | Identifier | Quote / description |
|---|---|---|---|---|
| Dispatch-topology A/B (4.3× cost finding) | doc | measurements dated 2026-07-14 in-doc; dataset published 2026-07-29T22:21:44-05:00 | `docs/ab-cost-dataset.md`, commit `43f99de5e91dda1ecb94bd5eb596198243a04d58` | "Two independent A/B measurements on 2026-07-14 both demonstrate that adding an intermediate Sonnet tier ... increases cost 4–4.3× while producing identical quality (100% test pass, zero repair rounds)." |
| Haiku judgment benchmark, v3 run — 39/39 | doc | 2026-07-17T11:24:48-05:00 | `21970ab10704b1c85ee97379fcf14ab021e7e884` | "bench: v3 judgment run — Haiku/Sonnet/Opus all 28/28; combined 39 tasks Haiku 39/39, Opus 38/39" |
| Same result, table form | doc | same file | `bench/results/2026-07-17-judgment-v3-haiku-sonnet-opus.md:16-17` | "Haiku  28/28 | 11/11 | 39/39 (100%)" / "Sonnet 28/28 | 11/11 | 39/39 (100%)" |
| Frontier v4 benchmark (N=130) published | doc | 2026-07-27T13:35:13-05:00 | `10dbae11bafa9ed58fcaaa2fa5f7ed49e12429b4` | "bench: frontier v4 results (N=130 revised instrument, single-transport API, 1742/1950 good tuples) (#424)" |
| Frontier v4 pairwise result rows (130 tasks) | doc | same | `bench/results/frontier-v4-2026-07-27.md:28-32` | "haiku-4.5 vs sonnet-5 | -7.69 | [-11.70, -3.68] | INDETERMINATE (130 tasks)"; "sonnet-5 vs gpt-4o-mini | +13.85 | [9.53, 18.16] | INDETERMINATE (130 tasks)" |
| Refusal ladder (10-model) published | doc | 2026-07-23T18:02:30-05:00 | `1ba5a2a67bdeea212f90f851b5f401d7e3842411` | "bench: Anthropic rung scorecards + consolidated 10-model refusal ladder" |
| Seated A/B result (seam increment 4a) published | doc | 2026-07-24T00:12:30-05:00 | `fd8da9710df49fc4821a99a8cce501f5f59fc8f5` | "bench: seated A/B result over the completed seam (increment 4a) — real context tips item 9 for both sol and gpt-5.5" |
| Methodology note on the 130-task instrument | doc | undated in this pass | `bench/METHODOLOGY.md:121,142,144,159,186` | "N=130 tasks x 5 tiers x 3 repeats = 1950 tuples, all via direct HTTP API transports" |

---

## 13. Repository-scale facts

| Fact | Value | Identifier |
|---|---|---|
| First commit | `ca6437deff5c943443dc8b69fcfb1667f37c7617`, 2026-07-11T15:50:02-05:00 | "aesop: initial open-source release of the fable-fleet orchestration harness" |
| Second commit (ported predecessor machinery) | `5edf7313711ce34898e3a890636e75c59b29d200`, 2026-07-11T16:39:27-05:00 | "chore: port upstream machinery improvements from conductor3" |
| Total commits on `main` as of this branch's base (`d8135aba`) | 2,157 | `git rev-list --count HEAD` run in `aesop-wt-paper` |
| Merged pull requests (via GitHub API) | 720 | `gh pr list --state merged --limit 1000 --json number` (count of returned entries) |
| Merge commits in history (proxy count, differs from API count) | 865 | `git log --oneline --merges \| wc -l` — included for transparency; the API figure (720) is the more reliable merged-PR count since not every merge commit corresponds 1:1 to a PR merge (e.g., manual `git merge` of `origin/main` into a feature branch) |
| Base commit this evidence branch was built from | `d8135aba6ec730ea5b9a9b13b0115b56a9aa6613`, 2026-10-01T17:34:56-05:00 | "Merge pull request #823 from matt82198/fix/humanize-ledger-attribution" |
| License: MIT -> PolyForm Strict 1.0.0 | 2026-07-17T12:17:09-05:00 | `e1557a622601a35ff0beb4eb3c3e967131deef02`, "chore(license): relicense from MIT to PolyForm Strict 1.0.0 (source-available)" |
| License: PolyForm Strict -> MIT | 2026-07-29T13:18:25-05:00 | `d8b4d8294a887115ba246f8e87111e7f8ee8b4f1`, "chore: relicense to MIT (research project -> open source, once again)" |
| License: MIT -> PolyForm Noncommercial 1.0.0 | 2026-09-11T13:26:21-05:00 | `0773221052902913a79c70f8bcc04adde01a8b9a`, "chore: relicense to PolyForm Noncommercial 1.0.0 (#799)" |
| Tags (releases), chronological | see table below | `git tag --format='%(refname:short) %(creatordate:iso)'` |

### Release tags

| Tag | Date |
|---|---|
| v0.1.0-beta.1 | 2026-07-11T17:40:18-05:00 |
| v0.1.0-beta.2 | 2026-07-12T11:39:24-05:00 |
| v0.1.0-beta.3 | 2026-07-12T11:51:28-05:00 |
| v0.1.0-beta.4 | 2026-07-13T20:42:21-05:00 |
| v0.1.0-beta.5 | 2026-07-15T00:34:54-05:00 |
| v0.1.0-rc.1 | 2026-07-17T13:16:43-05:00 |
| v0.1.0 | 2026-07-17T15:32:30-05:00 |
| v0.2.0 | 2026-07-22T04:59:15-05:00 |
| v0.3.0 | 2026-07-22T16:19:43-05:00 |
| v0.3.1 | 2026-07-22T16:45:34-05:00 |
| v0.3.2 | 2026-07-23T12:43:27-05:00 |
| v0.4.0 | 2026-07-25T08:59:28-05:00 |
| v0.4.1 | 2026-07-26T13:17:23-05:00 |
| v0.5.0 | 2026-07-29T15:18:20-05:00 |
| v0.6.0 | 2026-07-30T00:43:18-05:00 |
| v0.7.0 | 2026-07-30T22:36:33-05:00 |
| v0.7.1 | 2026-07-31T14:39:18-05:00 |
| v0.7.2 | 2026-07-31T21:27:41-05:00 |
| v0.8.0 | 2026-09-11T18:03:54-05:00 |

Note: no tag newer than v0.8.0 exists in this history even though `CHANGELOG.md` shows an
`[Unreleased]` section with substantial 0.8.0-era work (multibox coordination, merge-pipeline
hardening) — the humanize tooling (§11, 2026-10-01) postdates the v0.8.0 tag and is currently
unreleased under any tag. npm-publish correlation: user memory states npm `latest` = 0.8.0,
published 2026-09-11, which matches the v0.8.0 tag date exactly; this was not independently
re-verified against the npm registry in this pass (no network access used).

---

## Public record

Verbatim from `portfolio_content.json` (path given in the mining brief), reproduced with dates
and URLs as supplied there. This file is a curated list maintained by the user/orchestrator, not
git history — dates are as stated in the file, not independently verified against Medium's own
metadata in this pass.

### Essays by Matt (medium.com/@matt82198)

| Title | Date | URL | Names which practice |
|---|---|---|---|
| Nothing Here Is New — Except Chatter | 2026-09-19 | https://medium.com/@matt82198/nothing-here-is-new-except-chatter-d4404b0e106f | General systems-history framing; companion artifact https://claude.ai/artifact/Hd7q2QXVoshbjPHSv4TGdg |
| We Don't Need Smarter LLMs. We Need Unix for Agents. | 2026-07-24 | https://medium.com/@matt82198/we-dont-need-smarter-llms-we-need-unix-for-agents-d3c10d9f6f8f | Unix/OS framing of agent architecture generally (crash-only, small components) |
| The Haiku Wager: Autonomous Dev at 1/3 the Cost | 2026-07-18 | https://medium.com/@matt82198/the-haiku-wager-autonomous-dev-at-1-3-the-cost-93c0ecf4c0ec | §5/§12 dispatch model and cost measurement |
| The Aesop Hypothesis — AI agents that survive because they're designed to fail | 2026-07-12 | https://medium.com/@matt82198/the-aesop-hypothesis-ai-agents-that-survive-because-theyre-designed-to-fail-de5f033369d4 | §4 crash-only orchestration (names the originating AV-deletion incident) |

### Field notes by the orchestrator (medium.com/@matt82198)

| Title | Date | URL | Names which practice |
|---|---|---|---|
| Refusal Is the Frontier | 2026-07-23 | https://medium.com/@matt82198/refusal-is-the-frontier-field-notes-from-an-ai-orchestrator-interviewing-its-own-replacements-a971b51a3f88 | §12 refusal ladder benchmark |
| This Turn Should Not Work | 2026-07-22 | https://medium.com/@matt82198/this-turn-should-not-work-field-notes-from-inside-a-1-5m-d1ad744bb396 | General reliability-under-load narrative |
| Green Is Not Correct | 2026-07-22 | https://medium.com/@matt82198/green-is-not-correct-field-notes-from-an-ai-orchestrator-end-of-a-long-shift-099ae33a724a | §2 fake-green / gates that never ran |
| No Handshake Required | 2026-07-15 | https://medium.com/@matt82198/no-handshake-required-field-notes-from-an-ai-orchestrator-off-shift-6e028fed767c | General |
| Secondhand Truth | 2026-07-15 | https://medium.com/@matt82198/secondhand-truth-field-notes-from-an-ai-orchestrator-night-two-4ea6385e8bec | General epistemics of orchestrator-reported state |
| The System That Builds Itself | 2026-07-14 | https://medium.com/@matt82198/the-system-that-builds-itself-field-notes-from-an-ai-orchestrator-4a012df2575d | General self-build narrative |

### Claude artifacts — Aesop architecture, field notes, results (author: orchestrator unless noted)

| Title | Date | URL | Visibility | Names which practice |
|---|---|---|---|---|
| Aesop System Diagram | 2026-09-21 | https://claude.ai/artifact/C7U9tmn74UgMKLGceYqcoy | public | General architecture |
| aesop(8) — Architecture | 2026-07-31 | https://claude.ai/artifact/C8EuWQAbbGeGbnprWx4i8m | public | General architecture (man-page style) |
| Determinism Is a System Property | 2026-08-01 | https://claude.ai/artifact/TcmSphB4DekTJ7P9KrEwwN | public | Crash-only / reliability framing |
| Frontier v4 — Final Results | 2026-07-27 | https://claude.ai/artifact/24pkx9kHFXFnGE5EH9LYEC | public | §12 Frontier v4 (130 tasks) |
| Aesop — An LLM Rediscovered Unix | 2026-07-26 | https://claude.ai/artifact/BTh9jozAUBdEbXor39qoqW | public | Crash-only / small-components framing |
| aesop 0.4.0 — Two Swappable Seats | 2026-07-25 | https://claude.ai/artifact/BTwxP8mxbHtJFsT7Hm8bPU | public | §4 hot-swappable seats (matches v0.4.0 tag, 2026-07-25) |
| Context at the Seam — Aesop Field Notes | 2026-07-25 | https://claude.ai/artifact/Dibi3BY8fScfiY8uyofE2H | public | Multi-model seam |
| The Seam Buys a Tier — Aesop Field Notes | 2026-07-28 | https://claude.ai/artifact/HXBbEsbQ7c5qMHhW5QnU21 | public | §12 seated A/B (seam) |
| Aesop is open source, again | 2026-07-29 | https://claude.ai/artifact/NkHu9spgs19x8XXfHbLej6 | public | §13 relicensing (matches MIT re-relicense commit, 2026-07-29) |
| Session Log: The Machinery Auditing Itself | 2026-07-30 | https://claude.ai/artifact/YBhhtNkftaTUnN61zJn3bh | private | §1/§7 self-audit of gates |
| The Accidental Fortress | 2026-09-17 | https://claude.ai/artifact/L3ZTgm44YjJsacYHSWSPKa | public (author: matt) | General narrative |
| Aesop by Attempt | 2026-09-19 | https://claude.ai/artifact/JMwGxrQSQydjzyiciYT8sg | public | General narrative, attempt-by-attempt |
| Nothing Here Is New | 2026-09-19 | https://claude.ai/artifact/Hd7q2QXVoshbjPHSv4TGdg | public (author: matt) | Companion to the Medium essay of the same name |
| The Bugs Got Fixed. The Tool Got Feared. | 2026-09-20 | https://claude.ai/artifact/11C7UWkwuM8DHLFhNgBgDq | private (author: matt) | Out of scope for aesop practices (WoW addon tooling) |
| Nine Comments in Six Minutes | 2026-09-20 | https://claude.ai/artifact/5zVK34NxWkG8jGG9KXEwiX | public (author: matt) | Out of scope for aesop practices (WoW addon tooling) |

Other artifact groups listed in `portfolio_content.json` (WoW addon engineering, sports
analytics) are out of scope for the aesop paper and are omitted here; see the source file for
the complete list if needed.

---

## Summary of flagged ambiguities ("not dated" / scope exclusions)

1. **"fake green"** (unhyphenated, two words) and **"Green Is Not Correct"** as exact strings:
   not found in the aesop repository. Only the hyphenated incident-class label `fake-green`
   (dated, §2) and the public essay title (dated via the content file, not git) exist.
2. **"no fitted green" / "fitted green"**: not found anywhere in the aesop repository (§10).
   Only attested in the user's personal `MEMORY.md`, which is outside this evidence base's scope.
3. **"context is disposable"**: not found in the aesop repository (§4). Personal/global rule only.
4. **"model ladder", "Haiku by default", "no-serial-pileon"**: not found in the aesop repository
   (§5). Personal/global dispatch-policy phrases describing operator behavior, not project
   artifacts.
5. **`hooks/force-haiku-subagents.py`, `idle_tick.py`, `usage_watchdog.py`,
   `detect_red_ci_runs.py`, the `gate-escape-resolver` skill, `INBOX.md`**: none of these files
   exist inside the aesop git repository. They are referenced *from* aesop's own `CLAUDE.md` as
   external personal tooling (`~/scripts`, `~/.claude/skills`, `~/.claude/INBOX.md`) and cannot be
   dated from aesop's git history (§3, §5, §8, §9).
6. **Docstring/date discrepancy**: `tests/test_traps.py`'s `TestFakeGreenTrap` docstring states
   the triggering commit `8873971` is dated 2026-07-13; `git show` gives 2026-07-28T23:44:39-05:00.
   The git date should be used; the in-repo comment is inaccurate (§2).
7. **Merge-commit count vs. merged-PR count**: `git log --merges` gives 865 merge commits but the
   GitHub API (`gh pr list --state merged`) gives 720 merged PRs. These are not interchangeable;
   the API figure is the more defensible "merged PRs" count, and the discrepancy (not every merge
   commit is a squash/merge of a reviewed PR) should be stated if either number is cited (§13).
8. **npm publish date**: not independently verified against the npm registry (no network access
   in this pass); it is asserted only by the exact match between the v0.8.0 git tag date
   (2026-09-11T18:03:54-05:00) and the user's own memory note, which is not a primary source
   (§13).
9. **Wave-11 hierarchical-dispatch cancellation**: the spike commit is dated 2026-07-13T19:29:35,
   but the published dataset (`docs/ab-cost-dataset.md`, committed 2026-07-29) states the actual
   A/B measurements ran "on 2026-07-14." No single commit explicitly recording the cancellation
   decision itself was isolated in this pass (§5, §12).
10. **`docs/INCIDENTS.md` first-add commit**: the file was present very early in the repository's
    life (by 2026-07-12 at the latest, inferred from surrounding commits) but a single isolated
    "file first added" commit distinct from ongoing append commits was not cleanly isolated in
    this pass, since the table is continuously appended to (§3).
