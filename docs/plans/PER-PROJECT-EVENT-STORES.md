# Per-project event stores (G1.1, ruling R-EVENTSTORE-1)

Status: plan only. No code, hook or live-store change ships with this file.
Goal: G1.1 in aesop STATE.md INTENT. Ruling R-EVENTSTORE-1 is seq 254 of the live store.
Matt 2026-10-22, verbatim: "the play testers need to follow the same paradigm, actions left on disk in
the event store, it can be a seperate event store but i just realized that is the whole issue. We have
one event store... we need to decouple them and manage per project.. aesops job but that is causing
extrme confusion and long reconcilliations, this also enables the multi orchestartion pattern".

The defect: one hash-chained log holds every project's rulings, PR states and checkpoints. 74 of its
256 events cannot be attributed to a single project by any ref (section 1.4), and the PreCompact hook
replays all of it into every session whatever the session is working on.

All numbers below were measured read-only on 2026-10-22 against
`C:/Users/Mattt/conductor3-wt/events-log` (branch `events/log`, head `d406189`, one day file,
`OK 2026-10-22.jsonl lines=256`, last ts 2026-10-22T17:35:47Z). The census script was run with
`python -I` over the jsonl and wrote nothing. The store grew from 255 to 256 lines while the census
ran, so the migration tool recounts at cutover. These numbers are a snapshot.

---

## 1. Inventory

### 1.1 The store module (conductor3, branch `events/log`)

| File | Line | What |
|---|---|---|
| `tools/event_append.py` | 26 | `ROOT` = the checkout the script lives in. The store is wherever the script is. |
| `tools/event_append.py` | 28-32 | `KINDS` (9 kinds) and `EVENT_KEYS` (exact key set; any extra key breaks the chain) |
| `tools/event_append.py` | 51 | `check_goal_ref`: a ruling needs `goal=G<n>[.<m>]` (exit 2) |
| `tools/event_append.py` | 83 | `verify_lines`: `seq == line index` and `prev == sha256(previous line)` **per day file** |
| `tools/event_append.py` | 122-170 | `append_event`: verify, append one line, `git add` + `git commit` (157), and truncate the line back out if the commit fails |
| `tools/event_append.py` | 173 | CLI `--kind --actor --text --ref --session`, `--verify FILE` |
| `tools/event_project.py` | 28 | `load_all`: every `events/*.jsonl`, verifies each chain first |
| `tools/event_project.py` | 48 / 67 / 93 / 100 | `pending`, `fleet`, `describe` (ruling suffix ` [goal=G<n>]`), `run` (rulings, checkpoints --last N, all --since) |
| `tools/hooks/events_append_only.sh` | 12, 17 | pre-commit guard: only added files and appended lines pass |
| `events/README.md` | 6-22 | Event shape and kinds |
| `events/.gitattributes` | 3 | `*.jsonl -text` (lines are hashed byte for byte) |
| `tests/test_event_store.py` | 68 / 105 / 173 / 213 | TestAppend, TestGoalRef, TestProjections, TestAppendOnlyGuard |

`events/log` is not an orphan branch. It carries conductor3's 1008-commit history, so the store data
and the store code are versioned together on one branch.

### 1.2 Writers

| Writer | Where | Evidence |
|---|---|---|
| Orchestrator main thread, by hand | `python -I tools/event_append.py ...` run in the events-log worktree | actors in the chain: `orchestrator` 240, `matt` 16. Matt's events are the orchestrator quoting him; Matt does not run the tool. |
| (none else) | | A search for `event_append`, `event_project`, `EVENT_STORE_ROOT`, `events-log` and `events/log` found no writer in `~/.claude/hooks`, `~/scripts` (zero matches), `~/.claude/{skills,docs,agents,commands,scripts}` (zero matches) or the aesop repo. In aesop the only hit is STATE.md line 13, the G1.1 text. |

The watchdog pushes conductor3. `event_append.py` never pushes. Origin `events/log` is at
`d4061899`, the same head as the local branch.

### 1.3 Readers

| Reader | File:line | What it reads |
|---|---|---|
| PreCompact hook | `~/.claude/hooks/checkpoint-before-compact.mjs:62` | `EVENT_STORE_DEFAULT = 'C:/Users/Mattt/conductor3-wt/events-log'`, overridden by `EVENT_STORE_ROOT` (`:314`) |
| PreCompact hook | `checkpoint-before-compact.mjs:313-331` | `runEventProject`: spawns `python -I <root>/tools/event_project.py <args>` and surfaces `CHAIN BROKEN` verbatim |
| PreCompact hook | `checkpoint-before-compact.mjs:337` | `checkpointSection`: `checkpoints --last 3`, with the scratchpad ledger as a named fallback |
| PreCompact hook | `checkpoint-before-compact.mjs:351` | `rulingsSection`: `rulings`, last 12 |
| PreCompact hook | `checkpoint-before-compact.mjs:428` | `groupRulings(rulingsOut.all)`: the by-goal section |
| goals lib | `~/.claude/hooks/lib/goals.mjs:25, 91, 122` | `GOAL_SUFFIX_RE`, `splitGoalRef`, `groupRulings`. Parses the ` [goal=G<n>]` suffix from `event_project.py rulings`. |
| hook tests | `~/.claude/hooks/checkpoint-before-compact.test.mjs:16, 618-652, 792-803` | Pin `EVENT_STORE_ROOT` to a scratch dir and copy a stub `tools/event_project.py` into it |

Two items in the brief do not match the code:
- The brief lists `checkpoint-before-clear.mjs` as a reader. It has no event-store reference. Its
  only match for "event" is the comment `Event: PreCompact`.
- The brief describes keyrun as having an `events/log` branch. It has none: `git ls-remote --heads
  origin "events/*"` is empty for keyrun, aesop and claude-config. Only conductor3 has `events/log`.

Two stores sit next to this one and stay out of scope:
- `~/.claude/state/task-events.jsonl` (read at `checkpoint-before-compact.mjs:117`) has no chain.
- aesop `state_store/` is SQLite WAL with per-stream versions and OCC. It backs the tracker.
  Decision 3 in section 6 says why this plan does not move the rulings log into it.

### 1.4 What is in the chain (256 events, 2026-10-22T00:29:30Z .. 17:35:47Z)

Kinds: checkpoint 129, pr_state 54, ruling 49, note 13, verdict 6, finding 2, dispatch 2, release 1.

Ref keys: 109 distinct keys. The common ones are `repo` 96, `pr` 89, `sha` 57, `kind` 45,
`seq_prev` 43, `finding` 34, `prs` 26, `ruling` 16, `goal` 13. The other 100 keys are ad-hoc and
mostly encode a repo in the key name, for example `keyrun_pr_606`, `cc_266_merge` or `aesop1273`.
Keys that name a repo: keyrun-prefixed 30, `cc*`/`cs*` (claude-config, claude-scripts) 20,
aesop-prefixed 5. This key sprawl is a second defect. The new append command fixes it by taking the
project from `--project`, so a repo name never has to be packed into a ref key (section 2.3).

**Events per goal ref** (13 events carry one):

| goal | events | by kind |
|---|---|---|
| G0 | 3 | checkpoint 3 |
| G1 | 4 | checkpoint 3, ruling 1 (R-EVENTSTORE-1) |
| G2 | 6 | checkpoint 4, ruling 2 |
| G3 | 1 | checkpoint 1 |

Only 3 of the 49 rulings carry a goal ref. The requirement was added mid-day, and the earlier 46
predate it.

**Events per `repo` ref value** (96 events carry one): keyrun 69, aesop 14, claude-config 10,
conductor3 2, claude-scripts 1.

**Attribution under the migration rule in section 4.2**, applied to all 256 events:

| project | total | goal | repo ref | repo-named ref key | text names exactly one project | text names several projects (whole event to aesop) | no project named (aesop by default) |
|---|---|---|---|---|---|---|---|
| aesop | 121 | 7 | 14 | 3 | 5 | **74** | 18 |
| keyrun | 111 | 6 | 69 | 11 | 25 | - | - |
| brain | 21 | 0 | 13 | 4 | 4 | - | - |
| psinasty | 3 | 1 | 0 | 0 | 2 | - | - |

By kind after the split:
- aesop: checkpoint 88, ruling 16, note 6, pr_state 4, verdict 3, finding 2, dispatch 1, release 1
- keyrun: pr_state 41, checkpoint 33, ruling 28, note 5, verdict 3, dispatch 1
- brain: pr_state 9, checkpoint 7, ruling 3, note 2
- psinasty: checkpoint 1, ruling 2

82 checkpoints have text that names two or more projects. These are the fleet-wide checkpoints, one
event describing all four workstreams. This is the "extreme confusion" in mechanical form: a project
orchestrator cannot read its own state without also reading every other project's.

---

## 2. Target shape

### 2.1 aesop ships the store module

New package `tools/event_store/` in aesop. It is Python, standard library only, and run with
`python -I`:

| Module | Job |
|---|---|
| `store.py` | `Store(path)`: reads `store.json` (`{"project": "keyrun", "schema": 2}`), `append()`, `verify()` (v1 per-day chains and v2 global chains), `load()` |
| `project.py` | Projections: `pending`, `rulings`, `fleet`, `checkpoints --last N`, `all --since`. Works over one store or over every store in a registry. |
| `registry.py` | Reads the store registry (2.4) and maps a goal id to a project |
| `cli.py` | `python -I tools/event_store/cli.py append|verify|project|init|migrate|verify-run ...` |
| `init.py` | Creates a store: orphan branch, worktree, `store.json`, `.gitattributes *.jsonl -text`, and installs the guard |
| `migrate.py` | The one-time split (section 4) |
| `guard/events_append_only.sh` | The pre-commit guard, ported unchanged from conductor3 `tools/hooks/events_append_only.sh` |

Store branches hold data only (`store.json`, `events/`, `runs/`, `README.md`). The code lives in
aesop. Readers run it from a pinned checkout named in the registry (`tool` key). That checkout is a
worktree at an aesop release tag. It is never the diverged primary tree.

**Schema v2** (new stores only; the frozen store stays v1):
`{"v":2,"ts","seq","kind","actor","session","text","refs","prev"}`.
- `seq` counts across the whole store, not per day.
- The first line of a new day file has `prev` = the sha of the last line of the previous file.

In v1, every day file starts again from `prev:""`. Deleting a whole earlier day therefore verifies
clean, and only the pre-commit guard would catch it. v2 closes that hole.

New kinds:
- `playtest_start`, `playtest_end` (section 3)
- `migrated_genesis`, `migrated_close` (section 4)

### 2.2 Store location: one private store repo, one orphan branch and one worktree per project

| Project | Branch (in `matt82198/conductor3`, PRIVATE) | Worktree |
|---|---|---|
| aesop | `events/aesop` | `C:/Users/Mattt/conductor3-wt/events-aesop` |
| keyrun | `events/keyrun` | `C:/Users/Mattt/conductor3-wt/events-keyrun` |
| psinasty | `events/psinasty` | `C:/Users/Mattt/conductor3-wt/events-psinasty` |
| brain (claude-config, claude-scripts, conductor3) | `events/brain` | `C:/Users/Mattt/conductor3-wt/events-brain` |
| (frozen) | `events/log` | `C:/Users/Mattt/conductor3-wt/events-log` (read-only after cutover) |

Why this location and not the other two:
- **Not a branch in each project's own repo.** aesop and keyrun are PUBLIC on GitHub. The chain holds
  16 Matt-verbatim events, internal paths and finding text, and pushing `events/log` to those origins
  would publish all of it. Also, aesop's pre-push hook runs the whole gate matrix on every push, which
  makes no sense for a data-only branch.
- **Not a directory on each repo's main.** Protected main means one PR per appended event.
- **Not per-project directories on one branch.** That keeps one git index, so two orchestrators
  committing at the same moment collide on `index.lock`. It also keeps one chain-of-commits, and that
  shared write is the coupling being removed.
- **Separate orphan branches give each project:**
  - its own chain;
  - its own worktree, with its own index;
  - its own push.
- conductor3 is already private and already pushed by the watchdog.
- A store is defined by its `store.json`, not by where it sits. A project that later wants its store
  in its own private repo moves the branch and edits one registry line.

### 2.3 The append command

```
python -I tools/event_store/cli.py append --project keyrun --kind ruling --actor orchestrator \
  --text "..." --ref goal=G2.1 [--ref pr=636 ...]
```

- `--project` picks the store from the registry.
- With no `--project`, the project comes from `--ref goal=G<n>` through the registry's goal map:
  G0 and G1 go to aesop, G2 to keyrun, G3 to psinasty. Brain events need an explicit `--project brain`.
- With neither, the append is **refused with exit 2**. A new event never falls back to aesop: the
  default-to-aesop rule exists only for migrating events written before stores were split.
- The append is refused if the store worktree's `store.json` project differs from the resolved
  project. This is the single-writer check.
- `--ref repo=...` stays allowed. Ref keys that encode a repo (`keyrun_pr_606`) are refused with a
  hint to use `--project` plus `pr=`. This stops new ref-key sprawl and leaves old lines untouched.

### 2.4 The registry

`~/.claude/event-stores.json` is per-machine configuration in the brain. aesop ships its schema and an
example.

```
{"tool": "C:/Users/Mattt/Desktop/aesop-wt-events-tool",
 "stores": {"aesop": {"path": "C:/Users/Mattt/conductor3-wt/events-aesop", "goals": ["G0","G1"]},
            "keyrun": {"path": "C:/Users/Mattt/conductor3-wt/events-keyrun", "goals": ["G2"]},
            "psinasty": {"path": "C:/Users/Mattt/conductor3-wt/events-psinasty", "goals": ["G3"]},
            "brain": {"path": "C:/Users/Mattt/conductor3-wt/events-brain", "goals": []}},
 "frozen": {"conductor3-events-log": "C:/Users/Mattt/conductor3-wt/events-log"}}
```

### 2.5 PreCompact reads every project store

`checkpoint-before-compact.mjs` reads the registry instead of the one `EVENT_STORE_DEFAULT` path:
- `rulingsSection` runs `cli.py project rulings --all-stores` once. Each line ends
  ` [project=keyrun] [goal=G2.1]`.
- `groupRulings` (`goals.mjs:122`) keeps grouping by goal and shows the project inside the group
  title. A ruling with no goal (the 46 migrated ones) is grouped under its project as
  `(no goal, project=keyrun)`, so it is never dropped.
- `checkpointSection` shows the last checkpoint **per store**, up to 3 stores. The current session's
  project comes first. The session's project is the store whose project matches the cwd repo. Cwd
  `C:/Users/Mattt/aesop` is not a repo, so it maps to aesop.

Failure handling stays fail-closed and is reported per store:
- A missing or unreadable store prints `store <project> unreadable: <reason>`.
- A broken chain prints `CHAIN BROKEN <project>/<file>:<seq>`.
- Neither is ever silent.
- `EVENT_STORE_ROOT` is still honored, as "this one store only", so the existing tests at
  `.test.mjs:16` keep their isolation.

### 2.6 Multi-orchestration

- One orchestrator per project. It owns exactly one store and appends only there; the `store.json`
  check in 2.3 enforces this.
- An orchestrator reads other stores read-only, through `project --all-stores`. It never writes to
  them.
- G0 (mission) and fleet-wide decisions go to the aesop store. aesop is the kernel, and its
  orchestrator is the fleet orchestrator.
- A cross-project fact is written once, in the store of the project it changes. Other projects cite
  it with `--ref xref=<project>:<seq>` and never copy it.
- No two orchestrators share a worktree, an index or a branch.

---

## 3. Keyrun playtester receipts (G2.4)

### 3.1 Where a bot decides and acts today (keyrun `1911c618`, read-only)

| Step | File:line |
|---|---|
| Per-tick sampling of the bot seats | `game/src/HostCore/BotCommandSource.cs:150` (`Sample(int tick, Run run, List<Command> into)`) |
| One batch for every bot seat, in ascending actorId order | `game/bots/Keyrun.Bots/Bot.cs:226` (`RunBots`) |
| A role's decision | `Bot.cs:204` (`role.Decide(view, self)` or `HealerRole.TopOffCommands`) |
| Output is commands only, with no reason | `Bot.cs:31` (`record BotBatch(IReadOnlyList<Command> Commands, ...)`) |
| Command shapes | `game/sim/Keyrun.Sim/Core/Commands.cs:22, 45, 64` (`MoveCommand`, `CastCommand(Tick, ActorId, AbilityId, TargetId?, Point?)`, `TargetCommand`) |
| Determinism proof | `BotCommandSource.Fingerprint`: FNV-1a over every command; `ProofLine()` prints `BOTS done commands=N hash=...` |
| The only reason-like output today | `PING react actor=.. kind=.. tick=..` from `NoteReactions` |
| Scripted human-seat bot | `game/src/Host/ScriptedWarden.cs:53` (`Sample`), `:124` (`CleaveAnswer`) |

The run log (`keyrun-screens/hv-1911c618/hv.log`, 17152 lines) has these line types:
- `TICK` 6999
- `EVENT` 4069 (sim events as JSON)
- `CAST done actor= ability=` 1112. These lines carry no tick and no reason.
- others: `CAMERA`, `ANIM`, `PATH`, `PULL`, `RUN start ... seed=1 ... key=2`, `RUN seats human=warden bots=2:chanter,3:cinder`

There is no `RUN end` line. No guided or blind mode exists in the code: a search for "guided" and
"blind" in game/src finds only the `blinded` status effect. G2.4 adds the mode.

### 3.2 Schema

The run's anchors go in the keyrun store chain:

```
{"v":2,"kind":"playtest_start","actor":"keyrun-host","text":"hollow-vault k2 seed 1",
 "refs":{"run":"<run_id>","sha":"<keyrun commit>","dungeon":"hollow-vault","key":"2","seed":"1",
         "seats":"1:warden:human,2:chanter:bot,3:cinder:bot","mode":"2:guided,3:blind",
         "guide":"<sha256 of the guide file set, or empty>","run_file":"runs/<run_id>.jsonl"}, ...}
{"v":2,"kind":"playtest_end","actor":"keyrun-host","text":"<outcome line>",
 "refs":{"run":"<run_id>","run_head":"<sha256 of the run file's last line>","lines":"<N>",
         "fingerprint":"<BOTS hash>","result":"timed|depleted|wiped|aborted","ticks":"<T>"}, ...}
```

The decisions go in their own chained file in the same store, `runs/<run_id>.jsonl`. It has one line
per command a bot seat issues, plus one line per guide rule that fires and issues no command (a
deliberate hold):

```
{"seq":17,"tick":412,"seat":2,"role":"chanter","mode":"guided",
 "act":"cast","ability":"chanter.dissonance","target":102,"point":null,
 "rule":"guide:chanter#kick-ledger.1","why":"interrupt choir.lieutenant Choral Mending",
 "prev":"<sha256 of line 16>"}
```

- `act` is `cast|move|target|dash|hold`. `ability`, `target` and `point` come from the command
  record unchanged.
- `rule` is a stable id. Guided seats use `guide:<class>#<section>.<n>` (a rule in the per-class
  guide). Blind seats use `policy:<Role>.<branch>`, the code branch that produced the command. A blind
  seat never cites a guide rule.
- `why` is a fixed short phrase per rule id. It is not free text, so it stays deterministic.
- The run file is written by the host. It is not written by the orchestrator. It is committed to
  `events/keyrun` at `playtest_end` in one commit together with the end anchor.

### 3.3 What a gate checks from disk alone

`cli.py verify-run --project keyrun <run_id>` checks:
1. The run file chain verifies.
2. The sha of its last line equals `playtest_end.refs.run_head`, and its line count equals `lines`.
3. Every seat named `guided` in `playtest_start.refs.mode` has only `guide:` rules. Every `blind` seat
   has only `policy:` rules.
4. The guide hash in `playtest_start` equals the guide files at `refs.sha`.

A start with no end is reported `OPEN`. An end whose file is missing is `FAIL`.

Keyrun-side property gates (examples, each a keyrun lane):
- A guided Chanter with an available kick answers every Choral Mending cast with a `kick-ledger` rule.
- Two guided seats never cite the same rule id in the same tick.

Blind seats count toward the time-to-learn measure only (G2.4) and are never balance evidence.

Determinism: receipts are written to a side channel. `Fingerprint` keeps hashing commands only, so
the same seed gives the same `BOTS done` hash with or without receipts. This is test K1-a below.

---

## 4. Migration

### 4.1 Order (an orchestrator-run cutover, after lanes L1-L7 are merged)

1. **Freeze.** Append the last v1 event to `events/log`:
   `note "FROZEN: split into per-project stores, <PR url>"` with `refs.frozen=1`. From then on,
   conductor3 `event_append.py` refuses every append (lane L7). Call its seq N.
2. **Dry run.** `migrate --source events-log --dry-run` writes `manifest.jsonl`. The manifest has one
   row per source seq: `{src_seq, src_sha, project, rule}`. `rule` is one of `goal`, `repo`, `refkey`,
   `text1`, `multi`, `default`. The orchestrator reads the per-rule counts before the real run.
3. **Init.** `init` creates the four orphan branches and worktrees (2.2) and the registry (2.4).
4. **Migrate.** Each store's `events/0000-genesis.jsonl` holds, in order:
   - `migrated_genesis` with refs `{src: "conductor3/events/log", src_head, src_count: N, manifest_sha}`;
   - that project's events in source order. Each keeps `ts`, `kind`, `actor`, `session` and `text`
     byte for byte. Its refs are the source refs plus `src_seq`, `src_sha`, `split_rule`, and for
     `multi` rows a `mentions` list (`keyrun,brain`, ...). It gets a new `seq` and `prev` in the new
     chain.
   - `migrated_close` with `{count}`.

   One commit per store.
5. **Verify.** Every source seq 1..N appears in exactly one store, and the per-store `count` sums to
   N. Each migrated line's `src_sha` equals sha256 of source line `src_seq`, and its non-ref fields
   equal the source's. Every new chain and the frozen chain verify. The tool prints
   `MIGRATION OK source=N aesop=a keyrun=k brain=b psinasty=p` or exits 1. At this snapshot that would
   read `source=257 aesop=122 keyrun=111 brain=21 psinasty=3`, with the freeze note going to aesop.
6. **Switch readers.** Merge the PreCompact lane L6. Until it merges, the hook keeps reading the
   frozen store, which is still correct, just stale. Then the orchestrator appends through
   `cli.py append` only.

### 4.2 Attribution rule (first match wins; mechanical, no hand edits)

1. `refs.goal`: G0 and G1 to aesop, G2 to keyrun, G3 to psinasty.
2. `refs.repo`: `keyrun` to keyrun, `aesop` to aesop, `claude-config`, `claude-scripts` and
   `conductor3` to brain.
3. Ref keys that name exactly one repo (`keyrun*`, `cc*` or `cs*`, `aesop*`).
4. Text that names exactly one project. The patterns are: keyrun; psinasty, dynasty or sleeper;
   claude-config, claude-scripts, conductor3, `.claude/` or brain; aesop.
5. Text that names two or more projects: the whole event goes to aesop, with `refs.mentions`
   (rule `multi`).
6. Otherwise aesop (rule `default`).

A row the orchestrator thinks is misattributed is never moved. It is fixed forward by appending to
the right store a `note` that cites `--ref xref=aesop:<seq>`. Events are facts, and the manifest
stays the record of what the rule did.

### 4.3 The old store

`events/log` stays on origin, unchanged after the freeze note, and keeps its own v1 tooling on the
same branch: `python -I tools/event_append.py --verify events/2026-10-22.jsonl` keeps working forever.
The registry lists it under `frozen`. `project --all-stores` never reads it, because every event in
it now lives in a new store with a pointer back.

---

## 5. Implementation lanes (file-disjoint; write the test first and watch it fail)

Wave A runs in parallel. Each lane is one repo and one set of files.

| Lane | Repo / files | Test written first (must fail on the current tree) |
|---|---|---|
| **L1 store core** | aesop `tools/event_store/{__init__,store,registry,cli}.py`, `tests/test_event_store_core.py` | v2 append chains across two day files (second file's first `prev` = first file's last sha). Deleting the first day file makes `verify` fail. A v1 fixture verifies under v1 rules. `append` with no `--project` and no goal exits 2. Goal G2 resolves to keyrun. An append to a store whose `store.json` project differs is refused. A ref key `keyrun_pr_606` is refused. |
| **L2 init + guard** | aesop `tools/event_store/init.py`, `tools/event_store/guard/events_append_only.sh`, `tests/test_event_store_init.py` | `init` in a temp repo creates an orphan branch with no parent commit, a worktree, `store.json`, `.gitattributes`, and an installed guard. The guard rejects an edited line and accepts an appended one (port of conductor3 `TestAppendOnlyGuard`). |
| **L3 migrate** | aesop `tools/event_store/migrate.py`, `tests/test_event_store_migrate.py`, fixture `tests/fixtures/event_store/source-v1.jsonl` | Fixture has one row per rule (goal, repo, refkey, text1, multi, default). The manifest assigns each one as in 4.2. Counts sum to the source. `src_sha` matches. A migrate run against a source with no FROZEN note exits 1. Re-running migrate into a non-empty store exits 1. |
| **L4 aesop pre-push for store branches** | aesop `hooks/pre-push-policy.sh` (one narrow branch), `tests/` shell test | Only needed if any store branch is ever pushed through an aesop clone. With 2.2 the branches live in conductor3, so this lane is **conditional on decision 1** and is dropped if that decision stands. |
| **L6 PreCompact multi-store** | claude-config worktree (never `~/.claude` in place): `hooks/checkpoint-before-compact.mjs`, `hooks/lib/goals.mjs`, `hooks/checkpoint-before-compact.test.mjs` | Fixture registry with two stub stores. Rulings from both appear grouped by goal with `[project=]`. A ruling with no goal lands under `(no goal, project=X)`. One missing store prints `store X unreadable`, and the other store still renders. A CHAIN BROKEN in one store is shown verbatim with its project. `EVENT_STORE_ROOT` set still means one store only. |
| **L7 freeze the v1 writer** | conductor3 `events/log`: `tools/event_append.py`, `tests/test_event_store.py` | After a `note` with `refs.frozen=1`, `append_event` raises and writes nothing. `--verify` still prints `OK`. The pre-freeze tests stay green. |
| **K1 bot reasons** | keyrun `game/bots/Keyrun.Bots/Bot.cs` (+ Roles), `game/bots` tests | Every command in a `BotBatch` has exactly one reason with a stable rule id. **K1-a:** a seed-1 key-2 autorun has the same `Fingerprint` with and without a receipt sink. A role that emits a command with no rule id fails the test. |

Wave B starts after wave A merges, because these lanes use the L1 API:

| Lane | Repo / files | Test first |
|---|---|---|
| **L5 projections** | aesop `tools/event_store/project.py`, `tests/test_event_store_project.py` | `rulings --all-stores` over two fixture stores, newest last, with project and goal suffixes. `pending` closes across stores only through `xref`. `checkpoints --last 1 --per-store`. |
| **K2 receipt sink + modes** | keyrun `game/src/HostCore/BotCommandSource.cs`, new `game/src/HostCore/BotReceiptSink.cs`, host `RunLog.cs`, tests | A 200-tick autorun writes a run file whose chain verifies and whose line count equals the bot command count plus holds. A seat flagged blind never emits a `guide:` rule. |
| **K3 verify-run** | aesop `tools/event_store/runs.py`, `tests/test_event_store_runs.py` | All four checks in 3.3, each with a mutant fixture: a tampered line, a wrong head, a guided seat with a `policy:` rule, an end with no file. |

After both waves, the orchestrator runs the cutover in 4.1. It runs once, is not a lane, and needs
the dry-run counts reviewed first.

---

## 6. The five calls Matt might overrule

1. **Location: private conductor3, one orphan branch per project, not a branch in each project's own
   repo.**
   - Why: aesop and keyrun are public, and the log holds Matt's words and internal paths.
   - Cost: a project's history does not travel with its repo.
   - The overrule: store branches in each repo, pushed only to a private remote.
2. **Fleet checkpoints that name several projects go whole to aesop (74 events now), with
   `refs.mentions`.** They are not split by paragraph and not copied into each store.
   - Why: splitting rewrites facts, and copying breaks "each source seq appears once".
   - Cost: the aesop store carries the historic cross-project noise. From the cutover on, the
     orchestrator writes one checkpoint per project.
3. **A git-committed JSONL hash chain packaged in aesop, not aesop's SQLite `state_store`.**
   - Why: the PreCompact hook and the append-only guard already work on JSONL in git. JSONL diffs and
     pushes as text. The SQLite WAL backup incident (memory: sqlite-wal-backup-destroys-data) is a
     known risk.
   - Cost: two event stores in aesop until someone decides to merge them.
4. **Schema v2 with a store-wide seq and a chain across day files.**
   - Why: v1 restarts the chain every day, so deleting a whole day verifies clean.
   - Cost: the new stores and the frozen one use two verify modes.
5. **Bot receipts: one line per issued command plus deliberate holds, not per tick. Committed in
   keyrun's store as `runs/<run_id>.jsonl` and anchored by `playtest_start` and `playtest_end`.**
   - Estimate: hv-1911c618 had 1112 casts across all seats, so a full run file is probably on the
     order of a megabyte. K2 must measure the real size.
   - The overrule: anchor-only. The file then stays beside the capture, and only its hash and line
     count are committed. A gate fails closed if the file is missing.

Also decided without asking, because each is reversible and within the brief:
- The registry lives at `~/.claude/event-stores.json`.
- Brain events need an explicit `--project brain`.
- G0 goes to aesop.
- Readers run the tool from a pinned aesop worktree at a release tag.
