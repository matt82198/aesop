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
  additions with nothing for git to flag (PR #745: a conflict resolution took origin/main's whole
  `tools/merge_queue.py`, dropping the PR's own `build_bisect_batches`/`parse_bisect_lineage`/
  `bisect_is_exhausted`, and CI stayed green because only the PR's own tests exercised them). After
  resolving any merge conflict, run `python tools/pr_symbol_survival_check.py --base <base> --head
  <head>` (Guardrail G13) before pushing.
- **Lanes cannot force-push (hook-blocked).** Recover a dirty PR with `git merge origin/main` into
  your branch, keeping BOTH intents (never `checkout --ours/--theirs` on code or docs), regenerate
  registered generated artifacts (`python tools/gen_tool_index.py --regenerate`), run `python
  tools/pr_symbol_survival_check.py --base origin/main --head HEAD` (0 MISSING), then a plain push.

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
  `git -C $AESOP_ROOT fetch -q origin && git -C $AESOP_ROOT worktree add $AESOP_ROOT-wt-<name> -b <branch> origin/main`.
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
- **Lanes open/update PRs and push branches. They NEVER merge.** Only the orchestrator (via `tools/auto_merge.py`)
  merges. A lane's job ends when the branch is pushed and a PR is open.
- **Merge automation: arm native auto-merge at PR open time.** After `gh pr create`, immediately run `gh pr merge <N> --auto --squash`
  to arm GitHub's native auto-merge. Never use `--admin`. A PR must never wait for a session daemon or manual merge to complete.
  Native auto-merge is the merge actor; AesopMergeQueue is disabled. To re-run required checks on an armed PR without code
  changes, use `gh api repos/<owner>/<repo>/pulls/<N>/update-branch -X PUT`; a `gh workflow run` dispatch creates a separate
  check suite that branch protection ignores.
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
- **Before pushing, run the shard for your test file.** Run `python tools/ci_shard_runner.py <n> 4` for the shard that owns your
  test file (see tests/CLAUDE.md for shard assignment). CI is confirmation of local verification, not discovery of breakage. Paste
  the shard output to your report: it proves your changes work before they hit main.
- **Merge = `python tools/auto_merge.py <n>` with the PR number. Never bare.** The primary tree is the merge tool's working tree.
- Generated whole-tree artifacts (`tools/generated_paths.py::GENERATED_PATHS`) self-heal post-merge via `.github/workflows/regen-on-main.yml` + `tools/regen_all.py`; do not hand-edit them.
- Stay inside your declared files. If the chain leaves them, **STOP and hand off** — a clean hand-off
  beats a collision and is a complete result, not a failure.
- **REARCH sections 69+ are orchestrator-reserved.** Claim an unreserved number AT WRITE TIME and
  record it; ask before taking 69+.
- **Python**, **ASCII** source.
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

## 9. Linux shape — cross-platform test gate
- **Shell, workflow, and Node-test changes are proven on the Linux shape before push**
  (detect Windows-only CI red via WSL local testing): `tools/linux_shape_check.py` runs
  shell and Node test suites under WSL when commits touch `*.sh`, `hooks/*`,
  `.github/workflows/*.yml`, or `tests/**/*.test.mjs`.
- On Windows with WSL available: tests run automatically before push; failures block push
- On Windows without WSL: NOTICE printed, exit 0 (CI gate remains); set `AESOP_REQUIRE_LINUX_SHAPE=1`
  to enforce local testing
- On non-Windows: automatically skip (native Linux tests run on CI)
- Node tests run with `USERPROFILE` unset to catch environment variable assumptions
  (e.g., isolated-home tripwire test failures on Windows-only CI shard #864)
