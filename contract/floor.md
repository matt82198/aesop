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

Provenance: Matt 2026-10-22, rows f-bcd7ff61 and f-11a046ce, ruling R-LANES-1.

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
- **Receipts.** Every push emits a signed local-run receipt automatically (`hooks/pre-push-policy.sh`
  `check_emit_receipt`). `AESOP_RECEIPT_EMIT=0` is INERT: the hook logs `receipt_emit_optout_ignored` and
  emits anyway, so never set it. A receipt-less push means the gates did not run (a precondition skip prints
  a WARN and returns 0): find and fix the cause, never bypass.
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
- Anti-vacuity proofs are two blocks: BEFORE (failure with the fix removed) and AFTER (pass with fix restored).
  The four-part pass condition for a lane that changes files (branch on origin, gates cited, watched-to-fail,
  PR MERGED) is stated once, in "7. Definition of DONE" (judgement step).

## 6. Boundaries (all lanes)
- Stay inside your declared files. If the chain leaves them, **STOP and hand off** — a clean hand-off
  beats a collision and is a complete result, not a failure.
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

## 8. Report format
Causal chain with `file:line` | gate output (literal) | the anti-vacuity run as literal output
| gate counts | PR number | MERGED-state proof | worktree removed | **anything you refused to decide silently**.
Lead the report with the ACCEPTANCE observable (section 8a), before the check list.
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

## 9b. Purpose pin (intent decay)
1. Every dispatch brief for a lane that creates or changes product code carries one line `PURPOSE: this artifact exists to <why, from the project's plan doc>; status: real|throwaway|oracle`.
2. The lane copies that line verbatim into its PR body and into any new file header or STATE entry it writes.
3. A lane whose brief has no PURPOSE line asks for it in its first message and does not start code.
4. A lane that discovers its work builds on a `throwaway` artifact as if it were `real` stops and reports (intent decay), it does not continue.
