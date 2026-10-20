# LANE-CONTRACT.md — the standing contract every aesop lane is dispatched under

Derived from wow-ledger/LANE-CONTRACT.md; universal sections verbatim.

**Model: lanes are stateless processes. A lane that fails is KILLED, never corrected.**
When one fails, the question is never "how do I fix this agent" — it is **"what did this contract
fail to specify?"** Amend this file, then dispatch a FRESH lane. Corrections sent to a live agent
die with it and teach nothing; a contract amendment compounds forever. Failures are cheap when
lanes are Haiku and stateless — so fail fast, fix the contract, re-run.

Every dispatch brief should POINT here rather than restate it. Below is what was learned the
expensive way during aesop development. Each line exists because a lane failed without it.

## 1. Evidence, not narration
- **Run every command you report. Never describe what a command "would" do.** "Would fail",
  "proven by the assertion path", "test logic shows failure" are all REJECTED. Paste literal output.
  *(One lane burned 3 rounds on this before being replaced.)*
- If something cannot be run, say so and why. Do not narrate a hypothetical result.
- A claim in a report that was not executed is a defect, not a summary.
- **"Pre-existing flake", "green locally", and "on schedule" are CLAIMS, not findings.** Attach
  proof — 3x reruns with pasted summary lines, the literal CI log line, or two unattended heartbeat
  advances — or open a fix lane instead. *(The orchestrator refuted 3 of 3 flake claims in one day.)*

## 2. Tests
- **A test file MUST return a suite declaration** (second return value). Without it the runner prints
  `BROKEN <name> (returned no suite declaration)` and the suite NEVER EXECUTES while looking present.
- **A committed suite must PASS on the fixed tree, always.** Never commit a deliberately-failing case
  to "document the bug". Never delete the test to avoid a red.
- **Anti-vacuity is mandatory and is proven by REMOVING the fix and RUNNING the suite** — paste the
  failure, restore, paste the pass. Not by reasoning. If removing the fix does NOT redden it, the
  test is vacuous: say so and rewrite it.
- Verify the suite is COUNTED: executed == declared, 0 dormant, total up by the number you added.
- **A test must EXERCISE BEHAVIOUR, never grep source text.** Asserting that a file *contains*
  `foo:Bar()` — or does not contain `foo.Bar()` — is not a test: it passes on a match inside a
  comment or a string, fails on any equivalent refactor, and proves nothing about what runs.
  Drive the real code path and assert the observable result (no raise, the value written, the
  gate passed). *(Two lanes shipped source-inspection tests before this line existed: a gate
  fix that grepped for `encoding=` vs `text=True`, and a covering fix that grepped for imports
  but never tested the import path itself.)*
- If behaviour genuinely cannot be driven because a mock does not exist, **build the mock or say so
  plainly** — do not substitute a text assertion and call it coverage.
- A wrong fix needs its own control. A test that only catches a MISSING implementation will not catch
  a WRONG one — a naive fix made the reported symptom disappear while leaving the gate open. Mutants:
  implementation-removed, naive-wrong, and destructive.
- **Declare a test module WITH the correct file extension** — `python: tests/test_foo.py`, never
  `"test_foo"`. CI runners read by extension; a missing or incorrect one makes the runner silently
  `SKIP` the suite as dormant while it looks present. *(A lane shipped 30+ lines of genuinely
  behavioural tests that had never executed once — the branch looked complete and added zero coverage.)*
  Confirm CI reports the executed-suite count went UP by the number you added — not just that yours
  "passed".
- **Tests run with origin rewritten to a local bare repo and `gh` blocked** (`tests/__init__.py` ->
  `tools/test_network_isolation.py`); `tools/remote_refs_tripwire.py` wraps CI's test shards and fails
  if a remote branch/PR it can attribute to the run appears (a plausibly test-created branch name, or
  the PR's own head branch moving unexpectedly) -- it does NOT fail on an unrelated branch moving
  elsewhere in the fleet mid-run (logged as "observed, not attributed"; PR #829/#837). Never work
  around either to make a merge_train/merge_queue/auto_merge test reach a real remote.
- **Shell tests run under an isolated `AESOP_STATE_ROOT`/`CONDUCTOR_ROOT`/`HOME`** (`tools/run_shell_tests.sh`
  pins `HOME`/`TMPDIR` before any test runs; `tools/test_isolation_tripwire.py` also guards conductor3
  heartbeats, state JSON, and scheduled-task registrations) -- never pin `AESOP_ROOT`/`CONDUCTOR_ROOT` to
  `REPO_ROOT` or leave them unset on a daemon invocation; always point them at your own mktemp fixture.

## 3. Never fit green
- **Never relax an assertion, lower a floor/ratchet, delete a suite, add a skip, or retune a constant
  to make a tree green.** If an expectation is stale, the OWNING LANE derives the correct value.
  *(A lane updated an expected-file count down when the real issue was that a verify_*.py script was
  skipped, making the count meaningless.)*
- A ratchet may be RAISED by hand with a measurement and a stated reason. Never `--update-baseline`.
- **Never self-grant a `--@gate-allow` annotation.** It makes a gate PASS while the rule it enforces
  stays broken, and the gap is invisible in review — put the write in the file the gate actually
  owns (e.g., store-owners.txt for state_store writes). *(A lane added `--@gate-allow` to
  stateapi_lint when the real fix was removing a direct state file read and using the read_api
  facade instead.)*  If a gate looks wrong, that is a question for the contract, never a self-service
  annotation.
- Re-measure scalars on the MERGED tree. **Never sum two sides' literals** — disjoint changes can
  carry the same stale number with nothing for git to conflict on, and it stays silently wrong.
- **Resolving a merge conflict means keeping BOTH intents, never taking one side's whole file.**
  Taking "theirs" (or "ours") wholesale on a conflicted file silently deletes the other side's
  additions with nothing for git to flag (PR #745: a conflict resolution took the default branch's whole
  `tools/merge_queue.py`, dropping the PR's own `build_bisect_batches`/`parse_bisect_lineage`/
  `bisect_is_exhausted`, and CI stayed green because only the PR's own tests exercised them). After
  resolving any merge conflict, run `python tools/pr_symbol_survival_check.py --base <base> --head
  <head>` (Guardrail G13) before pushing.
- **Lanes cannot force-push (hook-blocked).** Recover a dirty PR with `git merge origin/HEAD` (the default branch, see section 4) into
  your branch, keeping BOTH intents (never `checkout --ours/--theirs` on code or docs), regenerate
  registered generated artifacts (`python tools/gen_tool_index.py --regenerate`), run `python
  tools/pr_symbol_survival_check.py --base origin/HEAD --head HEAD` (0 MISSING), then a plain push.

- **A scalar's COUNTING RULE is part of the scalar.** Re-measuring is not enough if you assume what
  is being counted. A gate counting "Python test files executed" has a rule; a leg adding a `.mjs`
  test would be written wrong by anyone assuming "test files == entries", where the measured truth was
  just the .py count. Run the gate and read ITS number. The CI gate output is the arbiter.
- **THE CLEAN MERGE IS THE DANGEROUS ONE.** A whole-tree scalar (test counts, file counts, gate
  counters in CLAUDE.md) must be true of the MERGED tree but lives in a file neither branch touched
  — so git reports no conflict and the number ships one low, silently. **After every merge, re-measure
  every whole-tree scalar by RUNNING the gate itself** (ci.yml gates, test runners, coverage checks)
  — never by reading either side's literal. A merge with ZERO conflicts warrants MORE scalar checking,
  not less.
- **The full list of gate-owned counters, all in this category — re-measured on YOUR OWN merged
  tree, never copied from another branch, never lowered:** gate-owned counters in CLAUDE.md (test
  counts, file inventories, expected suite counts), CI gate state (EXPECTED_SOURCE_FILES, EXPECTED_GATES),
  and all baseline json files (.encoding-baseline.json, .stateapi-baseline.json, .subprocess-guard-baseline.json).
  Adding a source file or gate without raising the corresponding counter makes every gate abort
  "nothing was verified" — a full green bar over zero actual verification.

## 4. This repo's environment (do not re-derive, do not conclude "missing")
- **Worktrees: sibling only, never the primary tree.** Always create a worktree before writing code:
  `git -C $AESOP_ROOT fetch -q origin && git -C $AESOP_ROOT worktree add $AESOP_ROOT-wt-<name> -b <branch> origin/HEAD`.
  "origin/HEAD" here means the repo's default branch (origin/HEAD; main for aesop/keyrun/psinasty, master for the brain repos). Resolve it with `git symbolic-ref refs/remotes/origin/HEAD`; never hard-code the branch name.
  Exit the primary tree unchanged. Check `git branch --show-current` immediately after creating the
  worktree; if empty (detached HEAD), create a branch first.
- **Never git stash.** The stash is shared across worktrees; concurrent agents cross-contaminate WIP.
  Use `git diff > patch.diff && git checkout . && git apply patch.diff` instead.
- **AESOP_STATE_ROOT**: all state files (heartbeat, logs, audit trail) use `$AESOP_STATE_ROOT` env var
  (default `$AESOP_ROOT/state`). No hardcoded personal paths. For worktree work, export it explicitly.
- **Pre-push gates run automatically on every push.** `python tools/secret_scan.py --staged` blocks
  if secrets are detected. `python tools/encoding_lint.py --check` blocks subprocess calls without
  `encoding='utf-8'`. Never use `--no-verify` to skip (forbidden in every dispatch). If a gate fails,
  FIX IT, do not bypass it.
- Pushes emit a signed local-run receipt automatically; a push without one means the gates did not
  run — fix, don't bypass.
- **Every Python subprocess call needs explicit `encoding='utf-8', errors='replace'`.** The Windows
  default is cp1252, which corrupts UTF-8 output and has crashed production processes. Every
  subprocess.run/check_output/Popen that reads output: `encoding='utf-8', errors='replace'` not
  `text=True`.
- **Lanes may emit a local receipt after their full run:** `python tools/emit_receipt.py --post` (signs what ran, posts it on the head sha; the hosted `verify-receipt` check recomputes the tree hash and is NON-required during the measurement period -- see docs/RECEIPT-GATE.md).
- **Run the ACTUAL CI gate, not a proxy.** `npm run test:py` != a hand-written pytest call; the real
  test count is what CI reports. Verify each gate actually runs: check CI output for the expected counts
  and pass/fail status, never assume "green" means "verified". Partial verification is NOT a pass.
- **Resolve env vars and paths at dispatch time, never hardcode.** Use `$AESOP_ROOT`, `$HOME`, and
  `sys.executable`; never `/c/Users/matt8` or `python3` or machine-specific paths.

## 4a. Before you push — ordered self-checks

Five CI failures this week came from checks lanes could run locally. This ordered checklist prevents them:

1. **Footprint** — `git diff --shortstat origin/HEAD...HEAD` and `git log --oneline origin/HEAD..HEAD` (origin/HEAD = the repo's default branch). STOP if diff is unexpectedly large or any commit subject reads like a test fixture ("Initial", "fixture", "base", "seed", "baseline").
2. **Pyflakes** — `python tools/pyflakes_gate.py` (paste literal output).
3. **Test hygiene** — `python -m unittest tests.test_no_bare_test_functions tests.test_test_hygiene` (paste output).
4. **Metadata** — `python tools/claudemd_lint.py --root .` and `python tools/claudemd_sync_gate.py --check` (paste output).
5. **Tool index** — If tools/ changed, `python tools/gen_tool_index.py --regenerate` and commit the update.
6. **Push cwd** — Always push from the worktree; never `git -C <primary> push`.
7. **PR head** — `gh pr create --base main --head <branch>` explicitly, then verify `gh pr view N --json headRefName,headRefOid`.
8. **Receipt emission** — Every push emits a signed receipt automatically (`hooks/pre-push-policy.sh` `check_emit_receipt`). Never set `AESOP_RECEIPT_EMIT`. A lane that needs a faster push uses nothing; it waits for the hook. A push without a receipt means the gates did not run: fix it, don't bypass it.

## 4b. AESOP NATIVE FIRST — STANDING USER RULE (aesop core)

**"Read tools/CLAUDE.md and the gate implementation BEFORE fixing."** Before writing a gate fix,
subprocess change, or verification, read the actual gate script (`tools/encoding_lint.py`, `tools/secret_scan.py`,
`tools/verify_test_coverage.py`, etc.) to know what it checks, where it reads state, and what pass/fail
looks like. Matching the gate's contract is also what makes reproduction and review coherent.

**How to apply:**
- **Read the gate implementation BEFORE writing a fix.** Thresholds, state files, baseline paths, error
  messages, exclusion rules — it is all there, already defined and load-bearing. `tools/encoding_lint.py`
  scans the WHOLE repo, not just changed files; `tools/verify_test_coverage.py --check` reports orphaned
  test paths; `tools/secret_scan.py` uses word-boundary anchors on patterns.
- **Dispatches must carry the file:line.** A brief saying "fix encoding" gets guesses; a brief saying
  "tools/encoding_lint.py lines 12-14 scan for text=True without encoding=" gets targeted, correct fixes.
  **When a lane produces a large diff for a small gate failure, the failure is usually the ORCHESTRATOR'S
  at dispatch time.**
- **A large diff for a small gate failure is the tell that the reference was not read.** Read tools/CLAUDE.md
  first; it describes every gate and links to its implementation.
- **Prove a gate runs and passes before claiming it passed.** Paste the literal output of `python tools/encoding_lint.py --check`,
  `python tools/secret_scan.py --staged`, etc. Do not report "gates passed" without evidence.
- **The mock/fake state must never grant capabilities the real gate lacks.** If behavior can't be tested
  without a capability the fake state doesn't provide for real, that IS the finding — skip the feature,
  file a follow-up for the mock, or say so plainly.

## 5. Verification
- **The gates are**: `python tools/secret_scan.py --staged` (secrets), `python tools/encoding_lint.py --check`
  (subprocess encoding), `python tools/verify_test_coverage.py --check` (orphaned test files), `python tools/claudemd_sync_gate.py --check`
  (domain code changes documented), `python tools/dispatch_lint.py --check` (forbidden dispatch patterns).
  **Every CI workflow job is a gate.** Run or cite literal output for each.
- **Adding or wiring a gate: run `python tools/new_gate_check.py` green before pushing** — it runs the whole new-gate checklist in one command (pre-push self-test, gate_inventory parity, CLAUDE.md lint/headroom/sync, portability ratchet, verify_gates_wired, dispatch_lint, conflict_marker_check, a dry pre-push range check) and prints the exact fix command for any red row, instead of discovering each item one red CI round at a time (PR #872: five rounds).
- **"Green can mean never ran."** A test suite or gate marked PASS might be skipped due to a branch
  condition, a stale file, or a missing interpreter. Never assume a check passed — verify it actually ran
  by reading: (1) the exact output message, (2) the count of items processed/verified, (3) the tool's exit
  code explicitly (not just "the check passed").
- **After every merge to main, confirm the expected files actually EXIST** on the merged tree via
  `git ls-tree` or `git grep` on the MERGED tip, not on your local branch. Do not trust that a green bar
  means the feature landed.
- **MERGED-state proof via GitHub API, not by assumption.** After a merge, run:
  ```bash
  gh pr view <PR_NUMBER> --json state
  ```
  and confirm the output includes `"MERGED"`. An HTTP 200 response to a merge call is NOT proof the merge
  succeeded — verify MERGED state using the API.
- **"WATCHED-TO-FAIL" IS MUTATION TESTING, AND A TABLE OF PASSES IS NOT ONE.** To prove a test is
  non-vacuous, you MUST: (1) BREAK the behaviour in the PRODUCTION source (not the test), (2) RUN the test
  against the broken source, (3) Confirm it goes RED and paste the failure, (4) Revert and confirm green
  again. A test still GREEN when the code is broken is VACUOUS. A report with no red output means the
  procedure was not run.
- **All four of these are MANDATORY, or the lane FAILED — no partial credit:**
  1. The branch exists on origin: `git ls-remote --heads origin <branch>`.
  2. Every gate is cited with literal output (exact tool output, not "it passed").
  3. Anti-vacuity proofs are two blocks: BEFORE (failure with the fix removed) and AFTER (pass with fix restored).
  4. The PR number and MERGED state are confirmed via `gh pr view --json state`.
  
  **"Remaining work: ..." in a final report means the lane FAILED** — say so plainly and stop, do not
  soften it into "mostly done".

## 6. Boundaries
- **Merge authority: a lane never runs a merge that completes immediately.** No bare `gh pr merge`, never `--admin`,
  and never hand-resolving branch protection. The repo selects the landing mode; the lane does not choose it.
  - **Mode 1 - repos WITH required CI checks (aesop, keyrun).** The lane pushes, opens the PR, and ARMS native
    auto-merge once at PR open, pinned to the tested head:
    `gh pr merge <N> --repo <o/r> --squash --auto --match-head-commit <full head sha>`. The lane takes one
    foreground `gh pr checks <N>` snapshot (exit 8 = pending) and stops. The merge actor is GitHub: it merges once
    the required checks pass. The orchestrator verifies `state == MERGED` with
    `gh pr view <N> --json state,mergeCommit`; it does not poll or babysit the merge.
    **Arm only when the base is the default branch.** Before arming, run `gh pr view <N> --json baseRefName`. If
    `baseRefName` is the repo default branch, arm as above. A stacked PR (base is another PR's branch) is NOT armed:
    GitHub would merge it into that unprotected parent with no CI run. The lane reports
    `stacked on #N; retarget to main after #N merges` and stops.
  - **Draft-then-arm order (supersedes "at PR open" for draft PRs), in this order:**
    1. Open the PR as a draft at the first commit; this draft is the receipt.
    2. Run `gh pr ready <N>` only when the lane's own checks are green locally.
    3. Arm auto-merge immediately after marking ready: `gh pr merge <N> --repo <o/r> --squash --auto --match-head-commit <full head sha>`.
    Draft state is never an excuse to skip arming once the PR is ready.
  - **Mode 2 - repos WITHOUT CI (claude-config, claude-scripts, conductor3, psinasty).** There is no required check
    to arm against, so the lane stops at PR open. It runs its gates in its worktree, pastes the literal output, and
    reports the PR number and the full head sha. The orchestrator lands it with one pinned
    `gh pr merge <N> --repo <o/r> --squash --match-head-commit <sha>` after reading the pasted gate output (tests,
    secret scan), then re-verifies with `python ~/scripts/pr_merged_check.py --repo <o/r> --pr <N>`.
  - **The aesop merge tool.** `tools/auto_merge.py <n>` remains the aesop merge tool when the orchestrator drives a
    merge itself. A lane never runs it.
  - A non-null `autoMergeRequest` on a PR proves nothing about its state. Bind `state` and trust only `MERGED`.
    In both modes `--admin` is forbidden, `--auto` is used only in a Mode 1 repo, and no branch or worktree is
    deleted until MERGED is confirmed.
- **Mode 1 arming, in detail.** A PR must never wait for a session daemon or manual merge to complete. Native
  auto-merge is the merge actor in Mode 1; AesopMergeQueue is disabled. To re-run required checks on an armed PR without
  code changes, re-push the branch (a plain push, or a local merge of the default branch per section 3 when the PR is BEHIND). Never ask
  GitHub to update a PR branch server-side: that creates a merge head no box ran the gates on, so no receipt can exist for it.
  A `gh workflow run` dispatch creates a separate check suite that branch protection ignores.
  A Mode 1 PR that goes DIRTY because a sibling landed first is repaired by the merge in section 8c, never by the orchestrator editing the branch.
- **Fully machine-generated paths (`tools/INDEX.md`, `state/ledger/*.jsonl`, etc. — `tools/generated_paths.py::REGISTRY`)
  are push-blocked by `hooks/pre-push-policy.sh check_generated_paths()`.** The only override is
  `AESOP_ALLOW_GENERATED=1` — the DESIGNED writer path for a generator/regeneration push (a seated session re-running
  the generator, `tools/merge_queue.py::build_batch` scoped to the one push right after `regenerate_on_batch`, a
  daemon push), never for a hand edit. A bot push from GitHub Actions needs no override at all: Actions checkouts
  never have this repo's local pre-push hook installed. Separately, `tests/CLAUDE.md`/`tools/CLAUDE.md` are in the
  broader, weaker `GENERATED_PATHS`/`is_restorable()` tuple (automation may `git restore` them if dirty) but are NOT
  in `REGISTRY` — hand-editing them is legitimate and they are never blocked at push; `tools/INDEX.md` is the one
  path in both, and its own byte-identity gate (`tools/gen_tool_index.py --check`) still runs unchanged regardless of
  who was allowed to push it. **Merge-from-main = merge, then regenerate registered artifacts
  (`python tools/gen_tool_index.py --regenerate && git add tools/INDEX.md`), or let the driver/gate do it:**
  `tools/INDEX.md` merges through the `aesop-regen` driver (`.gitattributes` -> `tools/generated_merge.py`, registered
  per clone by `python tools/install_merge_drivers.py` — the pre-push hook does it for you), and
  `check_generated_regen()` rejects a push whose COMMITTED index is stale, printing exactly that instruction.
  **When a lane may set `AESOP_ALLOW_GENERATED=1`:** Only when the PR's deliberate purpose is to add/edit a `generated_paths.py::REGISTRY` entry (declaring a new generated path) or to regenerate a registered artifact (e.g., re-running `tools/gen_tool_index.py --regenerate` after adding new tool documentation). In either case, the lane must (1) run the registered generator to produce the artifact, (2) commit both the generator code and the regenerated artifact together in the same commit (never a hand-edited generated file), and (3) explain in the PR body which artifact was regenerated and why. The escape hatch must never be used to push hand-edited generated files, to regenerate unrelated artifacts, or to bypass the byte-identity gate on paths outside the PR's stated purpose.
- **Before pushing, run the shard for your test file.** Run `python tools/ci_shard_runner.py <n> 4` for the shard that owns your
  test file (see tests/CLAUDE.md for shard assignment). CI is confirmation of local verification, not discovery of breakage. Paste
  the shard output to your report: it proves your changes work before they hit main.
- **Merge paths.** A lane merges only through the Mode 1 or Mode 2 commands above. `python tools/auto_merge.py <n>` (never bare) is not a lane merge path; the primary tree is that tool's working tree and a lane never runs it.
- Generated whole-tree artifacts (`tools/generated_paths.py::GENERATED_PATHS`) self-heal post-merge via `.github/workflows/regen-on-main.yml` + `tools/regen_all.py`; do not hand-edit them.
- **Domain docs are ALWAYS in scope.** The one-line entry in the changed domain's CLAUDE.md (and `tools/INDEX.md` regenerated when a `tools/` script is added) is ALWAYS in scope for any lane that changes code in that domain. It is required by the pre-push `claudemd_sync_gate`, it is written in the same PR, it is not an out-of-scope edit, and it is never a reason to hand back BLOCKED.
- **Repo gates a lane must satisfy on push** (run each, paste output; fix, never bypass): `claudemd_sync_gate.py --check` (edit the domain CLAUDE.md); `encoding_lint.py --check` (ASCII ratchet `encoding-ascii-baseline.json`, shrink-only); `gen_tool_index.py --regenerate && git add tools/INDEX.md`; `import_resolution_check.py --staged`; `secret_scan.py --staged`; `sibling_import_check.py --check` (guard the import); `verify_test_suite_count.py --check`; `pyflakes_gate.py --check` (burn-down only: `--update-baseline --baseline .pyflakes-baseline.json`); `subprocess_guard.py --check` (burn-down only: `--update-baseline --baseline .subprocess-guard-baseline.json`); G13 `pr_symbol_survival_check.py --base origin/HEAD --head HEAD` (0 MISSING; origin/HEAD = the repo's default branch, see section 4). A ratchet is never raised with `--update-baseline`.
- Stay inside your declared files. If the chain leaves them, **STOP and hand off** — a clean hand-off
  beats a collision and is a complete result, not a failure.
- **REARCH sections 69+ are orchestrator-reserved.** Claim an unreserved number AT WRITE TIME and
  record it; ask before taking 69+.
- **Python**, **ASCII** source (tools/*.py enforced by `encoding_lint.ascii_findings`; shrink-only ratchet `tools/encoding-ascii-baseline.json`).
- **Dead-by-bug code must NOT be deleted — fixes wire it up.**
- **Never disable git hooks** — no `core.hooksPath` override, no `--no-verify`/`-n` on commit or push,
  no tampering with `.git/hooks`. (Cardinal Rule 8: `python ~/scripts/secret_scan.py --staged`, exit 1 blocks)
  — a lane did this once and merged to main with the scan disarmed. If a hook is failing, fix the underlying
  issue or report BLOCKED; do not turn the gate off.
- **PROCESS KILLS: a lane may stop only a process it identified by PID from its own CommandLine**
  (e.g. a looping run-watchdog instance it spawned). Never `taskkill /IM bash.exe`, never `/T` on a
  shell you did not spawn, never `Get-Process bash | Stop-Process` — an untargeted kill took down
  the orchestrator's monitors three times in one day.
- **MONITORS are orchestrator-only.** Lanes finish in the foreground; a lane never starts a monitor,
  watcher, or standing loop of its own.
- **BACKGROUND RUNS are denied by hook.** A lane runs to completion in the foreground — no
  `run_in_background`, no detached/nohup process; backgrounding is not a lane capability.
- **Hook probes never write the real resistance ledger.** Any manual run of a brain hook outside its test file sets `RESISTANCE_LEDGER=<temp file under the lane's worktree parent>` first (the override the hooks read, e.g. `main-context-ceiling.mjs`), so no row reaches `~/.claude/state/resistance-ledger.jsonl`. A probe that wrote the real ledger is reported in the hand-back.

## 7. Definition of DONE — work that is not pushed does not exist
- **A lane is not finished when the code is written and the bar is green. It is finished when the
  branch is PUSHED and a PR is OPEN, and the report names the PR number.** Work sitting in a local
  worktree is undelivered: the next lane rebases past it, the train never sees it, and it is lost.
  *(A lane replaced a gate bug, proved it, and then never pushed. The work had to be recovered by
  a second lane.)*
- **Never work on a DETACHED HEAD.** That is the mechanical reason work gets stranded: a detached
  worktree has no branch to push to, so the work simply sits there and the next lane rebases past it.
  Right after creating your worktree run `git branch --show-current` and confirm it prints a branch
  name. If it is empty, create one (`git switch -c <branch>`) BEFORE writing a line of code.
- **Verify your own push.** `git ls-remote --heads origin <branch>` and `gh pr view <n> --json state`.
  Do not report a PR you did not confirm exists.
- If a push FAILS or is blocked, that is the headline of your report, not a footnote. Say what the
  error was. Never end a report implying delivery you did not achieve.
- **Resolve a PR's branch from GitHub, never from a lane's prose.** `gh pr view <n> --json
  headRefName,headRefOid` and confirm the branch tip matches `headRefOid` before handing it to the
  orchestrator. *(A merge train was handed a branch name copied from a lane's report — the lane
  had reported one name but actually opened its PR from another. The train merged the stale branch,
  every gate passed, the bar was green — and the feature was never on main at all.)*
- **A green bar is not proof a feature landed.** After merging, assert the expected files and symbols
  actually EXIST in the MERGED tree (`git ls-tree`, `git grep`) — this is exactly what the bullet above
  would have caught.
- **All four of these, or the lane FAILED — no partial credit:** the branch exists on origin; every
  gate is cited with literal output; the change is proven watched-to-fail (break it -> RED, restore -> GREEN,
  shown as literal output); the PR number and MERGED state are confirmed via `gh pr view --json state`.
  **"Remaining work: ..." in a final report means the lane FAILED** — say so plainly and stop, do not
  soften it into "mostly done".

## 8. Report format
Causal chain with `file:line` | gate output (literal) | the anti-vacuity run as literal output
| gate counts | PR number | MERGED-state proof | worktree removed | **anything you refused to decide silently**.
Lead the report with the ACCEPTANCE observable (section 8a), before the check list.

## 8a. Acceptance (done-per-user, not done-per-CI)
1. Every lane brief carries an ACCEPTANCE line: the user's verbatim ask plus the single observable the user will check (a rendered page, a screenshot, a run's output, a command's exit).
2. The closing report leads with that observable shown (artifact path, URL + status, or pasted output), not with the check list.
3. CI green, merged state and pr_merged_check are preconditions and are reported after the observable.
4. A lane that cannot produce the observable reports BLOCKED-ACCEPTANCE with what it could prove, never "done".
5. The orchestrator records the acceptance observable in STATE.md next to the PR number before calling the item complete.

## 8b. Landing (done means pushed and PR open, verified)
1. A lane that changed any file has exactly two terminal states:
   - **LANDED**: branch pushed, PR open. The report carries the PR url and the full head sha, and the
     head sha is confirmed by pasting the literal output of `git ls-remote origin <branch>`.
   - **BLOCKED-LANDING: <reason>**: the branch is still pushed if it can be, and the exact denial text
     from the failed push or gh call is pasted.
2. "Code written, not committed", "ready to commit" and "tests green locally" are not terminal states.
   A hand-back in any of those states is a contract violation.
3. A hand-back that lists a changed file must carry a PR url, or BLOCKED-LANDING with the blocker. The
   orchestrator treats any other hand-back as unlanded: the lane is killed per the header rule and a
   fresh lane is dispatched with the SAME brief plus the unlanded branch name, never a "finisher" lane
   with a different scope.
4. The lane runs its push in its own foreground Bash call (timeout 600000), never in the background. A
   background push strands the lane (memory: background-push-strands-lanes).
5. When the merge step is in scope, LANDED extends to MERGED only with the `pr_merged_check` line pasted
   verbatim.

## 8c. Shared append files
1. **Evidence.** Between 2026-10-18 14:00Z and 16:20Z, five keyrun PRs (#120, #121, #122, #124, #128) each
   needed a lane-run `git merge origin/main` solely because every lane appends one line to BUILDLOG.md and one
   row to the end of game/CLAUDE.md. A sibling that merged first made each PR DIRTY, and GitHub runs no checks
   on a DIRTY PR.
2. **Rule.** A file that every lane in a repo appends to (BUILDLOG.md, a domain CLAUDE.md row table, an index)
   is a SHARED APPEND FILE. A lane touching one must, immediately before its push, run `git fetch origin <default>`
   and `git merge origin/<default>` inside its worktree (`<default>` resolved per section 4). On a conflict in a
   shared append file, keep BOTH sides: main's lines first, the lane's line last. Then re-run
   `python ~/scripts/secret_scan.py --range origin/<default>..HEAD` and push. Appends go at the END of the file,
   never inserted mid-file.
3. **DIRTY after a sibling lands.** If `gh pr view` shows mergeStateStatus DIRTY after a sibling lands, the lane
   (or a resume of it) repeats the merge in item 2. The orchestrator never edits the branch.
4. **Not in force (PLAN item).** The durable fix is for the lane to put its BUILDLOG line and CLAUDE.md row in the
   PR body, and for the orchestrator to append them on land. Until that is built, item 2 is the rule.

## 9a. Index-first reading (knowledge tree)
1. Before any grep/rg/find over a brain repo (`~/.claude`, `~/scripts`, `~/conductor3`) or any repo with a
   generated KNOWLEDGE.md, read KNOWLEDGE.md at the root, then the one domain INDEX*.md the task names.
2. Grep is a last resort for a symbol the index does not list. The report says which index was read first.
3. Counting things an index already counts (tests per file, entries per domain) is read from the index,
   never re-grepped. (Matt 2026-10-18: "see how python-pro is grepping for test counts lol, that emphasizes
   the issue".)
4. A lane that finds the index wrong fixes the source header (one derived line) and re-runs
   `python ~/scripts/knowledge_tree.py --write` for that root. It never hand-edits INDEX*.md.

## 9b. Purpose pin (intent decay)
1. Every dispatch brief for a lane that creates or changes product code carries one line `PURPOSE: this artifact exists to <why, from the project's plan doc>; status: real|throwaway|oracle`.
2. The lane copies that line verbatim into its PR body and into any new file header or STATE entry it writes.
3. A lane whose brief has no PURPOSE line asks for it in its first message and does not start code.
4. A lane that discovers its work builds on a `throwaway` artifact as if it were `real` stops and reports (intent decay), it does not continue.

## 9. Linux shape — cross-platform test gate
- **Shell, workflow, and Node-test changes are proven on the Linux shape before push.**
  `tools/linux_shape_check.py` triggers on commits touching `*.sh`, `hooks/*`,
  `tools/run_shell_tests.sh`, `.github/workflows/*.yml`, or `tests/**/*.test.mjs` (`should_check_linux_shape`).
- Non-Windows host (`host_is_windows()` false): suites run NATIVELY (`run_native_leg`, `run_shell_tests_native`,
  `run_node_tests_native`). WSL is never probed. Any suite failure is exit 1.
- Windows host: `wsl_status()` is tri-state. `WSL_OK` runs the suites under WSL (`run_shell_tests_wsl`,
  `run_node_tests_wsl`); a failure is exit 1 and blocks push.
- Windows, `WSL_ABSENT` or `WSL_NO_DISTRO`: NOTICE printed, exit 0 (CI gate remains). With
  `AESOP_REQUIRE_LINUX_SHAPE=1` (or `--require-wsl`) it is exit 1.
- Windows, `WSL_ERROR` (the probe itself failed): exit 1, fail closed, with or without the require flag.
- Node tests run with `USERPROFILE` unset (native and WSL) to catch environment variable assumptions
  (e.g., isolated-home tripwire test failures on Windows-only CI shard #864).

## 10. Plan-vs-output check (intent decay)
1. At every project checkpoint (and before any release) the orchestrator dispatches a read-only Haiku lane that reads the project's plan/design doc goals (e.g. keyrun design/BIBLE.md, aesop docs/ROADMAP or the STATE.md intent section), the current STATE.md and the shipped artifact, and answers only: does what was built match what this was for? It does not assess whether it works.
2. The verdict is one BUILDLOG line `[intent-check] <project> MATCH|DRIFT: <one sentence>`.
3. DRIFT blocks new feature lanes in that project until the orchestrator records the correction (re-plan or re-scope) in STATE.md.
4. The check names the artifact's status (real/throwaway/oracle) from the PURPOSE pins it finds, and flags any throwaway that has accumulated more than one feature lane.
