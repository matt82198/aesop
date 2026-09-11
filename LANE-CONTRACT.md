# LANE-CONTRACT.md — the standing contract every aesop lane is dispatched under

**Model: lanes are stateless processes. A lane that fails is KILLED, never corrected.**
When one fails, the question is never "how do I fix this agent" — it is **"what did this contract
fail to specify?"** Amend this file, then dispatch a FRESH lane. Corrections sent to a live agent
die with it and teach nothing; a contract amendment compounds forever. Failures are cheap when
lanes are Haiku and stateless — so fail fast, fix the contract, re-run.

Every dispatch brief should POINT here rather than restate it. Below is what was learned the
expensive way on aesop development. Each line exists because a lane failed without it.

## 1. Evidence, not narration
- **Run every command you report. Never describe what a command "would" do.** "Would fail",
  "proven by the assertion path", "test logic shows failure" are all REJECTED. Paste literal output.
  *(One lane burned 3 rounds on this before being replaced.)*
- If something cannot be run, say so and why. Do not narrate a hypothetical result.
- A claim in a report that was not executed is a defect, not a summary.

## 2. Tests
- **Failing test first (TDD).** Write a test that demonstrates the bug or missing feature before implementing a fix.
- **A test must EXERCISE BEHAVIOUR, never grep source text.** Asserting that a file *contains*
  `def foo():` — or does not contain `foo.Bar()` — is not a test: it passes on a match inside a
  comment or a string, fails on any equivalent refactor, and proves nothing about what runs.
  Drive the real code path and assert the observable result.
- **Test suites must never pollute cwd or git config.** Use `mktemp` for temp dirs, NEVER touch global
  `~/.gitconfig`. Python tests must use `encoding='utf-8'` in all `subprocess.run/Popen` calls to avoid
  Windows cp1252 corruption. Reset env vars in teardown.
- **Verify the suite is COUNTED and RUNS.** Check CI output — the test count must grow by the number
  you added, not just that yours "passed". Dormant tests that are never executed read as passed.
- **Anti-vacuity is mandatory and is proven by REMOVING the fix and RUNNING the suite** — paste the
  failure, restore, paste the pass. Not by reasoning. If removing the fix does NOT redden it, the
  test is vacuous: say so and rewrite it.

## 3. Never fit green
- **Never relax an assertion, lower a floor/ratchet, delete a suite, add a skip, or retune a constant
  to make a tree green.** If an expectation is stale, the OWNING LANE derives the correct value
  and runs the gate to measure it.
- **Never self-grant a `--@gate-allow` annotation or `--update-baseline` flag.** It makes a gate
  PASS while the rule it enforces stays broken, and the gap is invisible in review. Put the fix
  where the gate wants it. *(A lane added `--@gate-allow` to encoding_lint when the real fix was
  adding `encoding='utf-8'` to three subprocess calls.)*
- **Re-measure scalars on YOUR OWN current tree, never copy one from another branch.** Run the gate
  and read ITS number. Disjoint changes can carry the same stale counter with nothing for git to
  conflict on, and it stays silently wrong.
- **The clean merge is the dangerous one.** After every merge, re-measure every whole-tree scalar
  (gate counters, test counts, file counts) by RUNNING the gate itself — never by reading either
  side's literal. A merge with ZERO conflicts warrants MORE scalar checking, not less.

## 4. This repo's environment (do not re-derive, do not conclude "missing")
- **Worktrees: sibling only, never the primary tree.** Always create a worktree: `git -C $AESOP_ROOT fetch -q origin && git -C $AESOP_ROOT worktree add $AESOP_ROOT-wt-<lane-name> -b <branch> origin/main`. Exit the primary tree unchanged (git-ignored state/ stays clean).
- **Never git stash.** The stash is shared across worktrees; concurrent agents cross-contaminate WIP.
  Use `git diff > /tmp/x.patch && git checkout && git apply /tmp/x.patch` instead.
- **AESOP_STATE_ROOT**: all state files (heartbeat, audit logs) use `$AESOP_STATE_ROOT` env var
  (default `./state`). No hardcoded paths. For worktree work, export it explicitly.
- **Pre-push gates run automatically on every push.** `python tools/secret_scan.py --staged` blocks
  if secrets are detected. `tools/encoding_lint.py --check` blocks if subprocess calls lack `encoding='utf-8'`.
  Never use `--no-verify` to skip them (forbidden in every dispatch). If a gate fails, FIX IT, do not
  bypass it.
- **Every Python subprocess call needs explicit `encoding='utf-8'` and `errors='replace'`.** The Windows
  default is cp1252, which corrupts UTF-8 output and crashed merging. Every subprocess.run/check_output/Popen
  that reads output must have both: `encoding='utf-8', errors='replace'` not `text=True`.
- **Run the ACTUAL CI gate, not a proxy.** `pytest` != `python -m pytest -k some_test`, `luacheck .` is
  not covered by `npm run lint:lua`. Verify each test suite actually runs: check CI output for the expected
  counts, not just "the bar was green". Partial verification is NOT a pass.
- **sys.executable in tests.** Use `sys.executable` not `python` or `python3` to invoke Python in subprocesses,
  so tests run under the right interpreter on every platform.

## 4b. AESOP NATIVE FIRST — STANDING USER RULE (aesop core, 2025)

**"Read tools/CLAUDE.md and the gate's implementation before dispatching."** Before writing a gate,
scanning, or verifying behavior, read the actual gate script (`tools/encoding_lint.py`, `tools/secret_scan.py`,
`tools/verify_*.py`) to know what it does, where it reads state, and what pass/fail looks like. Matching it
is also what makes reproduction and review coherent.

**How to apply:**
- **Grep the gate implementation BEFORE writing a fix.** Sizing, thresholds, state files, error messages —
  it is all there, already defined and load-bearing. `tools/verify_test_coverage.py --check` tells you exactly
  which test files were orphaned; `tools/encoding_lint.py --check` outputs the exact subprocess call.
- **Dispatches must carry the file:line.** A brief saying "fix encoding" gets subprocess calls; a brief
  saying "tools/encoding_lint.py line X flags these calls" gets targeted fixes. **When a lane produces
  a large diff for a small gate failure, the failure is usually the ORCHESTRATOR'S at dispatch time.**
- **Prove a gate exists and runs before claiming it passed.** `verify_test_coverage.py --check`, `secret_scan.py --staged`,
  `encoding_lint.py --check` — confirm in CI output or locally (with literal paste) that the gate actually runs.
- **The mock/fake state must never grant capabilities the real gate lacks.** If behavior can't be driven
  without a capability the mock doesn't have for real, that IS the finding — skip the feature or fix the mock.

## 5. Verification & "green can mean never ran"
- **Run EVERY gate you report.** Never assume a CI job ran — a check marked PASSED might be skipped due to
  a branch condition or a stale test file (the gate never executed). Paste the literal output of each gate.
- **After every merge to main, confirm the expected files and symbols actually EXIST** on the merged tree
  via `git ls-tree` or `git grep` — do not trust that a green bar means the feature landed.
- **MERGED-state proof via GitHub API, not by reading output.** After merging, run:
  ```bash
  gh pr view <PR_NUMBER> --json state --jq '.state'
  ```
  and confirm the output is literally `MERGED`. A 200 HTTP response to a merge call is NOT proof — use
  the query to verify MERGED state on the merged tree itself.
- **All four of these are MANDATORY, or the lane FAILED:**
  1. The branch exists on origin (`git ls-remote --heads origin <branch>`).
  2. Every gate you ran is cited with literal output (paste the full line, not "it was green").
  3. Watched-to-fail proofs are pasted as two blocks: BEFORE output (failure) and AFTER output (pass).
  4. The PR number and MERGED state are confirmed via `gh pr view --json state`.
  
  **"Remaining work: ..." in a final report means the lane FAILED** — say so plainly and stop, do not
  soften it into "mostly done" or "ready for human review."

## 6. Boundaries
- **Lanes create PRs and push branches. They NEVER merge.** Merging is the orchestrator's role (via `tools/auto_merge.py`).
  A lane's job ends when the branch is pushed and a PR is open.
- **Stay inside your declared files.** If the change leaves them, **STOP and hand off** — a clean hand-off
  beats a collision and is a complete result, not a failure.
- **Dead-by-bug code must NOT be deleted — fixes wire it up.** Never delete code to hide a bug.
- **Never disable git hooks** — no `core.hooksPath` override, no `--no-verify`/`-n` on commit or push,
  no tampering with `.git/hooks`. If a hook is failing, fix the underlying issue or report BLOCKED; do not
  turn the gate off. (Cardinal Rule 8: secret_scan is non-negotiable.)

## 7. Definition of DONE — work that is not pushed does not exist
- **A lane is not finished when the code is written and the bar is green. It is finished when the
  branch is PUSHED and a PR is OPEN, and the report names the PR number.** Work sitting in a local
  worktree is undelivered: the next lane rebases past it, the train never sees it, and it is lost.
- **Verify your own push.** `git ls-remote --heads origin <branch>` and `gh pr view <n> --json state`.
  Do not report a PR you did not confirm exists.
- **Never work on a DETACHED HEAD.** Run `git branch --show-current` after creating your worktree and
  confirm it prints a branch name. If it is empty, create one (`git switch -c <branch>`) BEFORE writing
  a line of code.
- **If a push FAILS or is blocked, that is the headline of your report, not a footnote.** Say what the
  error was. Never end a report implying delivery you did not achieve.
- **Resolve a PR's branch from GitHub, never from a lane's prose.** `gh pr view <n> --json headRefName,headRefOid`
  and confirm the branch tip matches `headRefOid` before handing it to the orchestrator.

## 8. Report format
**Causal chain with file:line | gate output (literal) | anti-vacuity run (BEFORE/AFTER blocks) | gate counts | PR number | evidence of MERGED state | worktree removed | anything you refused to decide silently.**

**Max 15 lines for findings/summary.** Report MUST include:
- Files changed (with paths relative to repo root)
- Gate output (not a summary — the actual lines)
- PR number and state (via `gh pr view --json state`)
- Worktree cleanup status (removed = "done")

**Zero findings is valid.** Never pad a report with summaries. If you ran the gates and found nothing,
say "gates clean, no findings" and cite the literal output.
