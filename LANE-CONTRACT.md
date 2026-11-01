# LANE-CONTRACT.md — the standing contract every aesop lane is dispatched under

Derived from wow-ledger/LANE-CONTRACT.md; universal sections verbatim.

**Model: lanes are stateless processes. A lane that fails is KILLED, never corrected.**
When one fails, the question is never "how do I fix this agent" — it is **"what did this contract
fail to specify?"** Amend this file, then dispatch a FRESH lane. Corrections sent to a live agent
die with it and teach nothing; a contract amendment compounds forever. Failures are cheap when
lanes are Haiku and stateless — so fail fast, fix the contract, re-run.

**Read first:** this contract supersedes any session LANE-RULES file (for example a scratchpad `LANE-RULES-<time>.md`);
where they differ, this file wins. The shell and hook shapes are in "Shell and hook shapes (every lane, every repo)"
and the brief shape is in "The brief", both below.

Every dispatch brief should POINT here rather than restate it. Below is what was learned the
expensive way during aesop development. Each line exists because a lane failed without it.

## Ordering: write before act, one source per fact
Matt 2026-10-21, verbatim: "write before act, one source per fact." The failures are missing linearization, not bad
actors: each component acts on its own snapshot of the truth, and a task that crosses several snapshots fails.
Four rules:

1. **Intent becomes a write first.** When intent changes, it lands in this file (or the repo CLAUDE.md row) before any
   dispatch acts on it. No dispatch runs against a rule this file does not state yet. Shape: a ruling is an Edit to this
   file in a worktree PR; until that PR is on origin/main, the brief quotes the exact text and says it is pending.
2. **One source per fact.** A PR head comes from `git ls-remote origin refs/heads/<branch>`, not from gh; gh is used for
   check state on that sha and for MERGED. A live hook comes from the ~/.claude checkout
   (`git -C C:/Users/Mattt/.claude rev-parse HEAD`), not from a merged PR.
3. **Arm last.** Auto-merge goes on only after the final push to a branch. Nothing pushes to an armed branch. Shape: the
   arm is the last command of a lane (section 6). A lane that must push after arming runs
   `gh pr merge <N> --repo <o/r> --disable-auto` on its own arm, pushes, then re-arms with
   `gh pr merge <N> --repo <o/r> --squash --auto --match-head-commit <new sha>`; or it uses a new branch.
   An arm Matt enabled is never disarmed.
4. **No hidden local state at a compaction boundary.** Either everything is pushed, or the checkpoint and the compaction
   summary name the unpushed shas by branch. Shape: `Unpushed: <branch> <sha>` lines, or the single line `Unpushed: none`.

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
- **The red for a new pure symbol is taken on a stub.** When the red test references a symbol that does not exist
  on main, the test cannot compile on main. The lane declares the symbol on its branch as a stub whose body returns the
  wrong answer, records the red output with the assertion failing, then implements. A compile error is not a red test.
  *Why: a compile error shows the symbol is missing, not that the assertion checks behaviour, so the receipt must show the assertion failing.*
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
- When a brief's behaviour already exists on main, pin it with a test and prove non-vacuity by a production mutant instead of a red-first (discovery 2026-10-21).

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
- **On receipt-gated repos (aesop), never update a branch with GitHub's update-branch.** That covers `gh api .../update-branch` and `gh pr update-branch`: the merge commit is authored by GitHub and no box ran the gates on it, so it carries no receipt. Merge `origin/HEAD` in your own worktree and push, so the pre-push hook emits the receipt; a no-op push also emits one if the head is already current. Keyrun has no receipt check, so update-branch is fine there.
  On matt82198/aesop the orchestrator never calls update-branch for an aesop PR either. A behind-main aesop PR is brought forward by its lane: `git fetch origin && git merge origin/main` in its own worktree, resolve conflicts three-way (see the bullet above on merge conflicts), re-run the gates, then push. Memory: aesop-prs-never-update-branch (PR #1268).
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
- **Worktree path collision: stop and hand off.** If the worktree path already exists, or the branch is already checked
  out in another worktree, the lane stops and hands off naming the occupant (its `git worktree list` line). It never
  removes, reuses or re-targets the occupant and never picks a different path on its own; the orchestrator names a path
  that `git worktree list` shows free. *Why: 2026-10-21 18:16Z keyrun-wt/pair-marks was occupied by an older branch, and the stop was correct.*
- **Never git stash.** The stash is shared across worktrees; concurrent agents cross-contaminate WIP.
  Use `git diff > patch.diff && git checkout . && git apply patch.diff` instead.
- **AESOP_STATE_ROOT**: all state files (heartbeat, logs, audit trail) use `$AESOP_STATE_ROOT` env var
  (default `$AESOP_ROOT/state`). No hardcoded personal paths. For worktree work, export it explicitly.
- **Pre-push gates run automatically on every push.** `python tools/secret_scan.py --staged` blocks
  if secrets are detected. `python tools/encoding_lint.py --check` blocks subprocess calls without
  `encoding='utf-8'`. Never use `--no-verify` to skip (forbidden in every dispatch). If a gate fails,
  FIX IT, do not bypass it.
- **Secret scan follows the index state.** Before staging new changes, scan with `--staged`. After any merge-forward commit (`git merge origin/main`), or whenever the index is empty, run exactly `python ~/scripts/secret_scan.py --range origin/main..HEAD` and require exit 0. The `REFUSED: --staged scanned 0 files` refusal on an empty index is correct fail-closed behaviour: never retry `--staged` on an empty index. *(S4 ship lanes retried this refusal after a merge-forward, finding f-dc6f2294.)*
- Pushes emit a signed local-run receipt automatically; a push without one means the gates did not
  run — fix, don't bypass.
- **Every Python subprocess call needs explicit `encoding='utf-8', errors='replace'`.** The Windows
  default is cp1252, which corrupts UTF-8 output and has crashed production processes. Every
  subprocess.run/check_output/Popen that reads output: `encoding='utf-8', errors='replace'` not
  `text=True`.
- **Lanes may emit a local receipt after their full run:** `python tools/emit_receipt.py --post` (signs what ran, posts it on the head sha; the hosted `verify-receipt` check recomputes the tree hash and is NON-required during the measurement period -- see docs/archive/RECEIPT-GATE.md).
- **Run the ACTUAL CI gate, not a proxy.** `npm run test:py` != a hand-written pytest call; the real
  test count is what CI reports. Verify each gate actually runs: check CI output for the expected counts
  and pass/fail status, never assume "green" means "verified". Partial verification is NOT a pass.
- **Resolve env vars and paths at dispatch time, never hardcode.** Use `$AESOP_ROOT`, `$HOME`, and
  `sys.executable`; never `/c/Users/matt8` or `python3` or machine-specific paths.
- **Dispatch-ledger retro.** The dispatch-ledger retro lives at `C:/Users/Mattt/scripts-wt/ledger-retro/dispatch_ledger.py` (claude-scripts #137).
- `git -C` takes a literal path, never a shell variable, and commit and push are always separate commands (discovery 2026-10-21).
- A commit in claude-scripts needs `python ~/scripts/knowledge_tree.py --write --root <wt>` first, and a `tools/*.js` or `tools/*.py` change in aesop needs a tools/CLAUDE.md entry plus `python tools/gen_tool_index.py --regenerate`, pushed with the documented generated-artifact shape (discovery 2026-10-21).
- pwsh is not on the Bash PATH on this box, so keyrun's game gate runs through powershell per keyrun/LANE-CONTRACT.md (its command is not repeated here), and that gate skips tools/Keyrun.Playtest.Tests, so run that project directly when touching it (discovery 2026-10-21).
- Keyrun content lives in two trees, game/content/data (layout, forces, enemies/*.json) and repo-root content/data/dungeons, the sim is under game/sim/Keyrun.Sim, and a Godot-bound file (`using Godot;`) is never compiled by a test project, so a view-model seam goes in a Godot-free file linked into Keyrun.Sim.Tests.csproj (discovery 2026-10-21).
- A hook change in claude-config needs an evidence commit with a Hook-Proof trailer in exactly this shape: `Hook-Proof: hooks/<name>.test.mjs red=N/M green=M/M`, where N/M is the failing count on the red run before the fix and M/M the green run after. The `hooks/` prefix and both counts are required; `Hook-Proof: <name>.test.mjs ...` without the prefix is rejected by the pre-push hook. The base branch there is master (discovery 2026-10-21).
- A file listed in claude-scripts' LOOP-MANIFEST.json needs the same kind of evidence commit with `Loop-Proof: tests/<file>.py red=N/M green=M/M`, in the same red-before, green-after form.

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
  - **Mode 1 - repos WITH required CI checks (aesop, keyrun).** The lane makes its final push, opens or readies the PR,
    and ARMS native auto-merge only AFTER that final push (Ordering rule 3), never before it and never on a draft,
    pinned to the tested head:
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
    3. Arm auto-merge after the final push, as the last command: `gh pr merge <N> --repo <o/r> --squash --auto --match-head-commit <full head sha>`.
    Draft state is never an excuse to skip arming once the PR is ready.
  - **`--auto` means "merge when requirements are met".** With no required checks pending it merges immediately at arm
    time. So arm only after `gh pr ready` and the head sha is the final diff; `--match-head-commit <full head sha>` is
    mandatory; never arm a draft. If a repo has no required checks, arming equals merging, and the lane states that in its report.
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
- **Gate-change PRs are never armed by the lane.** A gate-change PR (anything under `.github/workflows`, a tool that CI runs, a branch-protection-named job, or a hook file) is armed by the orchestrator only after a read-only lane has diffed the head against the proof shape: each required name posts exactly one check run that actually runs the step; docs-only pass-through proven by a check-run listing; no job-level `if:` on a required-named job (game-gate's `if: always()` with `needs:` is the one allowed form); no second workflow reusing a required name; no `paths:` or `paths-ignore:` filter; no `continue-on-error`; no widened tolerance; scope classified with `git diff --no-renames --name-only`. The orchestrator reads that verdict before arming. A required check that is absent or in progress is a wait, never a PASS.
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
- Pushes run as their own Bash command with a 600000 ms timeout and are never backgrounded (discovery 2026-10-21).
- The ONE sanctioned wait for a lane is `gh pr checks <N> --repo <owner/repo> --watch` or `gh run watch <id>` as a single foreground command with a 600000 ms timeout, and Monitor and sleep loops stay denied to lanes (discovery 2026-10-21).
- **Hook probes never write the real resistance ledger.** Any manual run of a brain hook outside its test file sets `RESISTANCE_LEDGER=<temp file under the lane's worktree parent>` first (the override the hooks read, e.g. `main-context-ceiling.mjs`), so no row reaches `~/.claude/state/resistance-ledger.jsonl`. A probe that wrote the real ledger is reported in the hand-back.
- Pushing to a PR with auto-merge armed means pushing first and then re-arming with `gh pr merge <N> --repo <o/r> --squash --auto --match-head-commit <full head sha>` (discovery 2026-10-21).
- A stacked PR (base not the default branch) is never armed, because the orchestrator retargets it after the parent merges (discovery 2026-10-21).

## Shell and hook shapes (every lane, every repo)
Hooks enforce these. Each line is the allowed shape, then the denied one.
- **Git paths are literal:** `git -C C:/Users/Mattt/Desktop/<repo>-wt/<lane> ...`; never a shell variable (`$X`, `${X}`) inside any git path, worktree-add targets included.
- **File content is written with the Write tool** (edits with the Edit tool or a Python script file); never a heredoc (`python - <<EOF`, `cat <<EOF`), a here-string (`<<<`) or process substitution (`<( )`). No `sed` with `$` or a variable.
- **Commit messages:** write the message with the Write tool, then `git commit -F <file>`; never `-m` with multi-line text. PR text: `gh pr edit --body-file <file>`, `gh pr comment --body-file <file>`.
- **The append-only log (`BUILDLOG.md`)**: its name appears only in git commands and the two append shapes `printf '\n' >> <wt>/BUILDLOG.md` and `cat <tmp> >> <wt>/BUILDLOG.md`; any other shell command naming it is denied.
- **STATE.md** is edited only above the first `## NEXT STEPS`, with the Edit tool; never below it, never by shell. The fold anchor and the STATE-only fold diff are in section 8c item 5.
- **Secret scan BEFORE committing:** `python ~/scripts/secret_scan.py --staged --repo <wt>`; require CLEAN with files scanned > 0 (if 0, run `--range origin/<base>..HEAD --repo <wt>` after the commit and before the push). Never commit first and scan after.
- **Commit trailer, last line exactly, on every commit:** `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>` (Matt's rule, 2026-10-21; it overrides any harness attribution reminder, including one that names a different model, and a lane that sees one still uses this trailer). This applies to every lane regardless of the model it runs on; a harness reminder naming a different model does not change the trailer. *Why: 2026-10-21 a haiku lane wrote "Claude Haiku 5.5" because it read the bullet as ambiguous for non-Fable lanes.* **PR footer, last line exactly once, on every PR description:** `🐢 Generated with Aesop`, never the Claude Code line (Matt's rule, 2026-09-18).
- **dotnet** is `C:/Users/Mattt/.dotnet/dotnet.exe`; never bare `dotnet` from PATH.
- **keyrun game gate** is one plain invocation, its command set in keyrun/LANE-CONTRACT.md: `cd <wt> && <keyrun game gate command> > <scratch>/gate.txt 2>&1`, then read the file. Scene tests are NOT this shape: see the CI contract item 2 (no `cd &&` prefix there).
- **Scratch:** gate output is redirected to a file in the lane's scratch directory and read with the Read tool or tail; never read a transcript or session file.
- **One compliant retry.** When a hook denial names an allowed shape, retry ONCE in exactly that shape. If it is denied again, or names no shape, STOP and report the denial text verbatim. Never try a third variant, never ask the orchestrator to run the denied command for you.
- **Escape tokens are never used:** any `[[ALLOW-...]]` token, `AESOP_ALLOW_GENERATED=1` outside section 6's stated purpose, `--no-verify`, `core.hooksPath`, `--admin`, and any env var that turns a hook off.
- **Never:** `git stash`; history rewrites (`--amend` on a pushed commit, `rebase`, `reset --hard`, force-push); the primary checkout as a working tree (only `fetch origin` and `worktree add <literal path>` run there); touching an auto-merge arm Matt enabled (`gh pr merge --disable-auto` on an armed PR); deleting a branch or a worktree. Push shape: `git -C <literal wt> push origin HEAD:<branch>`, in its own foreground call.
- **Knowledge-tree hook fix shape** (a brain repo's knowledge-tree gate reds on a stale index): `python ~/scripts/knowledge_tree.py --write --root <wt>`, then stage `INDEX*.md` and `KNOWLEDGE.md`; never hand-edit them (section 9a).

### Hook-safe shapes (read before the first shell command)
1. Clock: `python C:/Users/Mattt/scripts-wt/utc-now/utc_now.py` from Bash at the lane's start and end (its `--local` flag prints Chicago time). Never a PowerShell expression, and never `~` in the clock command; any other script is named by its literal `C:/Users/Mattt/scripts/...` path. A denied clock read is recorded as "not captured" and never stops the lane.
2. Code reads: the Glob tool to locate files, the Read tool to read them, the Edit or Write tool to change them. In shell: never `sed`, `awk` or `grep` on a game path; never `$(...)` command substitution; `sed`/`awk` programs, where a gate needs them, are one single-quoted token on a literal path. PowerShell only for a named gate script.
3. Git paths: literal absolute paths with forward slashes in every git argument, worktree-add targets included; never a shell variable, never `$HOME`, never `~`.
4. Denials: a denial whose text names an allowed shape gets exactly one retry in that shape; any other denial means stop, record the text verbatim, hand back.
5. Push: the secret scan runs `--staged` before the commit and must print CLEAN with a count above zero; the push is its own command, never chained with `;` or `&&` after a scan or a test.
6. Branch base: cut from origin/main unless the brief names another base; a branch cut from an open PR's head makes a stacked PR, which is not armed.
7. Scratch: every gate's output, commit message, PR body and probe script goes to a file under the session scratchpad folder named in the brief, and is read back with the Read tool; the hand-back names each file by its complete absolute path.
8. Worktree npm and npx: `cd` does not persist across tool calls, so run vitest and npx as one command, `cd <wt> && npx vitest run ...`, and in a fresh worktree run `cd <wt> && npm ci --no-audit --no-fund` first. The scene-test exception in the CI contract (item 2, no `cd &&` prefix) still applies to scene runs. This shape is for npm, npx and vitest only; scene tests keep their no-`cd &&` shape, and the two rules do not conflict. *Why: 2026-10-21 two false vitest failures came from running in the wrong cwd.*

## The brief
A dispatch brief carries exactly these, and nothing else (everything else is this contract and is not repeated):
1. The PURPOSE line: `PURPOSE: this artifact exists to <one line>; status: real|throwaway|oracle` (section 9b).
2. The model, and why.
3. The acceptance line: one sentence verifiable in one run or one test (section 8a).
4. The worktree path, the branch name, and the scratch folder (the session scratchpad subfolder the lane writes its temporary files to; see Hook-safe shape 7).
5. The files or domain-map rows the lane owns.
6. The ruling ids it serves.
7. The CI jobs it can affect (CI contract item 1).
8. The hand-back block (section 8): the fields the orchestrator wants back.
A brief that restates shell, hook, push, merge or scan rules is carrying contract text: delete it and point here. A lane
that finds a rule missing from the contract reports "what did the contract not tell you" in its hand-back; the contract is amended, not the brief.

## CI contract (keyrun)
Applies to every lane that pushes to `matt82198/keyrun`. Sources: `game.yml@main` and `tests.yml@main` read
via `gh api repos/matt82198/keyrun/contents/.github/workflows/<f>?ref=main`; keyrun's game gate script (named in keyrun/LANE-CONTRACT.md); keyrun
`CLAUDE.md`, `game/CLAUDE.md`. Line numbers are `game.yml@main` unless stated. Measured 2026-10-21.

**1. Jobs.** Required on main (`gh api repos/matt82198/keyrun/branches/main/protection --jq
'.required_status_checks.contexts'` -> `["test","e2e","game-test","game-gate","game-e2e"]`). Every push to a PR
head re-runs both workflows (`on: pull_request`, tests.yml:3-5, game.yml:5-8); `cancel-in-progress` cancels the
older run (game.yml:18-20). Wall times from `gh api repos/matt82198/keyrun/actions/runs/<id>/jobs --jq '.jobs[]
| "\(.name) \(.started_at) \(.completed_at)"'`, last two green main runs (Game 39605588895 @0f6a8989, Game
39602147154 @6ce74317; Tests 39605588877, 39602147012):

| Job | Runner / timeout | Runs | Wall (run 1, run 2) |
|---|---|---|---|
| `test` (Tests) | ubuntu-latest, no timeout set | Node 24: `npm ci`, `npm run typecheck`, `npm test`, `npm run build` (tests.yml:10-20) | 35s, 43s |
| `e2e` (Tests) | ubuntu-latest, no timeout set | `npm ci`, playwright chromium, `python tools/ci/e2e_wait_ratchet.py`, `npm run test:e2e` (tests.yml:21-31) | 68s, 79s |
| `game-test` | windows-latest, 20 min (:24-25) | `dotnet test game/Keyrun.sln` on 8.0.x (:31-33), then `launch-lock-test.ps1` stub (:35-44, must print `launch-lock-test: PASS`) | 3m38s, 4m16s |
| `game-e2e` | windows-latest, 30 min (:47), per-launch cap `KEYRUN_GODOT_TIMEOUT_SEC=660` (:51), `GODOT_VERSION` 4.7.2 (:14) | Godot download (SHA512-checked, cached) -> `--build-solutions` (:92-97, step timeout 12) -> 9 scene tests -> boot smoke -> export (:218-223, step timeout 12) -> exported-exe smoke -> `E2eAssert.ps1` (:264-266, step timeout 5) | 12m32s, 12m42s |
| `game-gate` | ubuntu-latest, 5 min | `tools/ci/game_gate.py`: a PR touching `game/**` or `tools/godot/**` needs `game-e2e` success; needs both game jobs, `if: always()` | 8s, 5s |
| `balance` (non-required) | not measured here; see keyrun `game.yml` | Fork parity: `Bots.Tests` Category=Balance. Reported, never gates a merge (paragraph below). | not measured |

**balance is reported, not gated.** The `balance` job is not a required check: a red balance never blocks a merge and is never disarmed over. The standing finding (chanter.cadence groupSong dominated by swift; warden.bulwark fortress dominated by mobile) is carried in the checkpoint, not fixed by a lane without a ruling. `dotnet test game/Keyrun.sln` exits 1 while balance is red, so a lane runs the test projects individually (Content, Sim, Bots, Playtest) or reports that sln exit as the balance red.

Whole Game workflow wall: 12m57s and 14m18s (run `createdAt`->`updatedAt`, `gh run list --repo matt82198/keyrun
--branch main --limit 8 --json databaseId,name,conclusion,createdAt,updatedAt,headSha`). Budget a push at about
15 minutes of CI. game.yml lines 3-4 call game-test/game-e2e non-required; the protection query above is the
truth: they ARE required. `gh api repos/matt82198/keyrun/actions/jobs/119240827516 --jq '.steps[]...'` (game-e2e of
run 1) shows where the time goes: HUD scene test 5m32s, Run-end bonus 1m11s, Nameplate declutter 1m08s, Combat
text overlap 55s, `E2eAssert` 52s, `--build-solutions` 50s, export 37s, the other scene tests 3-17s each. A change
that adds ticks, frames or launches to a scene test raises its row; state it (item 6).

**2. Local reproduction before a push** (cd to your worktree; redirect output to scratch, read the summary line):
- tests.yml `test`: in a fresh worktree (no node_modules is carried) run `npm ci --no-audit --no-fund` first, before any vitest run; then `npm run typecheck && npm test && npm run build` (`npx vitest run` = `npm test`).
  `e2e`: `npm run test:e2e` after `npx playwright install chromium` plus `python tools/ci/e2e_wait_ratchet.py`.
- game-test: the keyrun game gate in all-projects mode, command in keyrun/LANE-CONTRACT.md (builds the sln with
  warnings as errors and the Keyrun.Sim banned-API analyzer, then the three xUnit projects; prints
  its PASS line `<gate>: PASS in <n>s`; without all-projects mode it maps the diff to projects and an unmapped path selects all,
  keyrun's gate script lines 33-53,98-116, per keyrun/LANE-CONTRACT.md), then the CI command itself: `C:/Users/Mattt/.dotnet/dotnet.exe test game/Keyrun.sln --nologo`.
  The keyrun game gate is the per-step loop and replaces neither `dotnet test game/Keyrun.sln` nor CI (its script lines 15-16). While `balance` is red, `dotnet test game/Keyrun.sln` exits 1 (item 1): a lane runs the test projects individually (Content, Sim, Bots, Playtest) or reports that sln exit as the balance red, never as its own failure.
- game-e2e scene tests (each needs Godot, runs through the launch lock, prints `<Name>: PASS`; script order is
  the CI order): `game/src/Host/SimDriverSceneTest.ps1` (:99), `HudSceneTest.ps1` (:109), `CombatViewSceneTest.ps1`
  (:119); `game/src/Hud/ScreenSceneTest.ps1` (:129), `ProgressionSaveSceneTest.ps1` (:139),
  `RunEndBonusSceneTest.ps1` (:149), `NameplateDeclutterTest.ps1` (:159), `HelpHintSceneTest.ps1` (:169),
  `CombatTextOverlapTest.ps1` (:179); then boot smoke (:189), export (:218) and `game/src/Host/E2eAssert.ps1` (:264).
  A lane runs the scene tests its change can affect (which file under `game/src/**`, `game/scenes/**`,
  `game/content/**` or the sim feeds which test), one at a time, in the shape claude-config #233 allows (reason
  `scene-test-run`, serialised by the launch lock): ONE `pwsh -File` (or `powershell -File`) of a
  `game/src/**/*SceneTest.ps1` or `*Test.ps1` with plain literal args and an optional redirect into the temp dir, e.g.
  `pwsh -File game/src/Host/HudSceneTest.ps1 > C:/Users/Mattt/AppData/Local/Temp/<lane>-Hud.txt 2>&1` (the script path is
  RELATIVE to the worktree root; an absolute script path or a `*>` redirect is denied by #233's parser, which accepts only
  `>`, `>>`, `1>`, `2>`, `2>&1` into an absolute path under the OS temp dir). Get the worktree root as the shell's working
  directory with a lone `cd C:/Users/Mattt/Desktop/keyrun-wt/<lane>` in its own earlier call (the directory persists
  between calls), never as a `cd &&` prefix; no quotes, no `&`, no `$`, no pipe or chain. The lane reads
  the output file and pastes the `<X>SceneTest: PASS` lines in the hand-back. A fresh worktree needs
  `tools/godot/setup-worktree.ps1` (build plus one-time import) first once keyrun #472 lands; until then the build
  and import commands keyrun `game/CLAUDE.md` names. Captures (`screens.ps1`), raw Godot launches and `--autorun`
  outside a scene script stay the orchestrator's. Capture shape: the `screens.ps1` run is one unchained PowerShell command; its OutDir folder is created in a separate Bash `mkdir -p` call first, never chained with `;`. *Why: 2026-10-21 afternoon the no-scrollback hook refused a chained New-Item + screens.ps1 line.* If the hook still denies a scene run, follow "one compliant retry"
  in "Shell and hook shapes"; the hand-back then lists the scene steps not run and why.

**3. One push per green local state.** A push restarts the whole run from blank (new head sha, both workflows,
the older run cancelled, game.yml:18-20); every job above re-queues and the ~15 min starts over. A fold, a
"fix up" or an unrelated commit pushed to an open PR costs a full cycle and invalidates any
`--match-head-commit` arm. So: finish the change, run every item-2 command that applies, paste the summary lines,
push once. Never push to "see what CI says"; CI is confirmation, not discovery. If the gates cannot all run
locally, the push is still one push and the hand-back names which job was not reproduced and why.

**4. Goldens and pinned fixtures (never loosened, never hand-edited).** These compare against committed
recordings or pinned counts, so a sim, content or host change can redden them with no code error:
- `game/fixtures/input/example-enter-ping.input-log.json` (HUD scene test step K1: the recorded input must equal
  the committed file byte for byte; HudSceneTest.ps1:171-181) and `example-yaw-walk.input-log.json` (same,
  :189-194; the one `startHeading` header line is ignored in the K1 comparison, :217).
- `$ReplayTickDigest = 'b950474eff2d9967'` (E2eAssert.ps1:63, checked :270) and the `game/sim/Keyrun.Sim.Tests/Fixtures/oracle/` traces + manifest
  hash: recordings of the C# sim (game/CLAUDE.md:41-50). Re-record only with
  `ORACLE_WRITE=1 npx vitest run tests/oracle` (ORACLE.md:14) and a stated reason next to the new value.
- `game/sim/Keyrun.Sim.Tests/Encounter/EncounterTraceTests.cs` pins record counts (`Assert.Equal(600, t.Records)`
  :239, `400` :249); `game/bots/Keyrun.Bots.Tests/ForkParityTests.cs` pins fork parity margins (read the file
  for the constants before touching bots, sim rules or talent variants).
Rule: a changed golden is RE-DERIVED by a real run of the producer, and the hand-back states `old -> new` and why
the rule change makes it so. Editing a number, margin, tick or seed until the test passes is a fitted green
(section 3) and the lane FAILED. A failing golden you did not intend to move is a finding: stop and report.

**5. Reading a failure** (foreground, one call each, output to a file):
- `gh pr checks <N> --repo matt82198/keyrun --json name,bucket,link` -> which job, then the job id from the link.
- `gh run view --repo matt82198/keyrun --job <id> --log-failed > <scratch>/fail.txt`, then read the tail.
- per-step timing: `gh api repos/matt82198/keyrun/actions/jobs/<id> --jq '.steps[] | "\(.name) \(.conclusion)
  \(.started_at) \(.completed_at)"'`. A step far over the table in item 1 (or a step that hit its 12/5 min
  timeout) is a hang or a budget breach, not a flake; a 660s launch cap kill reads the same.
- Scene-test output shape: asserted lines first, then one terminal line `<Name>SceneTest: PASS` or
  `<Name>SceneTest: FAIL (n)` (n = failed assertions). The workflow throws if the exit code is nonzero or the
  line `(?m)^<Name>: PASS` is missing (e.g. game.yml:99-107), so read the `FAIL` lines directly above the
  terminal line, not the PowerShell stack. `SMOKE ok` and `E2eAssert: PASS` follow the same rule.
- "Pre-existing flake" is a claim (section 1): attach a rerun and the main run id that shows the same step green.

**6. Hand-back must state about CI:** (a) the local gate lines verbatim (the keyrun gate's `PASS in <n>s` line, dotnet
`Passed!`/`Failed!` totals per project, vitest `Test Files`/`Tests` lines, each scene test's PASS line if run, or
the not-run list with reasons from item 2); (b) the expected `game-e2e` wall time if a scene budget (ticks,
frames, launches, timeout) changed, as old -> new; (c) the PR head sha (`gh pr view <N> --repo matt82198/keyrun
--json headRefOid`) that the `--match-head-commit` arm uses; any push after that sha restarts item 1.

**7. Lane row files (`game/lanes/`).** Every keyrun lane writes its game/lanes row file (`game/lanes/README.md`): exactly two non-empty lines, a lane bullet then a BUILDLOG line, never a PURPOSE line. A PURPOSE line belongs in the brief and the PR body. `tests/lanes/row-files.test.ts` enforces it in the required `test` job. *Why: the hedge made one lane ask.*

**8. Enemy content tree.** Enemy telegraphs, abilities and layout live in the repo-root content tree, `content/data/enemies/*.json`, embedded by `Keyrun.Content.csproj`. `game/content/data` is a different tree. Briefs and lanes name the repo-root path.

**9. Capture log writer.** Only `Driver.HostLine` reaches `demo.log`; `GD.Print` and Console output never do. Any log receipt a capture must show is emitted through `Driver.HostLine`. The view's ANIM/VIEW channel (`GD.Print` in `game/src/View`, e.g. ActorView's flash print) never reaches `demo.log` either; any receipt a capture must show is emitted through `Driver.HostLine` or the sim EVENT list (Run.cs emit list -> SimDriver -> RunLog). *Why: #488 found a `GD.Print` receipt would never appear in the capture log, and a recon could not see the hit flash in the log because it was a `GD.Print`.*

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
- **A hand-back without the required discovery-cost fields (section 8) is not DONE.** The lane is relaunched with the
  same brief.

## 8. Report format
Causal chain with `file:line` | gate output (literal) | the anti-vacuity run as literal output
| gate counts | PR number | MERGED-state proof | worktree removed | **anything you refused to decide silently**.
Lead the report with the ACCEPTANCE observable (section 8a), before the check list.
Recon lanes return their findings in the hand-back message, never as RECON.md or any other report file in the repo (an operator rule denies such writes).
A keyrun lane's report also carries the CI-contract hand-back (item 6 of "CI contract (keyrun)"): local gate lines verbatim, expected `game-e2e` wall time if a scene budget changed, and the PR head sha the arm matches.

### Required discovery-cost fields (every hand-back)
Every hand-back carries all of these fields, in this order:
- **Wall time:** start and end clock reads, UTC (from `python C:/Users/Mattt/scripts-wt/utc-now/utc_now.py`).
- **Validation attempts:** count of local gate runs before the final one.
- **First run passed:** yes or no.
- **Discovery:** "What did you have to figure out that the contract didn't tell you?" One line per item, or `nothing`. This answer is consequence-free: it never triggers a retro, never counts toward any threshold or ledger score, and never blocks or passes the hand-back or the PR. It is read only later, across many hand-backs, to find contract sentences that are missing. `nothing` is a fine answer.
- **Failures:** one line per validation failure, tagged `{code, environment-or-CI, flaky, contract-ambiguous}`, with the failing line.
- **Hook denials:** each hook denial verbatim, or `none`.
- **Unpushed:** `Unpushed: none`, or one `Unpushed: <branch> <sha>` line per unpushed branch (Ordering rule 4).
A hand-back missing a field is relaunched, not sent a SendMessage. Two `environment-or-CI` or `contract-ambiguous` tags
across lanes mean a contract section is written before the next dispatch in that area.

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
   verbatim, from `python C:/Users/Mattt/scripts/pr_merged_check.py --repo <owner/repo> --pr <N>`.

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
5. **Fold anchor and fold verification.** A checkpoint block folds into STATE.md directly above the FIRST line that begins `## NEXT STEPS` (keyrun STATE.md has two such headings; the first is the anchor), with no blank separator line. The fold verifier takes the STATE-only diff, `git -C <wt> diff -- STATE.md`, never a whole-tree diff: git lists BUILDLOG.md before STATE.md, so a flat added-line list fails on a multi-file diff. BUILDLOG.md is appended only through `printf '\n' >> <wt>/BUILDLOG.md` then `cat <block> >> <wt>/BUILDLOG.md`, and its numstat must be block lines + 1 added, 0 deleted (verified separately from the STATE diff). When a STATE.md has more than one `## NEXT STEPS` heading, the fold block goes directly above the FIRST one with no blank separator, and the lane's hand-back states the anchor line number it folded above. *Why: `verify_1220b.py` does not check which heading was used, so the rule has to be written and the hand-back has to show it (keyrun STATE.md has two headings, at lines 726 and 1569 at 0e560876).*

## 8d. Receipt first (push early, draft PR early)
1. A build lane pushes its branch and opens a draft PR within 20 minutes of its first commit, before any long test run. The draft PR is the receipt; its body says what is folded in and what is still red.
2. Every later push happens at least every 45 minutes while the lane is working, or at every green step, whichever is first.
3. A lane that folds other branches and reverts one of its own merges says so in the PR body at the time of the revert, with the reason, and pushes.
4. A branch with no origin ref 30 minutes after its first commit is a contract breach the monitor flags as [CHURN-NO-SHIP]; the orchestrator stops and relaunches that lane.

Why: 2026-10-20 integrate/playable-dungeon reached 154 commits over 15 hours with no push and three self-reverts before anyone noticed.

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

## 8e. CI and gate failures: the catalog is the lookup
- The error line from a red CI log is a lookup key in `docs/CI-CATALOG.md`. Find the matching row and apply its recipe ONCE.
- A second identical failure is a hand-back, not a retry. Report the row id and the run id.
- Commands in a recipe use literal absolute paths, and every command must too (shell variables in paths are denied by the worktree gate).
- A signature with no catalog row is a new category: read the first `not ok` or `FAILED` line in the full job log, and report it. Do not retry it.

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

## Stage lanes (G5)
1. A change reaches CI through a pipeline of stage lanes, never one lane. Each stage is its own dispatch.
2. Every brief carries one line `STAGE: S1 SPEC | S2 IMPL | S3 GATE | S4 SHIP | S5 REVIEW` (the one stage it runs) and one `CATEGORY:` line. A brief that names more than one stage is denied.
3. Per stage, one line each, with input artifact, output artifact and forbidden verbs:
   - S1 SPEC: in a row or ruling; out the failing test path and its red output; forbidden: any non-test edit, commit, push.
   - S2 IMPL: in the S1 test path and red output; out a diff file, no commits; forbidden: editing the test, commit, push, gates other than the named test.
   - S3 GATE: in the S2 diff; out a gate log; forbidden: Edit, Write, commit, push, any --update-baseline; hands back PASS or the catalog recipe id; on any red, match the failing line to its row in docs/CI-CATALOG.md by path before any retry. Gate command list, run each that applies: `node --test <the named test files>` (for scaffold tests add `--import ./tests/helpers/isolated-env.mjs --test-force-exit --test-timeout=60000`); `python tools/claudemd_sync_gate.py --check --base-ref origin/main`; `python tools/claudemd_lint.py --root .`; `python tools/gen_tool_index.py --check` (when tools/ changed); `python -m pyflakes <touched .py>` compared against the same files on origin/main (a warning that pre-exists on main is not red); `node --check bin/cli.js` (when bin/ changed); `python tools/encoding_lint.py --check` (G10 encoding lint, scans the whole repo, run once per S3); `python ~/scripts/secret_scan.py <touched files>`. No whole-suite discover run is part of S3; the full python suite exceeds a lane's foreground timeout and the pre-push gate runs the shards at S4.
   - S4 SHIP: in the S3 PASS and message files `<scratchpad>/lanes/<branch>/{commit-msg,pr-body}.txt` (never another branch's); out PR number, head sha, armed state; forbidden: code edits, --no-verify, update-branch, manual merge, merge train. Steps: 1 `git fetch origin main`; 2 commit the staged files with the message file: `git commit -F <msg file>`; 3 merge origin/main (three-way): `git merge origin/main`, never rebase; 4 regenerate or verify generated paths (tools/INDEX.md, docs/INCIDENTS.md); chore-commit changes; 5 secret scan `python ~/scripts/secret_scan.py --range origin/main..HEAD --repo <worktree>` (exit 1 blocks), push foreground; 6 open the PR against main; 7 arm with `gh pr merge <N> --auto --squash --match-head-commit <sha>`. On red, match failing line to its docs/CI-CATALOG.md row by path before retry. Stash is never used; a merge refusing on local changes means step 2 was skipped. `--staged` only when files are staged (empty index refuses 'scanned 0 files', fail-closed); else `--range`.
   - S5 REVIEW: in the S4 PR diff and the ruling and the bible; out a review file; forbidden: edit, commit, push, merge.
4. The orchestrator sequences stages from hand-backs. A stage starts only when the previous hand-back names its output artifact (path or sha).
5. Briefs are the filled template from docs/STAGE-CONTRACTS.md and nothing more. A lane never widens its own stage.

### S3 test-log recipe
- Fact: the harness backgrounds a Bash call at 180 s regardless of the timeout parameter. One full-suite call therefore loses its footer (0-byte or truncated log; node --test footers are lost, pytest footers survive). A lane never reruns the suite to recover it, and never uses `run_in_background` for `node --test`, `npm test` or `pytest` (a hook denies it).
- Recipe (aesop), chunked. Every call is foreground with timeout 600000 and starts with `cd <worktree> &&` because cwd does not persist between calls.
  1. List the files: `ls tests/*.test.mjs`. Pull `tests/collect-signals.test.mjs` out of the list; it runs alone (step 4).
  2. `tests/*.test.mjs run in chunks of at most 8 files`, in name order, numbered chunk 1, 2, 3 and so on.
  3. Per chunk N, one Bash call with its own log and reporter destination, and each chunk one foreground Bash call that must finish under 170 s: `cd <worktree> && python tools/test_isolation_tripwire.py -- node --import ./tests/helpers/isolated-env.mjs --test --test-reporter=spec --test-reporter-destination=.s3-chunk-N.spec.log --test-force-exit --test-timeout=60000 <chunk N files> > .s3-chunk-N.log 2>&1`. The footer is in `.s3-chunk-N.spec.log`; `.s3-chunk-N.log` holds tripwire output and stderr.
  4. `tests/collect-signals.test.mjs runs alone`. List its test names with `grep -n "test(" tests/collect-signals.test.mjs`, then split it into subsets by `--test-name-pattern` (its two long tests take 60 s and 120 s, so the 120 s test gets its own subset). Each subset is one call shaped like step 3, with its own log and destination named `.s3-collect-K.log` and `.s3-collect-K.spec.log`.
  5. Sum the footers. For every chunk and subset, read the `ℹ tests`, `ℹ pass`, `ℹ fail` and `ℹ skipped` lines from its spec log: per-chunk ℹ tests/pass/fail footers are summed and reported per chunk, then one overall total.
  6. Report per chunk: its footer counts, every failing name with its file:line (`grep -n "✖" <spec log>`), then the summed total.
  7. Rule: a chunk with no footer is a recipe defect, never a code verdict. Report it as RECIPE-DEFECT with its log path, and mark no test red or green from it.
  8. Rule: a chunk the harness backgrounds is read from its log after the harness notification; never relaunched, never polled.
  9. Delete the scratch logs (`.s3-chunk-*.log`, `.s3-collect-*.log`) only after the report is written.
- Brain (claude-config): steps 1 to 9 over `hooks/*.test.mjs`, with `cd <worktree> && node --test --test-reporter=spec --test-reporter-destination=.s3-suite.log` and no tripwire, as before. A bare directory argument is rejected by Node 24, so use the glob. The secret scan needs `--repo <worktree>`.

Provenance: Matt 2026-10-22, rows f-bcd7ff61 and f-11a046ce, ruling R-LANES-1.
