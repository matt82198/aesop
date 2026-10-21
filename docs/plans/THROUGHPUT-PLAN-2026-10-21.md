# Throughput regression and scale plan (keyrun under aesop), 2026-10-21 14:05Z (9:05 AM CDT)

PURPOSE: this document exists to explain, from gh and disk evidence, why the receipts loop is producing merges but not gameplay, and to lay out the fixes in order, with the ones that need Matt marked; status: real.

Matt 2026-10-21, verbatim: "it defined a receipts loop to fix the class of issues... you are supposed to be doing that with the receipts loop constantly, their are hooks in place, i don't understand what is going on. remove the autocompact right now and do a long tail possible throughput regression optimization planning phase, keyrun will be 8000prs at this point, scaling needs to be considered and how to fix this".

## 1. What the numbers say (all from gh or disk at 14:0xZ)

| Measure | Value | Source |
|---|---|---|
| keyrun PRs merged 2026-10-20 | 120 | gh pr list --state merged |
| keyrun PRs merged 2026-10-21 by 14:00Z | 80 | same |
| of today's 80: docs/checkpoint | 30 (25 are orchestrator checkpoints) | title prefix |
| of today's 80: fix | 26 | same |
| of today's 80: feat | 8 | same |
| of today's 80: test, integrate, ci, client, sim | 16 | same |
| LOC churn merged since 10-18 | ~150,000 | additions+deletions |
| Game workflow runs, last 100 runs (11:21Z to 13:54Z, 2.5 h) | 50 Game + 50 Tests | gh run list |
| Game runs cancelled (superseded by a new push) | 22 of 50 | same |
| Game run wall time | 13 to 14 min (game-e2e 12.5 min, HUD scene test 5.5 of it) | #1247 lane, two green main runs |
| Lane hand-backs this session | 476 (for ~80 merges: ~6 dispatches per merge) | ~/.claude/state/handbacks |
| keyrun worktrees on disk | 148 | keyrun-wt |
| keyrun STATE.md / BUILDLOG.md | 129 KB / 98 KB | wc -c |
| Open keyrun PRs | 18 (4 active, 14 from 10-20 untouched) | gh pr list |
| Open keyrun findings | 40 (63 across repos) | open_findings.py |
| Live ~/.claude vs origin/master | 18 local commits not on origin, origin 1 ahead, 3 dirty files | #233 lane |

Reading: the loop merges 80 to 120 PRs a day, so the receipts gate itself works. What it does not do is move the product. Thirty of today's eighty merges are the orchestrator writing to itself, each one a full 14-minute Game run on required checks and an update-branch cascade over every other open PR. Eight merges were features, and none of them is a bible slice Matt can feel in a run.

## 2. Root causes (ranked by throughput lost)

1. **The receipt that counts is "PR merged", not "slice playable".** The gate is CI green on a PR. Nothing checks that a merged PR moved the section 1 loop. So the fleet optimizes merges: findings (63 open, most one-line defects) become one PR each, checkpoints become PRs, and the gameplay slices Matt asked for (Seed room, boss pace) get deferred behind the pile.
2. **Checkpoints are PRs on the required-check path.** 25 checkpoint PRs today = ~6 hours of Game runner time for docs, plus the cascade. The context hook forced a checkpoint about every 20 minutes, each fold pushed, each push restarted CI on #463 (Matt's diagnosis, 13:5xZ). Fixed in part this morning (local folds); the shape is still a PR per span.
3. **Strict up-to-date protection without a merge queue.** Every merge invalidates every open PR; 22 of 50 Game runs were cancelled as superseded. One merge per 14-minute CI cycle is the ceiling, and docs merges spend those cycles. f-77c8d9f9 (merge queue approved by Matt once the pile lands).
4. **Lanes could not reproduce CI.** game-e2e runs nine scene scripts; the no-lane-game-launch hook denied lanes running them until claude-config #233 (merged 14:0xZ, NOT yet live: the live ~/.claude is diverged). Every scene-test red was found by CI, 14 minutes later, by a fresh lane. Five lanes in a row rediscovered "how game-e2e runs" (Matt's point; LANE-CONTRACT CI section is aesop #1247, armed).
5. **Dispatch granularity.** 476 hand-backs for 80 merges. Each hand-back is 1 to 3 KB into the orchestrator's context, which compacted ~15 times per span, losing intent each time and re-deriving state from STATE.md (129 KB and growing 20 KB a day).
6. **Balance tests as merge gates.** The fork-parity dominance fact on #449 fails on chaotic margins; four retune rounds today. A balance oracle in the required path blocks integration branches indefinitely.
7. **Nothing is garbage-collected.** 148 worktrees, 14 stale PRs from 10-20 carrying Matt's arms, 40 open findings, STATE.md unbounded. At 8000 PRs every one of these is a wall.

## 3. The fix, in order (each line is a slice with a receipt)

### Phase 0: stop the bleeding (today, in this order)
- P0.1 **Checkpoints off the CI path.** One checkpoint PR per phase boundary (at most every 3 hours), folded locally in between. Receipt: checkpoint merges per day <= 8 (gh). Orchestrator rule, in force now. [no Matt]
- P0.2 **Docs-only PRs skip the Game workflow.** game.yml and tests.yml get `paths-ignore` for `STATE.md`, `BUILDLOG.md`, `docs/**`, `design/**`, `*.md` with a tiny always-green "docs-only" job carrying the same required-check names so protection stays satisfied. Receipt: a docs PR's checks complete in < 2 min. [one keyrun PR; required-check names unchanged so no repo-settings change]
- P0.3 **Live brain sync.** ~/.claude is 18 commits ahead of origin/master and dirty; #233 (scene-test allow for lanes) is on origin only. Reconcile by committing the 3 dirty files (secret-scanned), `git merge origin/master` (no rebase), hook tests, push master. Receipt: `git -C ~/.claude status` clean and `rev-list --count origin/master..HEAD` = 0; the two hook test files green on the live tree. [Matt: the 18 unpushed commits are unexpected under the brain-integrity rule; the orchestrator needs a yes before pushing them]
- P0.4 **Lanes run game-e2e locally before pushing** (now allowed by #233 once live): a lane's hand-back names every scene test its change can affect and pastes their PASS lines. Receipt: first-pass CI rate on the ledger. [contract text in #1247 + discovery-fields PR]
- P0.5 **Land the four active PRs, open nothing else.** #472 (CI on 6ea9e2f5), #470 (d76d8db1), #449 (warden forks lane), #446 (2e52eb2b). Receipt: open active PRs = 0.
- P0.6 **Worktree GC.** Remove the worktrees whose PR is MERGED and whose tree is clean (pr_merged_check already proves MERGED). ~130 of 148. [Matt: deletes are a hard stop; the list is produced first, Matt says yes once, then it becomes automatic after every MERGED]
- P0.7 **Stale PR policy.** The 14 PRs from 10-20 (#250 to #418): each gets one haiku lane to rebase onto main and report green or red in one pass; red ones are closed with a finding that records what they held. [Matt: closing his armed PRs needs a yes]

### Phase 1: the receipts loop pointed at gameplay (this week)
- P1.1 **A slice is the unit of work, not a finding.** A slice = one sentence verifiable in one headless playthrough plus three viewed frames (Matt's definition). The acceptance line is a scene test or Playtest fact in the same PR (e.g. `seed.lock >= 2 before Seed death`). Findings that are not slices are folded into the slice that covers them or closed. Target: 5 to 8 open slices, one PR each, one lane each, merged before the next.
- P1.2 **Demo receipt per merged slice.** After MERGED, the orchestrator (or a capture lane once the hook allows screens.ps1 for lanes) captures the default run on main, views first-combat, mid-run and run-end frames, and writes keyrun-screens/<sha>/VERDICT.md naming the models and the dungeon. A slice is DONE only with that file. This is the receipt Matt reviews.
- P1.3 **Balance facts out of the required path.** Fork parity and similar dominance oracles run in a non-required `balance` job that opens a finding on drift. Depth stays tested; it no longer blocks integration. [Matt's call, asked at 13:5xZ]
- P1.4 **Slices first:** Seed room (R-SEED-1), boss pace (R-BOSS-PACE-1), KF2 Choir teaches the kick, KF1 combat read, then the bible section 9 pulls in order.

### Phase 2: CI and machinery for scale (next 2 weeks)
- P2.1 **Merge queue** on main (Matt approved after the pile lands): removes the update-branch cascade; required checks run once per queue batch.
- P2.2 **game-e2e as a matrix.** Nine scene scripts across 3 runners (HUD alone, then two groups) cuts 12.5 min to ~6; Godot binary and the import cache restored from actions/cache keyed on the assets hash.
- P2.3 **Dispatch ledger + contract retro.** dispatch_ledger.py (claude-scripts PR in flight): one line per dispatch; `report --last 10 --threshold 0.6` before every wave; RETRO stops dispatch until a contract amendment is open. A fresh haiku reviewer reads only the ledger and the last ten hand-backs each cycle (refinement-monitor charter step) and proposes amendments.
- P2.4 **Control-file rotation.** STATE.md keeps INTENT + an ACTIVE block <= 20 KB; everything older rotates to docs/checkpoints/<date>.md by a script, lossless. The orchestrator re-reads 20 KB after a compaction, not 129.
- P2.5 **Hand-backs as lines, not essays.** The hand-back carries the required fields block first; the orchestrator reads that block and the ACCEPTANCE line, the rest stays on disk.

### Phase 3: scale targets (8000 PRs)
Metrics script (gh + ledger), printed daily to BUILDLOG: merges/day by type; gameplay slices merged/day; CI minutes per merge; dispatches per merge; first-pass rate; time-to-merge p50/p90; open PRs by age; worktrees; STATE.md size. Targets: slices >= 3/day, CI minutes per merge < 20, dispatches per merge < 3, first-pass > 60%, open PRs < 10 with none older than 24 h, worktrees < 15, STATE.md < 40 KB.

## 4. Decisions Matt owns (hard stops)
1. Push the live brain's 18 unpushed commits after merging origin/master (P0.3).
2. Delete ~130 merged worktrees (P0.6), then automatic GC after every MERGED.
3. Close red stale PRs from 10-20 after one rebase pass (P0.7).
4. Fork-parity and other balance facts out of the required checks (P1.3).
5. Set BRAIN_READ_TOKEN in claude-config Actions so trailer-gate can run (it dies at checkout; the gate passes locally).
6. Merge queue timing (P2.1).

## 5. What the orchestrator changes about itself, now
- Checkpoint once per phase boundary; folds local.
- No new finding-sized PRs; slices only, and a slice has its acceptance line in the PR.
- Read ledger lines and ACCEPTANCE lines, not hand-back essays; relaunch, never SendMessage.
- Every hand-back's Discovery and Failures fields are read; two identical Discovery answers = contract PR before the next dispatch in that area.
- The demo receipt (frames viewed, VERDICT.md) closes a slice, never CI alone.
