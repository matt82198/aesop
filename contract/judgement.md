# STEP: judgement (implement, decide and adjudicate lanes; read together with the floor above)
S5 REVIEW stage lanes are described in the review step. A keyrun lane also reads the keyrun CI contract (contract/keyrun.md).

## 4c. Environment facts for implementers (discoveries)
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
8. **Receipt emission** — Receipts are automatic and `AESOP_RECEIPT_EMIT=0` is inert (see "Receipts" in section 4). A lane that needs a faster push waits for the hook.

## 6. Boundaries (continued): merge and landing mechanics
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
    mandatory; never arm a draft. A repo with no required checks is Mode 2 and is never armed (see section 6); the lane states that in its report.
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
  code changes, re-push the branch (a plain push, or a local merge of the default branch per section 3 when the PR is BEHIND). Never update a PR
  branch server-side (section 3: no receipt can exist for it).
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

- **Python**, **ASCII** source (tools/*.py enforced by `encoding_lint.ascii_findings`; shrink-only ratchet `tools/encoding-ascii-baseline.json`).
- **Dead-by-bug code must NOT be deleted — fixes wire it up.**
- Pushes run as their own Bash command with a 600000 ms timeout and are never backgrounded (discovery 2026-10-21).
- The ONE sanctioned wait for a lane is `gh pr checks <N> --repo <owner/repo> --watch` or `gh run watch <id>` as a single foreground command with a 600000 ms timeout, and Monitor and sleep loops stay denied to lanes (discovery 2026-10-21).

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

## Stage lanes (G5)
1. A change reaches CI through a pipeline of stage lanes, never one lane. Each stage is its own dispatch.
2. Every brief carries one line `STAGE: S1 SPEC | S2 IMPL | S3 GATE | S4 SHIP | S5 REVIEW` (the one stage it runs) and one `CATEGORY:` line. A brief that names more than one stage is denied.
3. Per stage, one line each, with input artifact, output artifact and forbidden verbs:
   - S1 SPEC: in a row or ruling; out the failing test path and its red output; forbidden: any non-test edit, commit, push.
   - S2 IMPL: in the S1 test path and red output; out a diff file, no commits; forbidden: editing the test, commit, push, gates other than the named test.
   - S3 GATE: in the S2 diff; out a gate log; forbidden: Edit, Write, commit, push, any --update-baseline; hands back PASS or the catalog recipe id; on any red, match the failing line to its row in docs/CI-CATALOG.md by path before any retry. Gate command list, run each that applies: `node --test <the named test files>` (for scaffold tests add `--import ./tests/helpers/isolated-env.mjs --test-force-exit --test-timeout=60000`); `python tools/claudemd_sync_gate.py --check --base-ref origin/main`; `python tools/claudemd_lint.py --root .`; `python tools/gen_tool_index.py --check` (when tools/ changed); `python -m pyflakes <touched .py>` compared against the same files on origin/main (a warning that pre-exists on main is not red); `node --check bin/cli.js` (when bin/ changed); `python tools/encoding_lint.py --check` (G10 encoding lint, scans the whole repo, run once per S3); `python ~/scripts/secret_scan.py <touched files>`. No whole-suite discover run is part of S3; the full python suite exceeds a lane's foreground timeout and the pre-push gate runs the shards at S4.
   - S4 SHIP: in the S3 PASS and message files `<scratchpad>/lanes/<branch>/{commit-msg,pr-body}.txt` (never another branch's); out PR number, head sha, armed state; forbidden: code edits, --no-verify, update-branch, manual merge, merge train. Steps: 1 `git fetch origin main`; 2 commit the staged files with the message file: `git commit -F <msg file>`; 3 merge origin/main (three-way): `git merge origin/main`, never rebase; 4 regenerate or verify generated paths (tools/INDEX.md, docs/INCIDENTS.md); chore-commit changes; 5 secret scan `python ~/scripts/secret_scan.py --range origin/main..HEAD --repo <worktree>` (exit 1 blocks), push foreground; 6 open the PR against main; 7 arm with `gh pr merge <N> --auto --squash --match-head-commit <sha>`. Mode 2 - repos WITHOUT CI (claude-config, claude-scripts, conductor3, psinasty): do not arm; the lane must stop at PR open and hand back the head sha; the orchestrator lands it with a gate-read and one pinned squash per section 6. On red, match failing line to its docs/CI-CATALOG.md row by path before retry. Stash is never used; a merge refusing on local changes means step 2 was skipped. `--staged` only when files are staged (empty index refuses 'scanned 0 files', fail-closed); else `--range`.
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
