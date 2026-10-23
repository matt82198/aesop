# Architecture recursion: back to small per-domain primitives (G6, R-ARCH-1)

Status: PLAN ONLY. Nothing in this file is built. Matt approves this PR before any lane runs. Planning team: one Opus planner plus six read-only Haiku readers (hooks, memory and incidents, .md sizes, per-project asks, map injection, Matt asks vs shipped), 2026-10-22 ~20:1x CT.

Matt 2026-10-22 ~9:0x PM CT, verbatim (the brief): "we've gotten to a point where i don't understand my own repo, at some point you started designing, and not consulting planning teams for waves, we don't really follow any of the same primitives anymore, its turning into a monolith of hooks and not a small package for per domain intelligence. Half my asks get dropped, like per project orchestrations has been suggested probably 4 times now and you built out like one file... nothing is being categorized and turned into generics or mechanical machinery, you just have a massive list of memories at this point and incidents and all this stuff logged that we never recurse, identify the pattern and fix. Domain files have become insane. We don't recursively split to the smallest operation and .md anymore... it works well, but the core architecture is becoming spaghetti" ... "nothing i ask is being built, its just plumbing and friction" ... "and specific hooks should fire per domain man".

How to read the pipeline rows: every section ends in numbered G5 stage pipelines, one row each: `category | S1 red test | S2 change | S3 gate | S4 ship target`. The orchestrator only sequences them (docs/STAGE-CONTRACTS.md). Category `aesop-tool` (Python under aesop tools/) does not exist in STAGE-CONTRACTS.md today; pipeline 2.3 adds it. Docs rows skip S1 per STAGE-CONTRACTS s5.

---

## 1. Inventory (numbers, with the command that produced each)

Hooks (brain, `C:/Users/Mattt/.claude/hooks`, branch master, clean):

| Fact | Number | Command |
|---|---|---|
| hook source files (non-test .mjs) | 66 (59 hooks + 7 `_` helpers) | `ls hooks/*.mjs \| grep -v '\.test\.mjs$' \| wc -l` |
| hook source lines | 28,533 | `... \| xargs wc -l \| tail -1` |
| hook test files / lines | 77 / 30,961 (INDEX-tests.md claims 83: drift) | `ls hooks/*.test.mjs \| wc -l` |
| registered hook commands in settings.json | 55 (36 PreToolUse) | settings.json lines 18-471, read by hand |
| hooks run on EVERY Bash call | 22 PreToolUse + 1 PostToolUse = 23 processes | matcher table below |
| hooks run on every Agent/Task call | 9 Pre + 2 Post = 11 | same |
| hooks run on every Edit / Write / Read | 7 / 8 / 5 | same |
| hooks that early-exit on cwd or project | 0 | first 60 lines of each .mjs grepped for cwd/keyrun/aesop |
| keyrun-specific hooks registered globally | 5 (no-lane-game-launch, lane-ledger keyrun floor, saturation-gate, block-repo-settings-writes, deny-stacked-auto-merge) | grep `keyrun` in heads |
| largest hook | no-lane-game-launch.mjs 2,808 lines (keyrun-only purpose) | `wc -l` |
| resistance ledger | 81,299 rows; 1,513 deny; 472 allow; 2026-10-15..10-23 | `grep -c '"kind": *"deny"' state/resistance-ledger.jsonl` |
| top denies | claim-binding 351, no-orchestrator-merge-train 229, block-bare-pr-merge 208, block-repo-settings-writes 178, knowledge-tree-current 158, deny-stacked-auto-merge 135, purpose-pin-gate 85, lane-ledger 47, inject-lane-contract 35, no-credential-hunting 32 | `grep deny \| grep -o hook... \| sort \| uniq -c` |
| logging but not registered | no-credential-hunting (32 deny), stop-central-time (5), pr-base-main-gate (1); ghost `stage-verb-gate.mjs` 4 denies, no file | settings.json grep returns 0 |
| present, never registered | bash-deny-policy, claim-measured-cap, gate-file-arm-guard, no-browser-restart, gate-read, handback-ack | settings.json grep |

Domain groups (non-test lines): git/merge 12 hooks 4,873; dispatch/lane/cost 15 hooks 4,929; orchestrator context 10 hooks 5,691; docs/memory/brain-state 9 hooks 3,894; execution/polling/ game-launch 5 hooks 3,955; secrets/security 4 hooks 2,189; hook self-protection 2 hooks 623; resistance/wave 2 hooks 410; helpers 7 files 1,969.

Friction measured during THIS planning run (evidence, not narration): 4 of 6 read-only readers were denied on read-only commands. no-lane-game-launch denied `awk`, `>` into scratch, a `for` loop over `find`, a `>=` in inline python and any command containing the token `keyrun` ("UNDECIDABLE"); buildlog-append-guard denied a `for ... wc` loop that never touched BUILDLOG; hooks-edit-in-worktree-shell denied a read whose command substitution named the hooks path; and purpose-pin-gate denied this planner's read-only Haiku dispatch because the brief named a PR number (classified as a build lane). Each forced the reader to fall back to search.

Memory (`C:/Users/Mattt/.claude/memory/aesop`):

| Fact | Number | Command |
|---|---|---|
| memory files (top level) | 202 files, 5,920 lines | `ls *.md \| wc -l`; `cat *.md \| wc -l` |
| archived | 84 files, 2,061 lines | `ls archive/*.md \| wc -l` |
| index rows in MEMORY.md | 197 (about 5 files unindexed) | lines 2-198 |
| name a mechanism (.mjs, .py, hooks/, scripts/) | 94 | `grep -lE '\.mjs\|hooks/\|\.py\|scripts/' *.md \| wc -l` |
| name a mechanism that EXISTS on disk | about 56 (hand tally, Glob per name) | listed in s4 |
| recurring clusters | 12 (gate integrity ~22, merge/PR ~19, dispatch shape ~17, lane claims ~16, orchestrator context ~15, test vacuity ~15, autonomy ~12, state stores ~9, polling ~8, memory governance ~8, secrets ~6, product facts ~35) | title classification of MEMORY.md |

Incidents (`aesop/docs/INCIDENTS.md`): 44 rows (`grep -c '^| [a-z-]* (' docs/INCIDENTS.md`), every row class "(inferred)" from a commit subject; conflict 21, stall 7, test-pollution 5, flake 4, gate-activation 3, doc-invented 2, ci-drift 1, fake-green 1. Ten of the 21 conflicts share one subject ("fix: merge main + resolve conflicts"); 7 are test-count drift in tests/CLAUDE.md. The newest row is 2026-10-15: the file stopped recording a week ago while the resistance ledger grew by 81k rows. Incidents are written and never recursed.

Domain .md sizes (`find <root> -name '*.md' ... -exec wc -l {} +`):
- aesop: 195 .md; 15 CLAUDE.md, only ui/CLAUDE.md over 200 (214, 16 H2 sections, 6 concerns plus two wave-32 refactor logs). hooks/CLAUDE.md (142) spans 5 hook families; mcp/CLAUDE.md (143) is mostly a 92-line per-tool reference; driver/CLAUDE.md (148) mixes the seam contract with two refactor logs. Control files: BUILDLOG.md 1,462, CHANGELOG.md 877, STATE.md 600 lines whose single lines run past 2,500 characters (the INTENT block alone is ~40 KB).
- keyrun: 168 .md; STATE.md 2,129 lines; design/BIBLE.md 550.
- brain: 2,246 .md, of which 1,668 are `state/handbacks/**` and 50 are two duplicate synced skill packs.
- So the problem is not "CLAUDE.md over 200 lines"; it is files that span several primitives (concern count) and control files that grow without a split rule. The current line cap is the wrong check.

Per-project orchestration (detail in s6): asks at events seq 254, 255, 268 (2026-10-22) and 2026-10-23 seq 13; STATE.md:409 and BUILDLOG.md:746 (2026-10-18 02:00Z "recursive per-project orchestrator lanes", recorded as PLAN, never a row). Shipped: plan #1279, store core #1280, migrate
#1281, init+guard #1282, v1 read-only #1283, projections #1284, PreCompact multi-store reader
claude-config #279. Live: nothing. No registry, no per-project store branches, no cutover, v1 writer not frozen, no per-project orchestrator.

Map injection (`hooks/lib/maps.mjs`): maps read are PROJECTS.md and SYSTEM-MAP.md (MACHINERY-MAP.md absent); match keys are 8 project aliases (maps.mjs:131-140) or file tokens (:147); rows emitted in map-file order, not relevance (:263-279); cap 2,000 bytes (:126), this very brief dropped 49 matching rows; never denies (inject-lane-contract.mjs:55,651,657). No per-domain map exists.

Matt asks (`python -I ~/scripts/open_findings.py list --json`): 123 rows, 54 source=matt (40 open, 14 closed). Of the 54: shipped 18, partial 31, not shipped 5. Product 32 (shipped 9, partial 23); machinery 22 (shipped 9, partial 8, not shipped 5). Merged PRs since 2026-10-15 classified machinery by title: aesop 126 of 195, claude-config 196 of 216, claude-scripts 46 of 85. Matt's "nothing i ask is being built, its just plumbing" is measured: about 75% of merged PRs are machinery, and every product row is at best partial.

Pipelines (inventory upkeep becomes a script, not a reader fleet):
1. aesop-tool | tests/test_arch_inventory.py: a fixture brain + repo yields the s1 numbers as JSON (red: tool absent) | tools/arch_inventory.py emits hooks/memory/incidents/md/asks counts | S3 aesop-tool gates | aesop PR, runs at wave close
2. hook | hooks/INDEX-tests drift test: index count equals `*.test.mjs` count (red today: 83 vs 77) | regenerate INDEX-tests from disk | node --test + _failclosed | claude-config PR

---

## 2. Primitive set and the recursive-split rule

Twelve primitives. Each owns exactly ONE .md; every other file that mentions it is a pointer.

| Primitive | What it is (smallest operation) | Owner .md (after) | Owner today |
|---|---|---|---|
| lane | one stateless process, one stage, one owned file set | LANE-CONTRACT.md | same (293 lines, spans lane+gate+test+merge: split) |
| stage | S1..S5 verbs and hand-back shape | docs/STAGE-CONTRACTS.md | same |
| gate | one check, exit code, fail closed | docs/primitives/gate.md | tools/CLAUDE.md + LANE-CONTRACT s5 + GATES-FIRED.md |
| receipt | signed record that a run happened | docs/RECEIPT-GATE.md | same |
| ruling | Matt verdict as an event citing a G-id | docs/primitives/ruling.md | STATE.md INTENT prose |
| finding | ledger row with an observable | docs/primitives/finding.md | open_findings.py docstring |
| event | one hash-chained line in a project store | tools/event_store/CLAUDE.md | docs/plans/PER-PROJECT-EVENT-STORES.md |
| checkpoint | STATE/BUILDLOG write with WHY | docs/primitives/checkpoint.md | ~/.claude/docs/checkpointing.md |
| memory | recall-by-relevance fact, no mechanism | docs/primitives/memory.md | MEMORY.md header |
| domain .md | the map of one domain's files, gates, tests | docs/primitives/domain-md.md | root CLAUDE.md "Dispatch rule" |
| template | one stage x category brief | docs/STAGE-CONTRACTS.md s4 (cells) | same |
| map | rows: project, domain, files, gates, tests | docs/primitives/map.md | PROJECTS.md, SYSTEM-MAP.md |

The recursive-split rule, as a check (not prose):
- Every domain .md, control .md and hook package manifest declares front matter `primitive: <one of the twelve>` and `domain: <one name>`.
- `tools/primitive_lint.py` fails when: (a) the field is missing or names two values; (b) a .md has more than 8 H2 sections or 150 lines (CLAUDE.md) / a control file has a line over 400 chars; (c) a hook package's hooks guard more than one domain (manifest `domain` vs each hook's `guards:` header); (d) a .md's H2 headings name a second primitive's owner file topic (heading vocabulary table in docs/primitives/INDEX.md).
- The fix for every failure is the same: split into children, one primitive each, parent becomes a pointer list. The lint prints the split plan (which H2 goes to which child).
- STATE.md INTENT is the one sanctioned exception to (b) line length only until pipeline 2.4 splits each G-id into its own line block of at most 400 chars plus a pointer to its ruling events.

Pipelines:
1. aesop-tool | tests/test_primitive_lint.py: a fixture .md with two primitives and one with 9 H2s both fail; a clean one passes (red: tool absent) | tools/primitive_lint.py | aesop-tool gates + run on repo in report mode | aesop PR (report-only first; required after 2.5)
2. docs | skipped | docs/primitives/{INDEX,gate,ruling,finding,checkpoint,memory,domain-md,map}.md, each under 80 lines, moved text not new text | secret scan | aesop PR
3. docs | skipped | STAGE-CONTRACTS.md gains category `aesop-tool` (pytest via tools/ci_shard_runner.py, gates = LANE-CONTRACT s5) and a MAP field (s5 below) | secret scan | aesop PR
4. docs | skipped | STATE.md INTENT: one block per G-id, 400-char lines, verbatim quotes moved to ruling events with the seq cited | secret scan | aesop PR (orchestrator-owned file: orchestrator dispatches with STATE.md named)
5. docs | skipped | split ui/CLAUDE.md (6 concerns), hooks/CLAUDE.md (5 families), mcp per-tool reference to docs/MCP-TOOLS.md, archive the wave-32 refactor logs from ui and driver | primitive_lint report mode shows 0 for these files | aesop PR
6. docs | skipped | split LANE-CONTRACT.md into lane (s1, s6, s7, s8) with gate text moved to docs/primitives/gate.md and test text to tests/CLAUDE.md | secret scan | aesop PR

---

## 3. Hooks per domain: packages and a dispatcher

Today: 59 hooks in one flat directory, 55 settings.json entries, 23 processes on every Bash call in every repo, 0 cwd-aware exits. A keyrun lane runs aesop merge gates, a docs lane runs code gates, and an aesop read runs the keyrun game-launch parser (2,808 lines, the top false-positive source in this run).

Target shape (aesop ships the mechanism, the brain holds instances; this is the "small package for per domain intelligence"):
- aesop `hookpkg/`: `dispatch.mjs` (the ONLY script registered in settings.json, one entry per event), `manifest.schema.json`, `resolve.mjs` (project and domain from cwd, tool_input paths, worktree path, and the dispatch brief's MAP field), and tests. Published in the npm package.
- brain `hooks/packages/<domain>/`: `manifest.json` (`domain`, `primitive`, `fires_on: {events, tools, projects, path_globs, stages}`), the hooks, their tests, `INDEX.md`, `CLAUDE.md`. Domains: git-merge, dispatch, orchestrator-context, brain-state, secrets, keyrun-runtime, self-protection, ledger.
- Firing: dispatch resolves (project, domain, stage); runs only packages whose `fires_on` match. A keyrun lane never runs aesop-only gates; a docs-stage lane never runs code gates; read-only tools (Read/Grep/Glob) run only orchestrator-context and secrets.
- Fail-closed contract kept: if resolve cannot decide the project or domain, dispatch runs ALL packages (today's behavior), never none. A package that throws denies. dispatch itself is covered by `_failclosed.test.mjs`. One node process per event instead of 22.
- Every deny keeps GATE-DENY WHY/JUDGED/PASS text (memory gate-denials-carry-their-reasoning).

Retire/merge table. Nothing is deleted: "retire" means moved to `hooks/archive/` after a measured zero (0 deny and 0 allow rows in the resistance ledger for 14 days AND not registered). Evidence column is the resistance ledger 2026-10-15..10-23 unless noted.

| Hook | Domain package | Action | Evidence |
|---|---|---|---|
| no-orchestrator-merge-train, block-bare-pr-merge, deny-stacked-auto-merge, deny-default-branch-push, pr-base-main-gate, gate-file-arm-guard | git-merge | merge into one rule module set, one process | 229 + 208 + 135 deny; pr-base-main-gate 1 deny but unregistered; gate-file-arm-guard never registered |
| block-forbidden-git-flags, git-stash-block, block-git-hooks-bypass, git-history-guard, primary-branch-guard | git-merge | merge (shared `_gitgate` parser already exists) | 13 separate Bash processes today |
| no-lane-game-launch | keyrun-runtime | keep; fire only when project=keyrun or path under keyrun; read-only commands short-circuit before the UNDECIDABLE parser | 4 of 6 readers denied on read-only commands this run |
| force-haiku-subagents, model-lever, route-to-specialists, purpose-pin-gate, inject-lane-contract, dispatch-model-ledger | dispatch | keep; one process; purpose-pin classifies read-only (`subagent_type` read-only or no Write/Edit verbs) as non-build | purpose-pin 85 deny incl. this run's read-only reader |
| lane-ledger (4 registrations), saturation-gate, no-serial-pileon, no-monitor-in-lanes, capture-open-findings, task-event-projector, handback-ledger | ledger | one registration per event; keyrun floor in saturation-gate becomes a project-scoped rule | 47 deny |
| main-context-ceiling, no-grep-on-main-thread, no-scrollback-as-record, checkpoint-before-clear, checkpoint-before-compact, reprime-from-disk, session-end-* | orchestrator-context | merge the two main-thread read guards; fire only for the main thread, never in lanes | 15 deny |
| claim-binding, verdict-ledger, rule-binding, buildlog-append-guard, knowledge-tree-current, auto-commit-brain, new-code-needs-purpose | brain-state | keep; knowledge-tree-current fires only in brain repos; buildlog guard fires only on commands that write BUILDLOG | claim-binding 351 (top denier: review denial text), kt 158 |
| enforce-rules, block-repo-settings-writes, no-credential-hunting, bash-deny-policy | secrets | re-register no-credential-hunting explicitly (32 deny with no registration found); bash-deny-policy: measure | 178 + 32 |
| hooks-edit-in-worktree, hooks-edit-in-worktree-shell | self-protection | merge; fire on writes only, not reads naming the path | 1 false positive this run |
| stop-central-time, usage-kill-switch, no-polling, no-cli-api-calls, no-heredoc-file-authoring, no-browser-restart, claim-measured-cap, gate-read, handback-ack | per domain on review | measure 14 days; retire to archive only on a measured zero | stop-central-time 5 deny but unregistered; stage-verb-gate ghost (4 deny, no file): find the registering settings file first |

Pipelines:
1. aesop-tool (node) | hookpkg/dispatch.test.mjs: fixture packages; a keyrun-cwd Bash call runs keyrun-runtime + git-merge only; an unresolvable cwd runs ALL; a throwing package denies (red: absent) | hookpkg/{dispatch,resolve}.mjs + schema | node --test + fail-closed suite | aesop PR, npm next minor
2. hook | packages/git-merge parity test: every rule of the 11 git hooks produces the same allow/deny on the replay of 30 days of resistance-ledger Bash inputs (red: package absent) | move rules into packages/git-merge, old files become 3-line shims | node --test + replay | claude-config PR (settings.json unchanged)
3. hook | resolve test: read-only Bash containing "keyrun" in an aesop cwd is not evaluated by keyrun-runtime (red today) | no-lane-game-launch manifest scope + read-only short-circuit | node --test + replay of this run's 4 denials (must now allow) and of every real launch deny (must still deny) | claude-config PR
4. hook | purpose-pin test: a read-only fleet-auditor brief naming a PR number is not a build lane (red today) | classifier rule | node --test | claude-config PR
5. hook | settings parity test: settings.json registers only `hookpkg/dispatch.mjs` per event and every package manifest is reachable (red today) | switch settings.json to the dispatcher (A/B: 24h shadow mode where dispatch logs its decision next to the old chain, then cut over) | node --test + 24h shadow diff = 0 missed denies | claude-config PR (hard gate: shadow diff evidence attached)
6. hook | ghost/unregistered test: every hook with ledger rows is registered in settings or a package (red: no-credential-hunting, stop-central-time, pr-base-main-gate, stage-verb-gate) | register or locate | node --test | claude-config PR
7. script | tests/test_hook_retire.py: a hook with 0 rows in 14 days and no registration is listed; one with any row is not (red: absent) | ~/scripts/hook_retire_report.py (report only, moves need Matt row approval) | script gates | claude-scripts PR

---

## 4. Memory and incident recursion as machinery

Today: 202 memories, 44 incidents, 81k ledger rows, 1,513 denies, and no step ever turns a repeated signature into a generic plus a check. About 56 memories restate a mechanism that already exists; they cost recall and teach nothing the hook does not already enforce.

The mechanism, `tools/recurse.py` (aesop, product), the CI-CATALOG shape generalized:
- Inputs: memory .md files, INCIDENTS rows (later: incident events in the project store), resistance ledger denies, CI-CATALOG rows.
- Signature: (category, mechanism file named, normalized title tokens, deny reason_class). Cluster by equal mechanism first, then token overlap >= 0.5.
- Output 1: `docs/RECURSION-CATALOG.md`, one row per cluster: `id | category | signature (verbatim member titles) | root cause | recipe | mechanism (hook/test/script path) | members | status`.
- Output 2: for a cluster with no mechanism, a stub S1 SPEC brief (category + the red test to write) appended as an open_findings row (source=recurse, parent G-id). Never writes code itself.
- Output 3: for a memory whose mechanism exists AND has a test, a retire line for the memory-keeper (single writer): move to `memory/aesop/archive/` with a pointer line in the mechanism's package INDEX.md. Nothing is deleted.
- Runs at every wave close (buildsystem/runwave close step) and prints counts: clusters, new generics, rows emitted, memories retired. A wave close with recurse output missing fails the close check (fold into the existing wave-close step, no new hook).

Retire first (mechanism exists, per the memory reader; keeper confirms each has a test): orchestrator-never-runs-merge-trains, auto-merge-is-default, ceiling-hook-first-bash-after-clear, no-grep-hook-stdin-filter, no-grep-even-in-auto-mode, never-override-haiku-dispatch, secret-scan-empty-set-blesses-no-inspection, memory-canonical-tree, hard-checkpoints-and-clears- enforced, never-author-files-via-bash-heredoc, pre-push-hook-blocks-tag-push-from-main, doc-gates-induce-fabricated-docs, subagentstop-additionalcontext-rewakes-agents, incidents-go-to-the-log-not-md, kill-switches-need-behavioral-proof, conflict-resolution-must-be- three-way (25 candidates in the reader report; 4 name files that do not exist and stay).

First generics the clusters already show:
- Merge conflict (21 of 44 incidents): recipe = LANE-CONTRACT s3 three-way + pr_symbol_survival_check + regen drivers; mechanism exists; incident rows close against it.
- Test-count drift in tests/CLAUDE.md (7): the counter moves to a generated file (generated_paths REGISTRY), so no lane edits it; S1 red test = a merge of two branches each adding a test leaves the counter right.
- Hook false positive on read-only commands (this run: 4 readers): generic = "read-only short-circuit before deny", one shared lib function every Bash package calls; mechanism = s3 pipeline 3.

Pipelines:
1. aesop-tool | tests/test_recurse.py: fixture of 6 memories (3 share a mechanism) + 4 incidents yields 2 catalog rows, 1 stub finding, 3 retire lines; rerun is idempotent (red: absent) | tools/recurse.py + docs/RECURSION-CATALOG.md header | aesop-tool gates | aesop PR
2. memory | check: the 16 listed memories each name an existing mechanism with a test (red for any that fail: those stay) | memory-keeper moves the passing ones to archive with pointers | secret scan | claude-config commit (keeper only)
3. aesop-tool | tests/test_generated_counter.py: two branches each add one test, merged counter is +2 with no hand edit (red: counter hand-edited today) | move tests/CLAUDE.md counts into a registered generated file | aesop-tool gates | aesop PR
4. docs | skipped | runwave/buildsystem close step runs `python tools/recurse.py --wave-close` and records its counts in the checkpoint event | secret scan | aesop PR (skills/)

---

## 5. Maps first: why lanes still search, and the fix

Why lanes still search and read (each cause measured):
1. No per-domain map exists. The only maps are project-level (PROJECTS.md, SYSTEM-MAP.md); MACHINERY-MAP.md is absent. A lane gets "aesop is a Node and Python repo", never "domain tools/ owns these files, these gates, these tests".
2. maps.mjs matches project aliases and file tokens only, emits rows in map-file order, and caps at 2,000 bytes; this brief received 4 rows and dropped 49. Relevance plays no part.
3. The STAGE-CONTRACTS matrix has no MAP field, so a filled stage brief names a category and a test path but no map key; the injector has nothing domain-shaped to match.
4. The primary checkout lags origin/main (docs/STAGE-CONTRACTS.md and docs/plans/PER-PROJECT- EVENT-STORES.md are on origin/main, absent in the primary tree). A lane told "read X" on the primary finds nothing and searches. Three of six readers hit this today.
5. Hook false positives on read-only commands (s1 friction) push lanes from one `wc` to many Greps.
6. inject-lane-contract never denies a brief without a map pointer (maps.mjs "never denies").

The fix:
- Generated per-domain maps: `tools/domain_map.py` reads each domain CLAUDE.md (front matter from s2 plus its Files section) and writes `docs/maps/<project>/<domain>.md` rows: `domain | owned files | primitive | gates (commands) | tests (paths) | owner .md`. Registered as generated paths; regen on main. One generator serves aesop, keyrun and the brain (it takes `--root`).
- Brief template: every STAGE-CONTRACTS cell gains `MAP <project>/<domain>`. The orchestrator fills it from the plan row (s8), not from memory.
- Injector: maps.mjs keys on the MAP field first and injects that domain's map file whole (cap per domain, 1,500 bytes, no cross-domain rows); project rows only when MAP is absent. The brief then carries the map rows it needs and nothing else.
- Gate (folded into inject-lane-contract, no new hook): a stage brief (S1..S4) with no MAP field, or whose MAP key resolves to zero rows, is denied with WHY/JUDGED/PASS; read-only readers (fleet-auditor, Explore) are exempt but receive the map when the brief names one.
- Worktree rule in every brief: read maps from the lane's worktree (origin/main), never the primary.

Pipelines:
1. aesop-tool | tests/test_domain_map.py: a fixture repo with 2 domain CLAUDE.md files yields 2 map files with files/gates/tests rows; `--check` is red after a file is added without regen (red: absent) | tools/domain_map.py + generated docs/maps/aesop/*.md | aesop-tool gates + gen_tool_index | aesop PR
2. hook | maps.test.mjs: a brief with `MAP aesop/tools` gets only docs/maps/aesop/tools.md rows and no PROJECTS rows (red today) | maps.mjs MAP-field keying + per-domain cap | node --test + _failclosed | claude-config PR
3. hook | inject-lane-contract.test.mjs: an S2 IMPL brief with no MAP field is denied; a fleet-auditor reader is allowed (red today) | deny branch with GATE-DENY text | node --test + 7-day ledger replay (count of briefs that would have been denied, attached) | claude-config PR
4. docs | skipped | STAGE-CONTRACTS cells gain MAP; dispatch-templates.md stage briefs gain MAP and "read from your worktree" | secret scan | aesop PR + claude-config PR
5. aesop-tool | test: domain_map `--root` on a keyrun fixture writes docs/maps/keyrun/<domain>.md (red: aesop-only) | keyrun maps generated in keyrun repo | keyrun fastcheck | keyrun PR

---

## 6. Per-project orchestration, end to end (G1.1)

What Matt asked, each time:

| When | Where | Ask | Became |
|---|---|---|---|
| 2026-10-17 | STATE.md:459 (D11) | per-project grants (keyrun Opus) | grants.md row |
| 2026-10-18 02:00Z | BUILDLOG.md:746, STATE.md:409 | "recursive per-project orchestrator lanes" | a PLAN line only; no ledger row, no lane |
| 2026-10-22 ~17:5xZ | events 2026-10-22 seq 254 (R-EVENTSTORE-1), 255 (G1.1 pinned) | "decouple them and manage per project ... enables the multi orchestartion pattern" | plan #1279, rulings |
| 2026-10-22 18:01Z | seq 268 (R-EVENTSTORE-2) | accepts the plan's five calls | lanes L1-L3, L5 |
| 2026-10-23 01:11Z | 2026-10-23 seq 13 | "suggested probably 4 times now and you built out like one file" | this plan |

The event store only starts 2026-10-22, so the earlier asks exist only in prose; that gap is part of the cause.

What exists vs missing:

| Element (plan section) | State | Evidence |
|---|---|---|
| store core, registry resolver, append CLI (2.1, L1) | SHIPPED | #1280 |
| init + append-only guard (L2) | SHIPPED | #1282 |
| migrate split of v1 log (L3) | SHIPPED | #1281 |
| projections across stores (L5) | SHIPPED | #1284 |
| PreCompact reads every store (L6) | SHIPPED, DORMANT | claude-config #279; no registry file, so it falls back to one store |
| registry `~/.claude/event-stores.json` (2.4) | MISSING | file absent |
| per-project store branches events/aesop, keyrun, psinasty, brain (2.2) | MISSING | conductor3 heads: events/log, events/log-freeze-writer only |
| freeze v1 writer (L7) | PARTIAL | code on events/log-freeze-writer 2bbc4f0, not merged, no frozen note |
| cutover (4.1) | MISSING | no FROZEN note, no migrated_genesis events |
| verify-run for bot receipts (K3, runs.py) | MISSING | no tools/event_store/runs.py |
| keyrun bot reasons + receipt sink (K1, K2) | UNVERIFIED | keyrun not inspected |
| one orchestrator per project (2.6) | MISSING | no CLI, no session shape, no lane |
| per-domain hook firing per project | MISSING | s3: 0 cwd-aware hooks |
| per-project checkpoints (2.5) | MISSING | depends on registry |

The mechanism that dropped it (so the fix is mechanical):
1. A plan's lane table is not a ledger. #1279 merged and closed the conversation; its 11 lane rows never became open_findings rows, so idle_tick, saturation and the refinement monitor never saw the unbuilt ones.
2. Steps marked "orchestrator-run, not a lane" (cutover) and files outside every lane's ownership (the registry under ~/.claude) have no owner and no row; they are exactly the missing pieces.
3. Asks recorded as "PLAN:" in BUILDLOG/STATE prose are never rows (2026-10-18 02:00Z).
4. f-e9e467a8 is one parent row with no per-lane observable, so it sits PARTIAL forever. Fix: `plan_rows.py` turns every pipeline row of a merged plan file into a child ledger row (parent G-id, observable = the S4 ship target merged plus the named live check); a parent row cannot close while a child is open; "orchestrator-run" is not a valid row owner (every row is a stage lane).

Pipelines (product first):
1. aesop-tool | tests/test_event_store_registry_init.py: `event_store init-all --home <tmp>` creates four stores and writes a registry the PreCompact reader accepts (red: no init-all) | tools/event_store/registry.py init-all + docs | aesop-tool gates | aesop PR
2. script | conductor3 tests/test_event_store.py frozen case passes on events/log-freeze-writer | merge 2bbc4f0 via PR | conductor3 tests | conductor3 PR
3. aesop-tool | tests/test_event_store_cutover.py: dry-run over a v1 fixture prints per-project counts summing to the source and writes nothing (red: no cutover command) | tools/event_store/cutover.py --dry-run / --apply (apply refuses without a dry-run receipt) | aesop-tool gates | aesop PR; the --apply run is an S4-style lane after Matt reads the dry-run counts
4. aesop-tool | tests/test_event_store_runs.py: the four 3.3 mutants (tampered line, wrong head, guided seat with policy rule, missing end) each fail verify-run (red: absent) | tools/event_store/runs.py | aesop-tool gates | aesop PR
5. keyrun-bots | K1 test: every BotBatch command carries one stable rule id; fingerprint identical with and without a sink | Bot.cs reasons | keyrun fastcheck | keyrun PR
6. aesop-tool (node) | bin test: `aesop orchestrate --project keyrun --dry-run` prints the session plan: store path, STATE.md path, map root, hook packages that fire (red: no subcommand) | bin/ orchestrate subcommand; SessionStart reprime reads only that project's store and INTENT | node tests | aesop PR, npm next minor
7. aesop-tool | tests/test_plan_rows.py: a merged plan with 5 pipeline rows yields 5 child rows under its G-id; closing the parent with an open child exits 1 (red: absent) | ~/scripts/plan_rows.py (or aesop tools/, decided at S1 by where open_findings.py lives) | script gates | claude-scripts PR
8. hook | precompact test: with a registry of two stores, the compaction summary lists open plan rows per project by G-id (red: dormant) | checkpoint-before-compact reads plan child rows | node --test | claude-config PR

---

## 7. Dropped-asks reconciliation (source=matt rows and GOALS-tree verbatims)

54 matt rows: shipped 18, partial 31, not shipped 5. Product rows are ranked first.

| Rank | Ask (row / goal) | Class | State | Evidence |
|---|---|---|---|---|
| 1 | Per-project event stores + multi-orchestration (f-e9e467a8, G1.1) | product | PARTIAL | #1279-#1284 merged; nothing live (s6) |
| 2 | Ability spam root cause (f-47b1f102, f-c356528f, G2.1) | product | PARTIAL | selector #658; cause not named |
| 3 | D65 playable tutorial + second dungeon (f-b154c251) | product | PARTIAL | #612, #626; closes only on Matt's play |
| 4 | AoE kit + filler (f-c8ed3a62, f-c04e73a8, R-K4-1) | product | PARTIAL / NOT | #642/#643 not merged |
| 5 | Class toolkit, one mobility minimum (f-53a7aa83, G2.1 amended) | product | PARTIAL | validator #649, one displace op #645 |
| 6 | Improvement trees (f-6fec76e6, G2.2) and gear roll (G2.3) | product | PARTIAL | design only #638 |
| 7 | Dungeon synergy axis (G2.5) | product | PARTIAL | no PR found |
| 8 | Keyrun play asks f-8fc6e252, f-2514824e, f-c03a7b26 | product | PARTIAL | #592, #593, #604; no Matt replay verdict |
| 9 | Dynasty full league image, buys/sells, other-team failure modes (f-a6474198, f-509ea311, f-37d4b166, f-962d0445, G3/G3.1) | product | PARTIAL | R-DYNASTY-1..3 #152-#154 |
| 10 | Resume/portfolio plan (G4.2) | product | NOT SHIPPED | no plan artifact |
| 11 | aesopkernel.ai site (f-fee7b18a, G4.1) | product | SHIPPED | repo pushed, HTTP 200 (content unchecked) |
| 12 | Provenance release (f-a518aaf6, f-29d1eb37, f-d5da6ffc, f-ebd0616f, G1.2) | product | SHIPPED | v0.11.0 #1285, #1286 |
| 13 | Playtester guide modes (G2.4) | product | SHIPPED (rows) | per reader; K1/K2 receipts unverified |
| 14 | Architecture plan (f-d11ba28f, G6) | machinery | NOT SHIPPED | this PR |
| 15 | Stage lanes (G5, R-LANES-1) | machinery | PARTIAL | #1291, #1292 docs; the dispatch-hook enforcement is not built |
| 16 | CI sweep + templates (f-a730b75d, f-80a5d597, G1.3) | machinery | SHIPPED | #1287-#1289 |
| 17 | Linearization rules (f-2d678eeb) | machinery | PARTIAL | base gate #283; rules ii-v docs only; pr-base-main-gate unregistered |
| 18 | /power step 7 (f-dd11a9f0) | machinery | NOT SHIPPED | no PR |
| 19 | Fault-tolerance discussion (f-8948fa1d) | machinery | NOT SHIPPED | memory note only |
| 20 | Smart App Control (f-37ad66b3) | env | NOT SHIPPED | Matt's machine setting |
| - | Ledger drift: G3.1 cites f-f5d97be8, f-5f5815ea, f-4d263c1c, not in the ledger | defect | - | open_findings list |
| - | G0 mission and MEMORY TIERS verbatims | rule | no row | they are rules, not deliverables; s4/s8 implement MEMORY TIERS |

Remaining shipped machinery rows (f-049fb99d, f-a5ffb07b, f-1232d0aa, f-9607c9c0, f-e0650754, f-c1044aa4, f-f152efbb, f-4c11c408, f-a7c40b82, f-20cb0a8c) are listed in the reader output only.

Pipelines:
1. script | tests/test_open_findings_children.py: `list --json --tree` groups child rows under each matt parent and flags a parent cited in STATE.md but absent from the ledger (red: the three G3.1 ids) | open_findings.py tree view + drift check | script gates | claude-scripts PR
2. docs | skipped | ledger rows for G2.5, G4.2 and the three missing G3.1 ids, each with an observable | secret scan | claude-scripts ledger commit by the orchestrator
3. hook | test: saturation-gate counts product rows separately and denies a wakeup when an unblocked product row has no lane while a machinery lane is live (red today) | product-first rule in saturation-gate | node --test + replay | claude-config PR

---

## 8. Wave protocol

1. A planning team (one planner, up to six read-only Haiku readers with map-named briefs) writes `docs/plans/<NAME>.md` in this shape: sections ending in pipeline rows.
2. Matt approves by merging the plan PR (or names rows to drop). The orchestrator never designs a wave without a merged plan; designing is the planning team's stage.
3. plan_rows.py (s6.7) turns every row into a child ledger row under its G-id with id `<plan>#<section>.<n>`.
4. The orchestrator sequences stage lanes from those rows only: each brief carries `PLAN-ROW <id>`, `MAP <project>/<domain>` and one stage's verbs. It verifies MERGED from gh state and closes the row on its observable.
5. The refinement monitor measures each wave from disk: rows planned vs merged, product vs machinery merges, denies per package, recurse.py counts. It reports; it does not redesign.
6. Gate (folded into purpose-pin-gate, no new hook): a build-stage brief (S1, S2, S4) with `status: real` and no `PLAN-ROW` id that resolves to an open child row of a merged plan is denied with WHY/JUDGED/PASS. Exempt: read-only readers, ci-fix rows that cite a CI-CATALOG id, memory-keeper, and a P0 wrong-data row (P0 cannot wait for a plan).

Pipelines:
1. hook | purpose-pin test: an S2 brief with no PLAN-ROW is denied; with a PLAN-ROW of an open child row it is allowed; a ci-fix brief citing CI-A5 is allowed; a P0 row is allowed (red today) | plan-row branch in purpose-pin-gate reading the ledger | node --test + 7-day replay count attached | claude-config PR
2. docs | skipped | skills buildsystem/runwave: steps 1-6 above replace "rank backlog -> dispatch"; planning-team template in ~/.claude/docs/dispatch-templates.md | secret scan | claude-config PR
3. aesop-tool | tests/test_wave_measure.py: fixture ledger + gh JSON yields planned/merged/product/machinery counts (red: absent) | tools/wave_measure.py used by the refinement monitor | aesop-tool gates | aesop PR

---

## 9. Order and risk

First three pipelines after approval (product first):
1. s6.1 `event_store init-all` + registry (aesop-tool, G1.1): per-project stores exist and the dormant PreCompact multi-store reader turns on. Followed by s6.2 freeze merge and s6.3 cutover dry-run.
2. s6.6 `aesop orchestrate --project <p>` (aesop-tool, G1.1 multi-orchestration): the product surface Matt asked for four times, shipped in the npm package.
3. s6.7 plan_rows.py (script): the mechanism that dropped G1.1 becomes a check; every later row of this plan is then tracked as a ledger child and cannot silently disappear. Then, in order: s3.3 + s3.4 (the two false-positive fixes measured in this run), s5.1-s5.3 (maps), s3.1 + s3.2 + s3.5 (dispatcher and packages, shadow mode first), s4.1 (recurse.py), s2.1 (lint, report-only), s8.1 (plan-row gate), then the docs splits. Keyrun product rows (AoE #642/#643, G2.1 cause) keep running on their own G2 order; this plan does not pause them.

Reversible: every pipeline is a PR; packages keep 3-line shims until the dispatcher's 24h shadow diff shows zero missed denies; memory and hooks move to archive/ and are never deleted; the cutover keeps the frozen v1 log verifiable; primitive_lint ships report-only.

Waits for Matt: approving this plan PR; reading the cutover dry-run counts before `--apply`; approving each hook retirement row (measured zero attached); the settings.json cutover to the dispatcher (hook change with A/B shadow evidence); plan decision 1 of PER-PROJECT-EVENT-STORES (store location) stays as accepted at seq 268 unless he overrules.

Risk: the dispatcher is a single point; fail-closed (resolve unknown -> run all) bounds it, and the shadow week measures it. The plan-row gate could block urgent work; P0 and ci-fix exemptions and the replay count before arming bound that.

Pipelines (the first three, restated for sequencing):
1. aesop-tool | tests/test_event_store_registry_init.py red | tools/event_store/registry.py init-all | aesop-tool gates | aesop PR
2. aesop-tool (node) | bin orchestrate --dry-run test red | bin/ orchestrate subcommand | node tests | aesop PR
3. script | tests/test_plan_rows.py red | plan_rows.py | script gates | claude-scripts PR
