# STAGE-CONTRACTS.md: stage x category templates for every dispatch

Status: real (G5.1, ruling R-LANES-1; rows f-bcd7ff61, f-11a046ce). Read with LANE-CONTRACT.md. Ascii only.

## 1. The rule

A change reaches CI through a pipeline of stage lanes, never one lane: S1 SPEC writes the failing test, S2 IMPL makes exactly that test green, S3 GATE runs the gates read-only, S4 SHIP commits, pushes and arms the PR, and S5 REVIEW refutes the diff. The brief is the filled template for one stage and one category and nothing more. Matt 2026-10-22: "split the steps into different contracts, go back to the core of this project with extremelely tight scoped work" and "smaller contracts, aimed at the vertical delivery pipeline, for categories."

## 2. Stages

- S1 SPEC: IN a row or ruling; OUT a failing test path plus its red output; ALLOWED Read, Grep, Write to the test file, run that one test; FORBIDDEN editing non-test files, commit, push; HAND BACK test path and red output lines.
- S2 IMPL: IN the S1 test path and red output; OUT a diff file (no commits); ALLOWED Edit to the named files, run the named test; FORBIDDEN editing the test, committing, pushing, running gates other than the named test; HAND BACK diff path and the green line.
- S3 GATE: IN the S2 diff; OUT a gate log file; ALLOWED read-only gate commands; FORBIDDEN Edit, Write, commit, push, any --update-baseline; HAND BACK PASS line or the catalog recipe id that matched.
- S4 SHIP (github-agent): IN the S3 PASS and the message file; OUT PR number, head sha, armed state; ALLOWED git add, commit with message file, foreground push, PR against main, arm auto-merge; FORBIDDEN any code edit, --no-verify, update-branch, manual merge, merge train; HAND BACK PR number, head sha, state.
- S5 REVIEW: IN the S4 PR diff and the ruling; OUT a review file; ALLOWED read-only; FORBIDDEN edit, commit, push; HAND BACK verdict plus refutations, each with file:line.

## 3. Categories

- hook: brain hooks/*.mjs in claude-config (tests are hooks/<name>.test.mjs).
- script: the ~/scripts library (claude-scripts), with LOOP-MANIFEST.json for looped files.
- docs: markdown or prose only, no code and no gates beyond secret scan.
- keyrun-sim: game/sim/Keyrun.Sim (C#, tests in game/sim/Keyrun.Sim.Tests).
- keyrun-bots: game/bots/Keyrun.Bots.Tests (balance is reported, never gated).
- keyrun-content: game/content/data and the repo-root content/data/dungeons tree (never edit both trees in one lane).
- keyrun-view: Godot scene and HUD code (game/src/Host), scene tests only.
- ci-fix: a red CI run matched to one row of docs/CI-CATALOG.md.
- memory: aesop memory facts; lanes write INBOX.md only, memory-keeper is the sole writer of MEMORY.md.
- brain (not in the nine categories): a claude-scripts commit needs knowledge_tree first (see the script S4 cell).

## 4. Matrix

Field key: GOAL is the id from STATE.md G5 or the row; IN and OUT are the slot paths; CMD is the exact command; FORBIDDEN and HAND BACK are per cell. Slots in angle brackets are filled per dispatch. Angle brackets that name a source (`<... not in inputs>`) are unresolved and must be filled before dispatch.

### S1 SPEC
```
S1 SPEC | hook | PURPOSE one failing test for one hook rule | GOAL <id> | IN <ruling> | OUT hooks/<name>.test.mjs
CMD node --test hooks/<name>.test.mjs (must be red) | FORBIDDEN edit hooks/*.mjs, commit | HAND BACK test path + red lines
S1 SPEC | script | PURPOSE one failing test for one script behavior | GOAL <id> | IN <ruling> | OUT <script test path>
CMD <script test command, not in inputs> (must be red) | FORBIDDEN edit script, commit | HAND BACK test path + red lines
S1 SPEC | docs | SKIPPED (docs-only changes skip S1) | GOAL - | IN - | OUT -
CMD none | FORBIDDEN - | HAND BACK none
S1 SPEC | keyrun-sim | PURPOSE one failing sim test for one rule | GOAL <id> | IN <ruling> | OUT game/sim/Keyrun.Sim.Tests/<file>
CMD dotnet test game/sim/Keyrun.Sim.Tests --nologo (must be red) | FORBIDDEN edit game/sim/Keyrun.Sim, commit | HAND BACK test path + red lines
S1 SPEC | keyrun-bots | PURPOSE one failing bots test for one rule | GOAL <id> | IN <ruling> | OUT game/bots/Keyrun.Bots.Tests/<file>
CMD dotnet test game/bots/Keyrun.Bots.Tests --nologo (must be red) | FORBIDDEN edit bots code, touch balance tests | HAND BACK test path + red lines
S1 SPEC | keyrun-content | PURPOSE one failing content-load test | GOAL <id> | IN <ruling> | OUT <content test path, not in inputs>
CMD <Content test command from game CLAUDE.md, not in inputs> (must be red) | FORBIDDEN edit data files, edit the other content tree | HAND BACK test path + red lines
S1 SPEC | keyrun-view | PURPOSE one failing HUD scene assertion | GOAL <id> | IN <ruling> | OUT game/src/Host/<scene test>
CMD powershell -NoProfile -ExecutionPolicy Bypass -File game/src/Host/HudSceneTest.ps1 > <scratch>/hud-red.txt 2>&1 (must be red) | FORBIDDEN screens.ps1 captures, raw Godot launches | HAND BACK scene path + red lines
S1 SPEC | ci-fix | PURPOSE reproduce the red from one catalog row | GOAL <CI-id> | IN docs/CI-CATALOG.md row <CI-id> | OUT <repro path>
CMD <recipe from docs/CI-CATALOG.md row <CI-id>> (must reproduce) | FORBIDDEN editing gates, baselines, timeouts | HAND BACK repro path + matched lines
S1 SPEC | memory | PURPOSE one check that the fact is missing or wrong | GOAL <id> | IN <fact or ruling> | OUT <check path>
CMD <memory check command, not in inputs> (must fail) | FORBIDDEN writing MEMORY.md or any index | HAND BACK check path + failing lines
```

### S2 IMPL
```
S2 IMPL | hook | PURPOSE make the S1 test green, nothing more | GOAL <id> | IN <S1 test path> | OUT <scratch>/<id>.diff
CMD node --test hooks/<name>.test.mjs (must be green) | FORBIDDEN editing the test, commit, push | HAND BACK diff path + green line
S2 IMPL | script | PURPOSE make the S1 test green, nothing more | GOAL <id> | IN <S1 test path> | OUT <scratch>/<id>.diff
CMD <script test command, not in inputs> (must be green) | FORBIDDEN editing the test, commit, push | HAND BACK diff path + green line
S2 IMPL | docs | PURPOSE write the one doc named in the brief | GOAL <id> | IN <brief> | OUT <doc path>
CMD none beyond Write | FORBIDDEN edits outside the named doc, commit, push | HAND BACK doc path + line count
S2 IMPL | keyrun-sim | PURPOSE make the S1 sim test green | GOAL <id> | IN <S1 test path> | OUT <scratch>/<id>.diff
CMD dotnet test game/sim/Keyrun.Sim.Tests --nologo (must be green) | FORBIDDEN editing the test, bots or content, commit | HAND BACK diff path + green line
S2 IMPL | keyrun-bots | PURPOSE make the S1 bots test green | GOAL <id> | IN <S1 test path> | OUT <scratch>/<id>.diff
CMD dotnet test game/bots/Keyrun.Bots.Tests --nologo (must be green) | FORBIDDEN loosening balance margins, commit | HAND BACK diff path + green line
S2 IMPL | keyrun-content | PURPOSE make the S1 content test green in one tree | GOAL <id> | IN <S1 test path> | OUT <scratch>/<id>.diff
CMD <Content test command from game CLAUDE.md, not in inputs> (must be green) | FORBIDDEN editing the other content tree, commit | HAND BACK diff path + green line
S2 IMPL | keyrun-view | PURPOSE make the S1 HUD scene assertion pass | GOAL <id> | IN <S1 scene path> | OUT <scratch>/<id>.diff
CMD powershell -NoProfile -ExecutionPolicy Bypass -File game/src/Host/HudSceneTest.ps1 > <scratch>/hud-green.txt 2>&1 | FORBIDDEN screens.ps1, raw Godot launches, commit | HAND BACK diff path + green line
S2 IMPL | ci-fix | PURPOSE apply the catalog recipe once | GOAL <CI-id> | IN docs/CI-CATALOG.md row <CI-id> | OUT <scratch>/<id>.diff
CMD <recipe from docs/CI-CATALOG.md row <CI-id>> then the S1 repro (must pass) | FORBIDDEN raising timeouts, editing baselines to hide NEW keys, adding secrets, second retry | HAND BACK diff path + repro pass line
S2 IMPL | memory | PURPOSE write one INBOX.md item | GOAL <id> | IN <fact> | OUT INBOX.md (appended line)
CMD none beyond Write | FORBIDDEN writing MEMORY.md, any index, other files | HAND BACK appended line + INBOX path
```

### S3 GATE
```
S3 GATE | hook | PURPOSE run the hook test and the fail-closed suite read-only | GOAL <id> | IN <S2 diff> | OUT <scratch>/gate.txt
CMD node --test hooks/<name>.test.mjs hooks/_failclosed.test.mjs | FORBIDDEN Edit, Write, commit, push | HAND BACK PASS line or recipe id
S3 GATE | script | PURPOSE run secret scan and the script test read-only | GOAL <id> | IN <S2 diff> | OUT <scratch>/gate.txt
CMD python ~/scripts/secret_scan.py --staged; <script test command, not in inputs> | FORBIDDEN Edit, Write, commit, push | HAND BACK PASS line or recipe id
S3 GATE | docs | PURPOSE secret scan only (fastcheck skipped for docs-only) | GOAL <id> | IN <doc diff> | OUT <scratch>/gate.txt
CMD python ~/scripts/secret_scan.py --staged | FORBIDDEN Edit, Write, commit, push | HAND BACK CLEAN line with file count above zero
S3 GATE | keyrun-sim | PURPOSE run fastcheck on the sim diff | GOAL <id> | IN <S2 diff> | OUT <scratch>/fastcheck.txt
CMD cd <wt> && powershell -NoProfile -ExecutionPolicy Bypass -File tools/fastcheck.ps1 -All > <scratch>/fastcheck.txt 2>&1 | FORBIDDEN Edit, Write, commit, push | HAND BACK fastcheck: PASS in <n>s line
S3 GATE | keyrun-bots | PURPOSE run fastcheck, bots project reported separately | GOAL <id> | IN <S2 diff> | OUT <scratch>/fastcheck.txt
CMD cd <wt> && powershell -NoProfile -ExecutionPolicy Bypass -File tools/fastcheck.ps1 -All > <scratch>/fastcheck.txt 2>&1 | FORBIDDEN Edit, Write, commit, push, disarming balance | HAND BACK fastcheck PASS line + balance red note if any
S3 GATE | keyrun-content | PURPOSE run fastcheck on the content diff | GOAL <id> | IN <S2 diff> | OUT <scratch>/fastcheck.txt
CMD cd <wt> && powershell -NoProfile -ExecutionPolicy Bypass -File tools/fastcheck.ps1 -All > <scratch>/fastcheck.txt 2>&1 | FORBIDDEN Edit, Write, commit, push | HAND BACK fastcheck PASS line
S3 GATE | keyrun-view | PURPOSE run the HUD scene script read-only | GOAL <id> | IN <S2 diff> | OUT <scratch>/hud-gate.txt
CMD powershell -NoProfile -ExecutionPolicy Bypass -File game/src/Host/HudSceneTest.ps1 > <scratch>/hud-gate.txt 2>&1 | FORBIDDEN Edit, Write, commit, push, screens.ps1 | HAND BACK scene PASS line
S3 GATE | ci-fix | PURPOSE run the gate the CI log named, read-only | GOAL <CI-id> | IN <S2 diff> | OUT <scratch>/gate.txt
CMD <gate named in the CI log, from docs/CI-CATALOG.md row <CI-id>>; python ~/scripts/secret_scan.py --staged | FORBIDDEN Edit, Write, --update-baseline | HAND BACK PASS line or the row id
S3 GATE | memory | PURPOSE secret scan on the INBOX diff | GOAL <id> | IN <S2 diff> | OUT <scratch>/gate.txt
CMD python ~/scripts/secret_scan.py --staged | FORBIDDEN Edit, Write, commit, push | HAND BACK CLEAN line
```

### S4 SHIP
```
S4 SHIP | hook | PURPOSE commit, push, arm the PR; no code | GOAL <id> | IN <S3 PASS + message file> | OUT PR number + head sha
CMD commit with trailer Hook-Proof: hooks/<name>.test.mjs red=N/M green=M/M; git push (foreground); gh pr create --base main; arm auto-merge | FORBIDDEN code edits, --no-verify, update-branch, manual merge | HAND BACK PR number, head sha, armed state
S4 SHIP | script | PURPOSE commit, push, arm the PR; no code | GOAL <id> | IN <S3 PASS + message file> | OUT PR number + head sha
CMD python ~/scripts/knowledge_tree.py --write --root <wt> first; commit with Loop-Proof: tests/<file>.py red=N/M green=M/M only if the file is in LOOP-MANIFEST.json | FORBIDDEN code edits, --no-verify, manual merge | HAND BACK PR number, head sha, armed state
S4 SHIP | docs | PURPOSE commit, push, arm the PR; no code | GOAL <id> | IN <S3 PASS + message file> | OUT PR number + head sha
CMD commit; git push (foreground); gh pr create --base main; arm auto-merge | FORBIDDEN any edit, --no-verify, manual merge | HAND BACK PR number, head sha, armed state
S4 SHIP | keyrun-sim | PURPOSE commit, push, arm the PR per keyrun CI contract | GOAL <id> | IN <S3 PASS + message file> | OUT PR number + head sha
CMD commit; git push (foreground); gh pr create --base main; arm auto-merge | FORBIDDEN code edits, update-branch, manual merge | HAND BACK PR number, head sha (CI contract item 6)
S4 SHIP | keyrun-bots | PURPOSE commit, push, arm the PR per keyrun CI contract | GOAL <id> | IN <S3 PASS + message file> | OUT PR number + head sha
CMD commit; git push (foreground); gh pr create --base main; arm auto-merge | FORBIDDEN code edits, update-branch, manual merge | HAND BACK PR number, head sha, balance note
S4 SHIP | keyrun-content | PURPOSE commit, push, arm the PR per keyrun CI contract | GOAL <id> | IN <S3 PASS + message file> | OUT PR number + head sha
CMD commit; git push (foreground); gh pr create --base main; arm auto-merge | FORBIDDEN code edits, update-branch, manual merge | HAND BACK PR number, head sha
S4 SHIP | keyrun-view | PURPOSE commit, push, arm the PR per keyrun CI contract | GOAL <id> | IN <S3 PASS + message file> | OUT PR number + head sha
CMD commit; git push (foreground); gh pr create --base main; arm auto-merge | FORBIDDEN code edits, update-branch, manual merge | HAND BACK PR number, head sha, scene PASS line
S4 SHIP | ci-fix | PURPOSE push the fix or merge-forward, then arm | GOAL <CI-id> | IN <S3 PASS> | OUT PR number + head sha
CMD git merge origin/HEAD in the worktree only when the row says merge-forward; git push (foreground); arm auto-merge | FORBIDDEN update-branch, raising timeouts, --no-verify | HAND BACK PR number, head sha, CI-id
S4 SHIP | memory | PURPOSE commit the INBOX line to claude-config, push | GOAL <id> | IN <S3 CLEAN + message file> | OUT commit sha
CMD commit; git push (foreground) | FORBIDDEN writing MEMORY.md, PR arming unless asked | HAND BACK commit sha + push line
```

### S5 REVIEW
```
S5 REVIEW | hook | PURPOSE refute the hook diff against the ruling | GOAL <id> | IN <S4 PR diff + ruling> | OUT <scratch>/review.txt
CMD git diff origin/HEAD...<branch> (read-only) | FORBIDDEN edit, commit, push, gh pr merge | HAND BACK verdict + refutations with file:line
S5 REVIEW | script | PURPOSE refute the script diff against the ruling | GOAL <id> | IN <S4 PR diff + ruling> | OUT <scratch>/review.txt
CMD git diff origin/HEAD...<branch> (read-only) | FORBIDDEN edit, commit, push, gh pr merge | HAND BACK verdict + refutations with file:line
S5 REVIEW | docs | PURPOSE refute the doc diff against the brief (optional) | GOAL <id> | IN <S4 PR diff + brief> | OUT <scratch>/review.txt
CMD git diff origin/HEAD...<branch> (read-only) | FORBIDDEN edit, commit, push | HAND BACK verdict + refutations with file:line
S5 REVIEW | keyrun-sim | PURPOSE refute the sim diff against the ruling and the bible | GOAL <id> | IN <S4 PR diff + ruling> | OUT <scratch>/review.txt
CMD git diff origin/HEAD...<branch> (read-only) | FORBIDDEN edit, commit, push | HAND BACK verdict + refutations with file:line
S5 REVIEW | keyrun-bots | PURPOSE refute the bots diff against the ruling | GOAL <id> | IN <S4 PR diff + ruling> | OUT <scratch>/review.txt
CMD git diff origin/HEAD...<branch> (read-only) | FORBIDDEN edit, commit, push, touching balance tests | HAND BACK verdict + refutations with file:line
S5 REVIEW | keyrun-content | PURPOSE refute the content diff against the ruling | GOAL <id> | IN <S4 PR diff + ruling> | OUT <scratch>/review.txt
CMD git diff origin/HEAD...<branch> (read-only) | FORBIDDEN edit, commit, push | HAND BACK verdict + refutations with file:line
S5 REVIEW | keyrun-view | PURPOSE refute the view diff against the ruling | GOAL <id> | IN <S4 PR diff + ruling> | OUT <scratch>/review.txt
CMD git diff origin/HEAD...<branch> (read-only) | FORBIDDEN edit, commit, push, screens.ps1 | HAND BACK verdict + refutations with file:line
S5 REVIEW | ci-fix | PURPOSE refute the fix against the catalog row | GOAL <CI-id> | IN <S4 PR diff + row> | OUT <scratch>/review.txt
CMD git diff origin/HEAD...<branch> (read-only) | FORBIDDEN edit, commit, push, baseline edits | HAND BACK verdict + refutations with file:line
S5 REVIEW | memory | PURPOSE refute the INBOX line against the source fact | GOAL <id> | IN <commit + fact> | OUT <scratch>/review.txt
CMD git show <sha> (read-only) | FORBIDDEN edit, commit, push | HAND BACK verdict + refutations with file:line
```

## 5. Pipeline

- Orchestrator order: S1 -> S2 -> S3 -> S4, then S5 when the orchestrator names it (G5 does not make S5 mandatory per category).
- A stage never starts until the previous hand-back names its output artifact (the path or sha in the table above).
- Docs-only changes skip S1 and skip the S3 fastcheck (S3 runs the secret scan only).
- A brief names exactly one stage's verbs; the dispatch hook denies a brief that names more than one stage.
- Lanes already running finish under the old shape; this file governs new dispatches.
