# Machinery map
Every gate, daemon, store, lane contract, monitor and UI surface in this repository, mapped to the tree.

**Basis.** origin/main at `cb972c56`. Each row was checked against that tree: the file exists (`ls`), and each named function or flag appears in that file (`grep`). Line numbers are for that commit and drift on edit. Rows the tree did not confirm are not here (section 8.3).

**Scope limits.** Brain-side hooks (`~/.claude/hooks`), the agent catalog, and the fleet scripts in `~/scripts` and the external conductor checkout live outside this repo. They appear only as names (section 6). Runtime state (live tasks, heartbeat ages, queue contents) is not described.

**Columns.** "Path" is repo-relative, with `file:line` where a function is named. "Enforced-by" names the gate or code that makes a property hold. "Consumed-by" names the reader.

**Domain detail** lives in each domain's CLAUDE.md: [tools](../tools/CLAUDE.md), [hooks](../hooks/CLAUDE.md), [daemons](../daemons/CLAUDE.md), [state_store](../state_store/CLAUDE.md), [monitor](../monitor/CLAUDE.md), [mcp](../mcp/CLAUDE.md), [ui](../ui/CLAUDE.md), [dash](../dash/CLAUDE.md), [driver](../driver/CLAUDE.md), [tests](../tests/CLAUDE.md), [skills](../skills/CLAUDE.md).

## 1. Gates
### 1.1 Pre-push policy (`hooks/pre-push-policy.sh`)
The push-time check order runs from line 3333 to 3470. Base-pinned checks resolve their tool from the base gate copy.

| Component | Path | Purpose | Enforced-by / consumed-by |
|---|---|---|---|
| Base gate copy | hooks/pre-push-policy.sh:187 `gate_base_dir` | Archives origin/main `tools/` and `state_store/` to a temp dir so a push cannot judge itself. | Base-pinned checks; `gate_base_block` (:251) fails closed. |
| Tool status helpers | hooks/pre-push-policy.sh:111 `gate_tool_status`, :141 `gate_tool_missing_block`, :280 `gate_no_python_block` | Turn a missing gate tool or interpreter into a block. | Every check below. |
| Gate tamper banner | hooks/pre-push-policy.sh:258 `check_gate_tamper` | Prints GATE-CHANGED when a push edits gate files; non-blocking. | Invoked :3353. |
| Branch policy | hooks/pre-push-policy.sh:492 `check_branch_policy` | Branch rules; tag-only pushes are allowed from any branch (:531-538). | Invoked :3333. |
| Secret scan | hooks/pre-push-policy.sh:683 `check_secret_scan` | Runs `tools/secret_scan.py` over the pushed range. | Invoked :3355; receipt part `secret-scan`. |
| Import resolution | hooks/pre-push-policy.sh:819 `check_import_resolution` | Runs `tools/import_resolution_check.py`. | Invoked :3364; receipt part `import-resolution-check`. |
| Conflict markers | hooks/pre-push-policy.sh:906 `check_conflict_markers` | Runs `tools/conflict_marker_check.py` over the pushed range. | Invoked :3371. |
| Symbol survival | hooks/pre-push-policy.sh:989 `check_symbol_survival` | Runs `tools/pr_symbol_survival_check.py` (G13) from the base copy; exit 1 or 2 blocks. | Invoked :3378; CI job `pr-symbol-survival`. |
| Tracker guard | hooks/pre-push-policy.sh:767 `check_tracker_guard` | Runs `tools/tracker_guard.py` check mode (zombie resurrection). | Invoked :3384. |
| CLAUDE.md sync | hooks/pre-push-policy.sh:1067 `check_claudemd_sync` | Runs `tools/claudemd_sync_gate.py --check`. | Invoked :3390; receipt part `claudemd-sync-gate`. |
| Tool index | hooks/pre-push-policy.sh:1112 `check_gen_tool_index` | Runs `tools/gen_tool_index.py --check` (byte identity of tools/INDEX.md). | Invoked :3396; receipt part `gen-tool-index`. |
| Metrics | hooks/pre-push-policy.sh:1157 `check_metrics` | Runs `tools/metrics_gate.py`. | Invoked :3402. |
| Test suite count | hooks/pre-push-policy.sh:1202 `check_test_suite_count` | Runs `tools/verify_test_suite_count.py --check` (shard coverage). | Invoked :3414; receipt part `verify-test-suite-count`. |
| CLAUDE.md headroom | hooks/pre-push-policy.sh:1395 `check_claudemd_headroom` | Runs `tools/claudemd_lint.py --headroom`. | Invoked :3408. |
| Pyflakes ratchet | hooks/pre-push-policy.sh:1256 `check_pyflakes_ratchet` | Runs `tools/pyflakes_gate.py` against `.pyflakes-baseline.json`. Skips with a warning when the tool is absent (:1290). | Invoked :3420; CI `ci.yml:318-320`. |
| Subprocess ratchet | hooks/pre-push-policy.sh:1327 `check_subprocess_guard_ratchet` | Runs `tools/subprocess_guard.py --check` against `.subprocess-guard-baseline.json`. | Invoked :3427. |
| Encoding lint | hooks/pre-push-policy.sh:1528 `check_encoding_lint` | Runs `tools/encoding_lint.py --check`. | Invoked :3433; receipt part `encoding-lint`. |
| Test coverage | hooks/pre-push-policy.sh:1576 `check_test_coverage` | Runs `tools/verify_test_coverage.py --check` (orphaned tests). | Invoked :3439. |
| Generated paths | hooks/pre-push-policy.sh:1689 `check_generated_paths` | Runs the `tools/generated_paths.py` check for pushes that touch registered generated paths. | Invoked :3445. |
| Generated regen | hooks/pre-push-policy.sh:1789 `check_generated_regen` | Runs `tools/generated_push_gate.py`; a committed generated artifact must match its regenerator. | Invoked :3455. |
| Linux shape | hooks/pre-push-policy.sh:1621 `check_linux_shape` | Runs `tools/linux_shape_check.py --require-wsl` on shell, workflow and Node-test changes. | Invoked :3461; receipt part `linux-shape`. |
| Receipt emit | hooks/pre-push-policy.sh:1858 `check_emit_receipt` | Runs `tools/emit_receipt.py` after all gates pass; a red matrix blocks. | Invoked :3470; see 1.3. |

### 1.2 Receipt matrix parts (`tools/emit_receipt.py` `DEFAULT_REGISTRY`, lines 57-71)
`run_matrix` (tools/emit_receipt.py:343) runs each part. The pre-push check in the last column runs the same tool.

| Matrix part | Command | Pre-push check that runs the same tool | Notes |
|---|---|---|---|
| py-shard-0 .. py-shard-3 | `tools/ci_shard_runner.py n 4` | none in pre-push-policy.sh | Matrix only. |
| secret-scan | `tools/secret_scan.py .` | check_secret_scan :683 | |
| claudemd-sync-gate | `tools/claudemd_sync_gate.py --check` | check_claudemd_sync :1067 | |
| gen-tool-index | `tools/gen_tool_index.py --check` | check_gen_tool_index :1112 | |
| verify-test-suite-count | `tools/verify_test_suite_count.py --check` | check_test_suite_count :1202 | |
| encoding-lint | `tools/encoding_lint.py --check` | check_encoding_lint :1528 | |
| import-resolution-check | `tools/import_resolution_check.py` | check_import_resolution :819 | |
| sibling-import-check | `tools/sibling_import_check.py --check` | none in pre-push-policy.sh | Matrix only. |
| linux-shape | `tools/linux_shape_check.py --require-wsl` | check_linux_shape :1621 | Requires WSL. |
| browser-proofs | none on this box | none | Recorded as skipped with a reason. |
| windows-shard | none on this box | none | Recorded as skipped with a reason. |

Pre-push checks with no matrix part: conflict markers (:906), symbol survival (:989), tracker guard (:767), metrics (:1157), CLAUDE.md headroom (:1395), pyflakes ratchet (:1256), subprocess ratchet (:1327), test coverage (:1576), generated paths (:1689), generated regen (:1789), receipt emit (:1858).

### 1.3 Receipts: emit, sign, post, verify, flush
| Component | Path | Purpose | Enforced-by / consumed-by |
|---|---|---|---|
| Matrix and build | tools/emit_receipt.py `run_matrix` :343, `build_receipt` :424, `conclusion_for` :447, `exit_code_for` :451, `post_receipt` :477, `main` :560 | Runs the matrix, builds the envelope with `tree_hash`, signs it, posts it on the head sha. Exit 3 on a red matrix with `--fail-on-red`. | Pre-push :1858. |
| Shared primitives | tools/receipt_common.py `CHECK_NAME` :24 (`verify-receipt-local`), `KEY_ENV` :28 (`AESOP_RECEIPT_KEY`), `sign` :84, `verify_signature` :109, `resolve_spool_dir` :188 | Canonical JSON, ed25519 or HMAC-SHA256 signing, spool directory. | Emit and verify. |
| Offline verifier | tools/verify_receipt.py `verify` :134 (tree hash recomputed and compared at :164), `fetch_receipt_for_head` :245, `main` :357 | Recomputes `<head>^{tree}`, checks signature, freshness and required parts. Exit 0 valid, 1 invalid, 2 unevaluable. | `.github/workflows/verify-receipt.yml` (job `verify-receipt`, line 51). |
| Flush and poller | tools/receipt_flush.py `flush_spooled_receipt` :56; tools/receipt_autoflush.py `poll_and_flush` :67, `spawn_detached` :107 | Posts spooled receipts once the sha exists on GitHub. | Flush: `run_receipt_flush` (daemons/run-watchdog.sh:150) every cycle. Poller: spawned from pre-push (:2074). |

`verify-receipt` is not a required check. It is absent from `tools/required-checks.json` and from the live branch protection list (1.7).

### 1.4 Generated artifacts
| Component | Path | Purpose | Enforced-by / consumed-by |
|---|---|---|---|
| Registry | tools/generated_paths.py `REGISTRY` :139, `GENERATED_PATHS` :210, `is_generated` :258, `regen_matches_head` :365 | Single list of repo-generated files and their regenerators; checks the committed artifact against HEAD. | Pre-push :1689 and :1789; `tools/auto_merge.py` `fix_branch` (conflicts resolved only over registered paths); `tools/merge_queue.py` docstring (`worktree_is_safe` restores only registered paths). |
| Push gate | tools/generated_push_gate.py | Gate module for a push that touches a generated path. | Pre-push `check_generated_regen` :1789 (:1807). |
| Index generator | tools/gen_tool_index.py | Writes tools/INDEX.md. | CI `ci.yml:244-246`; pre-push :1112. |
| Regen on main | .github/workflows/regen-on-main.yml (job `regen-on-main`, line 35) | On push to main runs `tools/regen_all.py --fix` (line 72) and opens a self-heal PR. | Workflow trigger: push to main. |

### 1.5 Documentation and PR-shape gates
| Component | Path | Purpose | Enforced-by / consumed-by |
|---|---|---|---|
| CLAUDE.md lint | tools/claudemd_lint.py | Checks domain docs for scope and size; `--headroom` mode. | CI `ci.yml:236-238`; pre-push :1395. |
| CLAUDE.md sync | tools/claudemd_sync_gate.py `check_domain_claudemd_sync` :184, `main` :224 | A PR that changes domain code must also change a domain doc or INDEX.md. | CI `ci.yml:240-242`; pre-push :1067. |
| PR footprint | tools/pr_footprint_gate.py; CI job `pr-footprint-gate` (ci.yml:670) | Limits PR size; runs from the base tree (`ci.yml:696-699`). | Required check. |
| PR symbol survival | tools/pr_symbol_survival_check.py; CI job `pr-symbol-survival` (ci.yml:708) | G13: a symbol added by the branch must survive merges. Runs from base (`ci.yml:736`). | Required check; pre-push :989. |
| Incident report | tools/incident_report.py `--check --repo .`; CI job `incident-report-gate` (ci.yml:759) | Fails closed on an unknown incident class; docs/INCIDENTS.md must equal regenerated output. | `ci.yml:773-774`. Not required. |

### 1.6 Tests, isolation and ratchets
| Component | Path | Purpose | Enforced-by / consumed-by |
|---|---|---|---|
| Shard runner | tools/ci_shard_runner.py | Runs one of four Python test shards. | CI matrix `ci.yml:161` (`python-shard: [0,1,2,3]`); `ci.yml:343`. |
| Remote refs tripwire | tools/remote_refs_tripwire.py | Wraps Python shards; fails when a remote branch or PR attributable to the run appears. | CI `ci.yml:343`, `ci.yml:488`. |
| Isolation tripwire | tools/test_isolation_tripwire.py | Runs Node tests under an isolated environment. | CI `ci.yml:481` (shard 0). LANE-CONTRACT section 2. |
| Gates-wired check | tools/verify_gates_wired.py | Checks that each documented gate is referenced by a workflow. | CI `ci.yml:292`. |
| New-gate checklist | tools/new_gate_check.py | Runs the new-gate checklist, including gate inventory parity (tools/gate_inventory.py). | LANE-CONTRACT section 5. |
| Encoding ratchet | tools/encoding_lint.py `ascii_stale_entries` :351, `ascii_findings` :361; baseline tools/encoding-ascii-baseline.json | Shrink-only ratchet on non-ASCII in tools/*.py. | Pre-push :1528 and receipt part. CI does not run it directly (grep of ci.yml). |
| State API lint | tools/stateapi_lint.py; baseline .stateapi-baseline.json | Ratchet on direct state-file access. | CI `ci.yml:294-296`. |

### 1.7 CI workflows and branch protection
| Workflow | Triggers | Jobs (key line) | Notes |
|---|---|---|---|
| ci.yml | pull_request to main; workflow_dispatch; schedule `30 3 * * *` | docs-only-gate :52; windows-sensitive-paths :98; ci :144 (matrix shards 0-3); windows-shard :419; windows :490; ps1-syntax-check :538; browser-proofs :552; state-drift-gate :646; pr-footprint-gate :670; pr-symbol-survival :708; incident-report-gate :759 | Most jobs check out the PR head (`ci.yml:170`). The secret scan runs from a base copy (`ci.yml:406-417`). |
| main-full.yml | push to main; workflow_dispatch; schedule `30 3 * * *` | main-full-verify :32; main-full-browser-proofs :120 | Nightly full matrix. |
| verify-receipt.yml | pull_request to main; workflow_dispatch | verify-receipt :51 | Non-required. |
| regen-on-main.yml | push to main; workflow_dispatch | regen-on-main :35 | Runs `tools/regen_all.py --fix`. |
| examples.yml | pull_request and push to main, paths `examples/**` and `tools/wave_templates.py` | examples-verify :25 | |
| pages.yml | push to main; workflow_dispatch | build :17; deploy :36 | |
| publish.yml | release published | publish :11 | |
| reproduce.yml | workflow_dispatch; schedule `0 2 * * 0` | reproduce :17 | |
| stats-refresh.yml | schedule `0 2 * * *`; workflow_dispatch | refresh-stats :30 | |
| crossos-drift.yml | schedule `0 9 * * 1`; workflow_dispatch | crossos-drift :32 | |

**Branch protection.** `tools/required-checks.json` declares the required contexts: `ci (0)` through `ci (3)`, `windows`, `pr-footprint-gate`, `pr-symbol-survival`. `tools/required_checks_gate.py` fails on drift between this file and the enforced set. A live read of `gh api repos/matt82198/aesop/branches/main/protection/required_status_checks`, taken 2026-10-15, returned the same seven contexts (`strict: false`). `verify-receipt` and `incident-report-gate` are not required.

## 2. Daemons, installer and scheduled tasks
### 2.1 Daemons
| Component | Path | Purpose | Enforced-by / consumed-by |
|---|---|---|---|
| Watchdog entry | daemons/run-watchdog.sh `main` :306 | Runs one cycle with `--once`, or loops with `sleep 150` (:387, :416). Each cycle runs the backup cycle, receipt flush, PR sweep and task ingest. | Scheduled task `AesopWatchdogDaemon` (`--once`), or manual daemon mode. |
| Watchdog lock | daemons/run-watchdog.sh `acquire_lock` :16, `release_lock` :106 | Single instance through a mkdir lock at `state/.watchdog-lock`. | Self. |
| Monitor staleness signal | daemons/run-watchdog.sh `check_monitor_staleness` :118 | Logs a SIGNAL when `$CONDUCTOR_ROOT/monitor/.monitor-heartbeat` (read :442) is missing, empty or stale. | FLEET-BACKUP.log. |
| Halt check | daemons/run-watchdog.sh `check_halt` :236 | Calls `tools/halt.py --status`; an unknown result logs HALT-CHECK-FAILED (:283) and skips the cycle. | Fail-closed. |
| PR sweep | daemons/run-watchdog.sh `run_pr_sweep_throttled` :172 | Runs `tools/pr_sweep.py` at most once per 15 min (stamp `state/.pr-sweep-last`). | `tools/pr_sweep.py` `arm_auto_merge` :203. |
| Task-event ingest | daemons/run-watchdog.sh `run_task_ingest` :200 | Runs `tools/ingest_task_events.py` each cycle; a non-zero exit is logged and `--once` exits 1. | Event store task stream (section 3). |
| Receipt flush | daemons/run-watchdog.sh `run_receipt_flush` :150 | Runs `tools/receipt_flush.py` each cycle. | Section 1.3. |
| Backup cycle | daemons/backup-fleet.sh `main` :339, `scan_unpushed_commits` :120 | Discovers repos, pushes unpushed commits, runs `secret_scan.py --range` over them. Spawned by the watchdog each cycle (override `AESOP_WATCHDOG_CYCLE_CMD`, :325-330). | FLEET-BACKUP.log. |
| Watchdog heartbeat writer | daemons/backup-fleet.sh `write_heartbeat` :53 | Writes `$CONDUCTOR_ROOT/state/.watchdog-heartbeat`. daemons/CLAUDE.md documents the end-of-cycle rule. | Readers in 2.3. |
| Blocked marker | daemons/backup-fleet.sh `write_blocked_marker` :59, `clear_blocked_marker` :65 | Writes or clears `state/.watchdog-blocked` when repos are BLOCKED. | No reader verified in this map. |
| Self-heal supervisor | daemons/selfheal.sh `main` :200, `cycle_once` :177, `is_heartbeat_stale` :100, `restart_watchdog` :123, `restart_monitor` :152 | Reads the watchdog heartbeat (:242) and monitor heartbeat (:245); restarts stale daemons; logs state/SELFHEAL.log. | No task in daemons/install-tasks.ps1 invokes it. |
| Self-heal lock | daemons/selfheal.sh `acquire_lock` :24, `release_lock` :91 | mkdir lock at `state/.selfheal-lock`. | Self. |
| Merge-queue runner | daemons/run-merge-queue.sh `main` :116, `check_halt` :57 | One bounded pass of `tools/merge_queue.py --advance` per run; no loop and no sleep (file header). | Scheduled task `AesopMergeQueue` (opt-in). |

### 2.2 Installer and task names (`daemons/install-tasks.ps1`)
| Component | Path | Purpose | Enforced-by / consumed-by |
|---|---|---|---|
| Task prefix | install-tasks.ps1 param `TaskPrefix` (line 9, default `Aesop`) | Task names are `${TaskPrefix}WatchdogDaemon`, `${TaskPrefix}RefinementMonitor`, `${TaskPrefix}MergeQueue`. | Windows Task Scheduler. |
| Register and unregister | install-tasks.ps1 `Register-DaemonTask` :118, `Unregister-DaemonTask` :210 | Registers a task at `-IntervalMinutes`; an existing task with a different action is skipped with a warning unless `-Force`. | tests/test_install_tasks.py. |
| Audit log | install-tasks.ps1 `Append-AuditLog` (called :149, :199) | Writes `state/install-tasks-audit.log` with action and outcome per task. | Operator. |
| Watchdog task | install-tasks.ps1:328 (command), :333 (task name) | `bash <root>/daemons/run-watchdog.sh --once >> state/cron-watchdog.log 2>&1`, every 5 min by default. | Registered by default. |
| Monitor task | install-tasks.ps1 `-MonitorCommand` (20 min default) | Registers `${TaskPrefix}RefinementMonitor` only when a command is given. | Opt-in. |
| Merge-queue task | install-tasks.ps1 `-EnableMergeQueue` or `-MergeQueueCommand` (5 min default) | Registers `${TaskPrefix}MergeQueue`. | Opt-in. |
| Launcher | daemons/run-hidden.vbs | Runs a bash command with window style 0 and returns the child exit code. | Every task above. |

Registration scope: the default registers the watchdog only; `-EnableMergeQueue` or `-MonitorCommand` register only those; `-All` registers all three; `-Uninstall` removes all three.

### 2.3 Heartbeats and liveness files
| File | Written by | Read by |
|---|---|---|
| `$CONDUCTOR_ROOT/state/.watchdog-heartbeat` | daemons/backup-fleet.sh:28, :53 | daemons/selfheal.sh:242; dash/watchdog-gui.sh:21; mcp/server.mjs:101, :195; monitor/collect-signals.mjs:192; state_store/read_api.py:15 (docstring example); tools/health_checks.py:89; tools/alert_bridge.py:187; ui/collectors.py `get_heartbeat_status` :145. |
| `monitor/.monitor-heartbeat` (under AESOP_ROOT) | monitor/collect-signals.mjs:1319 (`MON` = AESOP_ROOT/monitor, :76) | daemons/run-watchdog.sh:442 (under CONDUCTOR_ROOT); daemons/selfheal.sh:245; mcp/server.mjs:102, :219; monitor/collect-signals.mjs:110, :191; ui/collectors.py `get_monitor_heartbeat_status` :194; tools/health_checks.py:103. |
| `state/.task-ingest-heartbeat` | tools/ingest_task_events.py `HEARTBEAT_FILENAME` :82, `_write_heartbeat` :378 | tools/ingest_task_events.py `status` :236; tools/boot_state.py:189 (via `ite.status`). |
| `state/.pr-sweep-last` | daemons/run-watchdog.sh:176 | daemons/run-watchdog.sh `run_pr_sweep_throttled` :172. |
| `state/.HALT` (box-wide and per-checkout) | tools/halt.py `_write_sentinel` :132, `halt` :153, `halt_box_wide` :160 | daemons/run-watchdog.sh `check_halt` :236; daemons/run-merge-queue.sh `check_halt` :57; tools/merge_queue.py `_halt_status` :321; tools/merge_train.py `halt_status` :109; tools/auto_merge.py `halt_reason` :50 (called :75); driver/wave_loop.py:1615 (state_dir None branch). |

**Path note.** The monitor writes under `AESOP_ROOT/monitor` (collect-signals.mjs:76). The watchdog reads under `CONDUCTOR_ROOT/monitor` (run-watchdog.sh:442). These are the same file only when the two roots resolve to the same tree.

## 3. Event store and projections
### 3.1 Storage and write entry points
| Component | Path | Purpose | Enforced-by / consumed-by |
|---|---|---|---|
| Event store | state_store/store.py `EventStore` (class), `append` :204, `read` :280, `read_since` :284, `read_all` :305, `save_snapshot` :331, `read_snapshot` :362 | Append-only SQLite event log in WAL mode; version check and append inside `BEGIN IMMEDIATE`. Database file `tracker_events.db` (state_store/paths.py:37). | Every writer. No delete method in state_store/*.py. |
| Read facade | state_store/api.py `StateAPI` :20, `append` :47, `get` :66, `get_since` :70, `project` :78 | Read and append facade; `project` raises ValueError for an unknown view (:83). | ui/collectors.py; tools/state_rebuild.py; tools/boot_state.py. |
| Write facade | state_store/write_api.py `WriteAPI` :76 | Single write entry point for tracker, orchestrator status, STATE.md and BUILDLOG. `_file_lock` :909 and the atomic renders (`_write_markdown_atomic` :1079, `_render_tracker_atomic` :1250, `_render_orchestrator_status_atomic` :1378) back it. | Callers in tools/ and driver/. |
| WriteAPI tracker entries | state_store/write_api.py `tracker_update_status` :138, `tracker_append_item` :219, `tracker_update_item` :324, `tracker_archive_item` :389, `tracker_adopt_disk_items` :454 | Tracker mutations, each appended as a `tracker` event. | tools/tracker_guard.py (imports WriteAPI :70; `enforce` reverts through it). |
| WriteAPI orchestrator entries | state_store/write_api.py `set_orchestrator_status` :677, `clear_orchestrator_status` :740 | `orchestrator_status` events. | tools/orchestrator_status.py:36. |
| WriteAPI document entries | state_store/write_api.py `write_state_md` :776, `append_buildlog` :813, `rebuild_state_md` :871, `rebuild_projection` :658 | State markdown and BUILDLOG appends; file write precedes the event (fail-closed). | tools/ensure_state.py:68 (`write_state_md`); tools/state_rebuild.py state-md view. |

### 3.2 Streams and event types
| Stream | Event types (path:line of append or fold) | Writer |
|---|---|---|
| tracker | `item_created` (state_store/write_api.py:307; fold projections.py:60), `item_updated` (write_api.py:202, :373; fold projections.py:91), `item_archived` (write_api.py:434; fold projections.py:99) | WriteAPI; `ingest.py:44` backfill. |
| orchestrator_status | `phase_changed` (write_api.py:715; fold projections.py:257), `activity_changed` (write_api.py:727; fold projections.py:265), `status_cleared` (write_api.py:760; fold projections.py:272) | WriteAPI. |
| state_markdown | `state_md_written` (write_api.py:809), `state_md_rebuilt` (write_api.py:899) | WriteAPI. |
| buildlog | `buildlog_entry` (write_api.py:848) | WriteAPI. |
| claims | `claim_requested`, `claim_released` (state_store/coordination.py:97, :188, :208; state_store/fs_claim_log.py:76-77; state_store/store.py:224) | coordination.py; fs_claim_log.py. |
| instances | `instance_registered` (state_store/instance_projection.py:49), `instance_heartbeat` (:79), `file_claim_requested` (:113), `file_claim_released` (:145) | instance_projection.py. |
| task | `task_event` (tools/ingest_task_events.py:79 constant; `api.append` :355) | `ingest` :288; stream `task`, actor `task_ingest`. |
| orchestrator_goal | `goal_set` (tools/goal_state.py:41 `GOAL_EVENT`; stream constant :40) | tools/goal_state.py. |

### 3.3 Source versus projection
The event streams are the source. The views below are derived from them.

| Component | Path | Kind | Readers |
|---|---|---|---|
| tracker fold | state_store/projections.py `project_tracker` :111, `project_tracker_with_snapshot` :121, `save_snapshot` :154 | Projection, registered as `tracker` (api.py:15-18). | tools/state_rebuild.py:188, :232; state_store/export.py:26. |
| orchestrator_status fold | state_store/projections.py `project_orchestrator_status` :229 | Projection, registered as `orchestrator_status` (api.py:15-18). | tools/state_rebuild.py:205, :275; state_store/read_api.py (projection-first reads, :56). |
| agent lifecycle fold | state_store/projections.py `project_agent_lifecycle` :169 | Exported from state_store/__init__.py:18; not registered; no caller found. | none. |
| goal fold | tools/goal_state.py `project_goal` :106 | Folds `orchestrator_goal` outside the store's projector table. | tools/boot_state.py `read_store` :119. |
| task stream | no projection | Raw events. | tools/boot_state.py `read_store` :119 (`api.get(TASK_STREAM)`). |
| instances | state_store/instance_projection.py `list_active_instances`, `get_all_claimed_files`, `detect_stale_instances` :276 | Read functions, not registered projections. | mcp/instances-claims.py; tools/instance_manager.py; tools/multi_dispatch.py; driver/wave_loop.py. |
| ledger view | not registered | Requested but missing (section 8.1). | tools/state_rebuild.py:217, :294; state_store/materialize.py:131 (docstring). |

### 3.4 Maintenance, ingest and boot brief
| Component | Path | Purpose | Enforced-by / consumed-by |
|---|---|---|---|
| Tracker guard | tools/tracker_guard.py `cmd_seed` :197, `cmd_check` :244 (default), `cmd_enforce` :311, `cmd_reconcile_journal` :393, `cmd_adopt_disk_items` :476, `main` :521 | Zombie-resurrection guard; journal in `state/tracker-journal.jsonl`. `check` exits 1 on a zombie; `enforce` reverts it through WriteAPI. | Pre-push `check_tracker_guard` :767. |
| State rebuild | tools/state_rebuild.py `main` :42, `_rebuild_all` :100, `_check_drift` :152 | Rebuilds views (`--all`, `--view tracker\|state-md\|orch-status\|ledger`) and checks drift (`--check`). | CI `state-drift-gate` (`ci.yml:657-663`). |
| Task-event ingest | tools/ingest_task_events.py `ingest` :288, `check` :214, `status` :236, `main` :382; cursor `CURSOR_FILENAME` :81; quarantine `QUARANTINE_SUFFIX` :124 | Tail-ingests the brain task-events JSONL into the `task` stream. Cursor remembers the offset; malformed lines go to `<source>.quarantine.jsonl`. Source default `~/.claude/state/task-events.jsonl` (:83-84, brain-side path). | run-watchdog `run_task_ingest` :200. |
| Boot brief | tools/boot_state.py `read_store` :119, `fit_to_cap` :320, `main` :384 | One byte-capped boot brief from the goal, task stream, tracker projection, STATE.md and BUILDLOG. | CLI. In-repo references: tests/test_boot_state.py and docs. |

### 3.5 Leases, claims and fencing
| Component | Path | Purpose | Enforced-by / consumed-by |
|---|---|---|---|
| Lease store | state_store/lease_claims.py `LeaseConflict` :127, `LeaseStore` :137, `claim` :221, `renew` :339, `release` :413 | SQLite lease table; claims use `BEGIN IMMEDIATE` (:263). | Through state_store/claim_backend.py `LocalLeaseBackend` (:100). |
| Lease renew caller | state_store/claim_backend.py `LocalLeaseBackend.renew` :135-137 | Wraps `LeaseStore.renew`. | No non-test code calls `LocalLeaseBackend.renew`. |
| Coordination claims | state_store/coordination.py `try_claim` :158 (default TTL 300 s), `release` :238, `current_holder` :259 | Resource claims as events. | driver/wave_loop.py (`coordination.try_claim` :778, `coordination.release` :652). |
| Filesystem claim log | state_store/fs_claim_log.py `FsClaimLog` :366, `fold_fs_claims` :149 | Shared-directory claim backend for multibox. | tools/multibox_config.py `build_backend` :380, only when `multibox.enabled`. |
| Multi-dispatch TTL | tools/multi_dispatch.py `claim_ttl_seconds` :65, called :185 | Derives the claim TTL from `--timeout` and the configured floor. | tools/multi_dispatch.py. |
| Failover and fencing | state_store/failover.py `FencedWriteError` :102, `FencingToken` :299, `assert_fenced` :320, `fenced_write` :341, `elect_primary` :485 | Primary election and generation fencing. | No non-test caller (section 8.1). |

### 3.6 Single-writer rule
| Component | Path | Purpose | Enforced-by / consumed-by |
|---|---|---|---|
| Writer discipline | state_store/write_api.py `WriteAPI` :76 | All tracker, orchestrator and buildlog writes go through this facade. | `tools/stateapi_lint.py` ratchet (1.6). |
| STATE.md guard | tools/reconcile.py:229 | States that STATE.md is single-writer and reconcile never writes it. | Reason string; no separate check. |
| Scheduler assumption | driver/wave_scheduler.py:29 | "SINGLE-WRITER ASSUMPTION: this pilot assumes tracker.json is NOT edited concurrently". | Assumption only, no check. |

## 4. Dispatch and lanes
### 4.1 Lane contract and its variants
| Component | Path | Purpose | Enforced-by / consumed-by |
|---|---|---|---|
| Lane contract | LANE-CONTRACT.md: section 1 "Evidence, not narration" :14; 2 "Tests" :24; 3 "Never fit green" :62; 4 "This repo's environment" :105; 4a "Before you push" :131; 4b "Native first" :144; 5 "Verification" :168; 6 "Boundaries" :201; 7 "Definition of DONE" :251; 8 "Report format" :279; 9 "Linux shape" :283 | Standing rules every lane runs under. | Read by lanes by instruction. Gates in section 1 back several rules. |
| Lean contract | LANE-CONTRACT-MIN.md | Arm B of a lane-contract A/B: walls only. Relaunches with the full contract on a fault. | Named by tools/lane_ab_report.py (docstring, line 7); no gate reads it. |
| Lanes never merge | LANE-CONTRACT.md section 6 (:202) | Lanes push and open PRs; only the orchestrator merges, through `tools/auto_merge.py`. | Prose, plus dispatch-lint merge patterns (4.2). The enforcing hook is outside the repo (section 6). |

### 4.2 Dispatch lint and wave definitions
| Component | Path | Purpose | Enforced-by / consumed-by |
|---|---|---|---|
| Forbidden patterns | tools/dispatch_lint.py `FORBIDDEN_PATTERNS` :57, `find_violations` :253, `scan_directory` :297, `main` :374 | Blocks forbidden dispatch and code patterns (`gh pr merge` without `--auto`, `--admin`, `--no-verify`, `--force`). | `hooks/pre-commit-dispatch-lint.sh`; tests/test_dispatch_lint.py. |
| Wave presets | templates/wave-presets/saas.json, data.json, library.json; tools/wave_templates.py `load_preset` :82, `PRESETS_DIR` :39 | Wave-manifest presets with template variables. | CI `examples.yml` (path filter includes tools/wave_templates.py); tools/wave_manifest_lint.py. |
| Wave driver | driver/wave_loop.py `run_wave` :1317, `main` :3142 | Runs a wave: claims, dispatch, journal. | driver/CLAUDE.md. |
| Journal (idempotent dispatch) | driver/wave_loop.py `_write_journal_entry` :373, `_load_journal_state` :427, `_should_skip_from_journal` :606, `_try_resume_from_journal` :1679 | Crash-only resume: a journaled item is not dispatched twice. | Driver. |
| Wave scheduler | driver/wave_scheduler.py `run_wave_scheduler` :1003, `main` :1343 | Runs scheduled wave items. | driver/CLAUDE.md. |
| Context pack | driver/context_pack.py `ContextPackViolation` :31, raised :144 | Allowlist-only orchestrator context; unknown sources raise. | Driver reads. |
| Halt in wave loop | driver/wave_loop.py :1615-1619 | Warns and allows when `state_dir` is None; otherwise honors halt. | Gap G3 (section 8.1). |

### 4.3 Merge actors
No caller-identity check exists in `tools/auto_merge.py`, `tools/merge_train.py` or `tools/merge_queue.py`. A grep for `orchestrator`, `actor` and `lane` in these files finds only docstrings. "Who may run them" is prose (LANE-CONTRACT section 6) plus the out-of-repo hook in section 6.

| Component | Path | Purpose | Enforced-by / consumed-by |
|---|---|---|---|
| Batch merge | tools/auto_merge.py `check_pr_status` :88, `merge_pr` :117, `halt_reason` :50, `main` :255 | Merges named or green PRs and verifies `state == MERGED` (:127). Fails closed on halt (:75). | Orchestrator, per LANE-CONTRACT section 6. |
| Merge train | tools/merge_train.py `halt_status` :109, `check_outcome` :176, `merge_pr` :246, `run_train` :790, `main` :933 | Serial merge train with halt check and check-outcome bucketing. | No in-repo caller besides tests (for example tests/test_merge_actors_box_halt_and_ref.py). |
| Merge queue | tools/merge_queue.py `_halt_status` :321, `_pid_alive` :394, `merge_and_verify` :999, `advance_singleton` :1030, `run_pass` :1931, `main` :1985 (`--advance`) | One bounded idempotent pass per run; lock `.merge-queue-lock` with pid liveness check (used :503). | `daemons/run-merge-queue.sh`; scheduled task `AesopMergeQueue` (opt-in). |
| Auto-arm | tools/pr_sweep.py `arm_auto_merge` :203, `main` :551 | Arms native auto-merge on open non-draft PRs that lack it; reports BEHIND and never server-updates. | daemons/run-watchdog.sh `run_pr_sweep_throttled` :172. |

Receipt emit, verify and flush are in section 1.3.

## 5. Monitor, UI, MCP and dash
### 5.1 Monitor (`monitor/`)
| Component | Path | Purpose | Enforced-by / consumed-by |
|---|---|---|---|
| Signal collector | monitor/collect-signals.mjs (1339 lines); `MON` :76; `checkHeartbeats` :174 (watchdog 300 s, monitor 3600 s at :180-181); `checkMainCiStatus` :618; `checkAgentStalls` :701 | Walks known roots; writes BRIEF.md, SIGNALS.json, ACTIONS.log. Skips a cycle when the heartbeat is under 300 s old unless `AESOP_MONITOR_FORCE` is set (:107-116). | Refinement monitor by instruction (monitor/CHARTER.md). No in-repo scheduler (section 8.2). |
| Outputs | monitor/collect-signals.mjs:1290 (BRIEF.md), :1304 (SIGNALS.json), :1319 (.monitor-heartbeat), :1324 (.signal-state.json), :1012 (ACTIONS.log) | Monitor state files. | Readers in 2.3. |
| Proposals | monitor/collect-signals.mjs `emitProposal` :916, `safeAcquireLock` :902 | Appends PROPOSE-tier items to PROPOSALS.md under a lock. | Operator review. |
| Charter | monitor/CHARTER.md | Fixed goal and action tiers for the refinement monitor. | Document. |

### 5.2 UI server (`ui/`)
| Component | Path | Purpose | Enforced-by / consumed-by |
|---|---|---|---|
| Entry | ui/serve.py (prints `http://127.0.0.1:<port>` at :86) | Starts the dashboard server on loopback. | Operator. |
| Request handler | ui/handler.py `DashboardHandler` :202; GET routes `_GET_EXACT_ROUTES` (:208-226) and prefix routes (:233-239); POST routes (:247-252) | JSON APIs (`/api/state` :425, `/api/queue`, `/api/wave/*`, `/api/tracker`, `/api/backlog`, `/api/agents`, `/api/bench`, `/api/state/events`) and static files. | Dashboard web app. |
| SSE stream | ui/handler.py `serve_events` :1216 (`text/event-stream` :1246); `_STATE_SECTIONS` :45 (data, backlog, agents, tracker, status, cost) | Pushes the six state sections to clients. | ui/web. |
| SSE broadcast | ui/sse.py `broadcast_sse` :93, `collector_loop` :370, `start_collector_thread` :420 | Polls the collectors and broadcasts changed sections. | ui/handler.py. |
| CSRF and session | ui/csrf.py; ui/config.py `UI_SESSION_TOKEN_FILE` :201 (`.ui-session-token`) | Session token and origin checks for mutating routes. | ui/handler.py. |
| Collectors | ui/collectors.py `get_heartbeat_status` :145, `get_monitor_heartbeat_status` :194, `get_queue_status` :338, `load_tracker` :493, `_write_api` :542, `_tracker_api` :556 | Reads heartbeats, queue, tracker and the event store. | ui/sse.py; ui/handler.py. |
| Web app | ui/web/ (`src`, `dist`) | React dashboard. The committed `dist` is gated by a drift check. | CI `ci.yml:349-369` (build, typecheck, dist drift at :362). |

### 5.3 MCP server (`mcp/`)
| Component | Path | Purpose | Enforced-by / consumed-by |
|---|---|---|---|
| Fleet MCP server | mcp/server.mjs (server name `aesop-fleet`) | Exposes fleet tools over MCP. | Claude clients. |
| Tools | mcp/server.mjs :1149 `fleet_status`, :1157 `fleet_agents`, :1165 `fleet_tracker`, :1173 `fleet_cost`, :1181 `fleet_cost_by_wave`, :1189 `fleet_budget`, :1197 `fleet_cost_trend`, :1211 `fleet_verify_stats`, :1219 `fleet_instances`, :1227 `fleet_claims`, :1235 `fleet_multibox_summary`, :1243 `ci_job_status` | Each tool reads state files or runs a read. | Clients. |
| File reads | mcp/server.mjs :101 (watchdog heartbeat), :102 (monitor heartbeat), :103 (SECURITY-ALERTS.log), :104 (tracker.json), :105 (orchestrator-status.json), :106 (ledger OUTCOMES-LEDGER.md), :510 (.HALT) | Read-only (`readFileSync`). | Tools above. |
| Instances helper | mcp/instances-claims.py `main` :107, `describe_backend` :61 | Prints active instances, file claims and a summary as JSON from the state store. | Spawned by mcp/server.mjs (mcp/CLAUDE.md). |

### 5.4 Dashboard shell tools (`dash/`)
| Component | Path | Purpose | Enforced-by / consumed-by |
|---|---|---|---|
| Watchdog GUI | dash/watchdog-gui.sh `render_frame` :58, `main` :117 | Terminal view of watchdog heartbeat status (reads `state/.watchdog-heartbeat`, :21). | Operator. |
| Agent transcript summary | dash/dash-extra.mjs `parseAgentJsonl` :113, `walk` :256, `loadCache` :86, `saveCache` :99 | Summarizes agent transcripts from a cache. | Dashboard. |

## 6. Brain-side hooks referenced by this repo
Brain hooks live at `~/.claude/hooks/<name>.mjs`. Their source is outside this repo. This section lists names only and does not read `~/.claude`.

### 6.1 Hooks copied into this repo
| Hook | Path | Registered in repo | Note |
|---|---|---|---|
| force-model-policy | hooks/claude/force-model-policy.mjs | `.claude/settings.json` (`$CLAUDE_PROJECT_DIR/hooks/claude/force-model-policy.mjs`) | The only hook registered by the repo's settings file. |
| no-heredoc-file-authoring | hooks/claude/no-heredoc-file-authoring.mjs (+ `.test.mjs`) | No. | Documented in hooks/CLAUDE.md. |
| no-scrollback-as-record | hooks/claude/no-scrollback-as-record.mjs (+ `.test.mjs`) | No. | Documented in hooks/CLAUDE.md. |

### 6.2 Brain hooks named in this repo (live copies outside the repo)
| Hook name | Repo files that name it |
|---|---|
| force-haiku-subagents | docs/paper/EVIDENCE.md; docs/paper/evidence.json; docs/archive/spikes/tiered-cognition/* |
| no-serial-pileon | docs/paper/EVIDENCE.md; docs/paper/evidence.json |
| claim-binding | STATE.md |
| checkpoint-before-clear | STATE.md |
| reprime-from-disk | tests/test_state_md_verifier.py |
| no-polling | STATE.md |
| no-grep-on-main-thread | STATE.md |
| deny-default-branch-push | STATE.md |
| session-end-checkpoint | STATE.md |
| task-event-projector | tools/ingest_task_events.py (writes the source JSONL this repo ingests) |
| no-orchestrator-merge-train | tools/dispatch_lint.py:8; tests/test_dispatch_lint.py |
| primary-branch-guard | tools/worktree_marker.py; tools/INDEX.md; STATE.md; BUILDLOG.md |

### 6.3 Path convention
| Reference | Where |
|---|---|
| `~/.claude/hooks/<name>.mjs` | docs/archive/spikes/tiered-cognition/ACTIVATION.md:59; STATE.md:59 |
| `~/.claude/state/task-events.jsonl` (ingest source) | tools/ingest_task_events.py:83-84 |

## 7. Documented principles
Status: **enforced** means a code path in the tree enforces the principle. **partial** means code exists but part of the principle is not enforced or not wired. **prose** means no code path was found.

| # | Principle | Doc site | Status | Enforcing file (path:line) |
|---|---|---|---|---|
| 1 | Fail-closed gates | README.md:24 | enforced | hooks/pre-push-policy.sh:111 `gate_tool_status`; tools/secret_scan.py:503 `scan_file` |
| 2 | Secret scan on every push | docs/CARDINAL-RULES.md:102 | enforced | hooks/pre-push-policy.sh:683 `check_secret_scan`; .github/workflows/ci.yml:415-417 |
| 3 | Crash-only, disk is the memory | docs/CHECKPOINTING.md:51 | enforced | driver/wave_loop.py:427 `_load_journal_state`; :1679 `_try_resume_from_journal` |
| 4 | Append-only log | state_store/CLAUDE.md:52 | enforced | state_store/store.py:204 `append`; no delete method in state_store/*.py |
| 5 | Single-writer control files | docs/CARDINAL-RULES.md:89 | partial | state_store/write_api.py:76 `WriteAPI`; tools/reconcile.py:229 (stated, not checked); driver/wave_scheduler.py:29 (assumption) |
| 6 | Orchestrator isolation, bounded reads | docs/CARDINAL-RULES.md:51 | enforced | driver/context_pack.py:31 `ContextPackViolation`, raised :144 |
| 7 | Lanes never merge | LANE-CONTRACT.md section 6 (:202) | partial | tools/dispatch_lint.py:57 `FORBIDDEN_PATTERNS`; out-of-repo hook (section 6) |
| 8 | Merge verified against the platform | LANE-CONTRACT.md section 6 | enforced | tools/auto_merge.py:127 (`state != 'MERGED'`); tools/merge_queue.py:999 `merge_and_verify` |
| 9 | Zombie resurrection fail-closed | docs/RECEIPTS.md:52 | enforced | tools/tracker_guard.py:244 `cmd_check`, :311 `cmd_enforce`; hooks/pre-push-policy.sh:767 |
| 10 | Ratchet baselines, shrink-only | LANE-CONTRACT.md:67 | enforced (the `--update-baseline` ban is prose) | tools/encoding_lint.py:361 `ascii_findings`; tools/pyflakes_gate.py:170 `check_ratchet` |
| 11 | Heartbeat and lease liveness | docs/GOVERNANCE.md:16 | enforced | daemons/run-watchdog.sh:16 `acquire_lock`; state_store/lease_claims.py:263 (`BEGIN IMMEDIATE`); state_store/instance_projection.py:276 `detect_stale_instances` |
| 12 | Fencing, single primary | state_store/CLAUDE.md:107 | partial (no production caller) | state_store/failover.py:299 `FencingToken`, :320 `assert_fenced`, :485 `elect_primary` |
| 13 | Signed receipts | docs/RECEIPT-GATE.md:177 | partial (verify-receipt is not required) | tools/receipt_common.py:84 `sign`; tools/verify_receipt.py:134 `verify` |
| 14 | Content-addressed tree binding | docs/RECEIPT-GATE.md | enforced | tools/verify_receipt.py:164 (tree hash recomputed and compared) |
| 15 | Incident-to-trap | docs/paper/aesop-arxiv.tex | partial (fake-green only) | tests/test_traps.py:37 `TestFakeGreenTrap`; tools/incident_report.py (ci.yml:773-774) |
| 16 | Idempotent dispatch via journal | docs/MICROKERNEL.md | enforced | driver/wave_loop.py:373 `_write_journal_entry`; :606 `_should_skip_from_journal` |
| 17 | Fake green and watched-to-fail | LANE-CONTRACT.md section 5 | partial (watched-to-fail is prose) | tests/test_traps.py:37 |
| 18 | Never fit green | LANE-CONTRACT.md section 3 | partial (assertion relaxation not enforced) | tools/verify_test_suite_count.py (shard coverage) |
| 19 | Pride bar, never ship known-broken | docs/CARDINAL-RULES.md:40 | prose | none |

## 8. Known gaps
"Confirmed" means the tree shows the gap. "Modified" means the inventory claim was narrowed or restated to match the tree.

### 8.1 Gaps from the inventories
| ID | Gap | Status | Evidence |
|---|---|---|---|
| lane 2 (latent) | `LeaseStore.renew` (state_store/lease_claims.py:339) has no production caller. | Modified: reached only through `LocalLeaseBackend.renew` (state_store/claim_backend.py:135-137), which no non-test code calls. | `grep '\.renew('` outside tests returns only claim_backend.py:137. |
| lane 2 (UNVERIFIED) | `elect_primary`, `assert_fenced`, `fenced_write` (state_store/failover.py:320, :341, :485) have no non-test callers: fencing is test-only in production. | Confirmed. | No non-test caller found. |
| lane 2 (UNVERIFIED) | driver/wave_loop.py:1616-1619 warns only (halt disabled) when state_dir is None. | Confirmed at :1615-1619. | Warn-and-allow branch. |
| lane 1 D8 | "ledger" projection requested but unregistered. | Confirmed. | tools/state_rebuild.py:217, :294 call `api.project("ledger")`; state_store/api.py:15-18 registers only `tracker` and `orchestrator_status`; api.py:83 raises ValueError. |
| lane 4 K5 | Escape detection has no scheduled cadence. | Modified: root CLAUDE.md:7 says detection runs "whenever the orchestrator runs idle_tick (no scheduled cadence)". | No task in daemons/install-tasks.ps1 runs idle_tick or detect_red_ci_runs. Both live in `~/scripts`, outside this repo. |
| lane 4 K8 | CANDIDATE: pr_sweep arms native auto-merge on every open non-draft PR outside the orchestrator hook scope. | Candidate; no repro. | tools/pr_sweep.py:11-14 (docstring); `arm_auto_merge` :203; called from daemons/run-watchdog.sh:172. |
| lane 3 conflict C | docs/CARDINAL-RULES.md:114 says "Python runs locally only (no cloud runners)". | Confirmed. | ci.yml runs Python on hosted runners (matrix at :161). |
| lane 3 conflict D | docs/CARDINAL-RULES.md:51 orchestrator read list omits tracker.json. | Confirmed. | driver/CLAUDE.md:61 includes tracker.json. |
| new (not in inventories) | `LANE-CONTRACT.md` section 3 names `.encoding-baseline.json`. | Confirmed absent. | `ls` shows no such file; the encoding ratchet baseline is tools/encoding-ascii-baseline.json. |
| new (not in inventories) | Pre-push pyflakes ratchet skips with a warning when its tool is missing. | Confirmed fail-open. | hooks/pre-push-policy.sh:1290. |

### 8.2 Found while mapping (not in the inventories)
| Gap | Evidence |
|---|---|
| The monitor heartbeat path differs between writer and reader unless the two roots coincide. | Writer: monitor/collect-signals.mjs:76, :1319 (AESOP_ROOT/monitor). Reader: daemons/run-watchdog.sh:442 (CONDUCTOR_ROOT/monitor). |
| No in-repo caller-identity check on the merge tools. | tools/auto_merge.py, tools/merge_train.py, tools/merge_queue.py: grep for `orchestrator`, `actor`, `lane` finds docstrings only. |
| `selfheal.sh` has no scheduled task in the repo's installer. | daemons/install-tasks.ps1 registers three tasks; none runs selfheal.sh. |
| The monitor has no in-repo scheduler. | Only `-MonitorCommand` in daemons/install-tasks.ps1 references it, and the command is supplied by the caller. |

### 8.3 Not carried (inventory claims the tree refuted or could not confirm)
| Inventory claim | Result on origin/main |
|---|---|
| lane 1 D3: boot_state.py and goal_state.py absent | Refuted: both exist in tools/. |
| lane 1 D4 and lane 2: auto_merge.py has no halt check | Refuted: tools/auto_merge.py:50 `halt_reason`, called :75. |
| lane 1 D7: task stream has no reader | Refuted: tools/boot_state.py:119 `read_store` reads it. No projection exists (section 3.3). |
| lane 2: multi_dispatch hard-codes claim TTL 300 | Refuted: tools/multi_dispatch.py:65 `claim_ttl_seconds` derives the TTL from `--timeout`. |
| lane 2: merge_queue lock reclaimed on age alone | Refuted: tools/merge_queue.py:503 checks `_pid_alive`. |
| lane 2: no pre-push rule blocks tag pushes from main | Premise inverted: tag-only pushes are allowed from any branch (hooks/pre-push-policy.sh:531-538). |
| lane 3 B: GOVERNANCE.md:101 says the orchestrator merges | Refuted: the line says the merge is delegated to a dispatched agent running `tools/auto_merge.py`. |
| lane 3 E: PrimaryGenerationMismatch and re_assume_primary named in state_store/CLAUDE.md | Refuted: absent from the doc and the code. |
| lane 3 F: MICROKERNEL.md names recovery_journal.ndjson | Refuted: the doc says "recovery journal" with no file name. |
| lane 4 K1: symbol survival and emit-receipt run the pushed tree's tools | Refuted: both use `gate_base_dir` (hooks/pre-push-policy.sh:991, :1860). |
| lane 4 K2: gate tamper regex misses gen_* | Refuted: the regex in `check_gate_tamper` (hooks/pre-push-policy.sh:258-279) matches `tools/gen_*` and `tools/generated_*`. |
| lane 4 K3: AESOP_CI_SHARD_TRIPWIRE_ARMED not scrubbed | Refuted: tools/emit_receipt.py:225 lists it in `SCRUBBED_ENV_VARS`. |
| lane 1 D1: LANE-CONTRACT "injected" by a hook | Phrase absent from root CLAUDE.md; the injector is outside the repo. |
| lane 4 K4, K6, K7, K9, K10; the external conductor checkout watchdog and monitor copies | Refer to files outside the repo; not checkable here. |
| Runtime measurements (event-store lag, usage-watchdog staleness, live task states, stray directory) | Runtime state, not in the tree; dropped. |

## 9. Service interactions (how the machinery connects at runtime)
**Basis.** origin/main at `ab27a6cc`, read on that tree for this section. Sections 1-8 cite `cb972c56` and their line numbers have moved (for example `run-watchdog.sh` `main` is now :332, not :306). Re-derive a cited line before relying on it.

**Scope.** Every edge in 9.2 names the file:line that makes it happen. Items that live outside this repo (the scheduled-task XML on the live box, `~/.claude` hooks, the machinery repo checkout, the contents of the `~/aesop-daemon-runtime` checkout) are marked **UNVERIFIED** in 9.5 and are not asserted as fact.

### 9.1 Runtime topology
```
[A] Orchestrator = interactive Claude Code session (outside repo; no daemon)
 |  brain hooks ~/.claude/hooks/*.mjs            (names only, section 6)
 |  repo hook .claude/settings.json PreToolUse Agent|Task
 |     -> hooks/claude/force-model-policy.mjs
 |  dispatch = harness agent-call or Task. ClaudeCodeDriver.dispatch_worker is a
 |     seam that raises outside the harness; it is not a subprocess.
 v
[B] Lanes = stateless agents, each in a sibling worktree <repo>-wt-<name>
 |  branch per lane, pushed in the foreground, never --no-verify
 v
[C] Pre-push gates: hooks/pre-push-policy.sh (check calls 3333-3470)
 |  secret scan, encoding, symbol survival, generated artifacts, ...
 |  check_emit_receipt (:1900) -> tools/emit_receipt.py run_matrix
 |     signs with ~/.aesop/receipt_key.pem; no key = WARN, push continues
 |     posts check-run / commit-comment on head sha; 422 -> spool
 |  spawns receipt_autoflush.py poller, detached (:2118-2122)
 v
[D] GitHub
 |  ci.yml: ci(0..3) shards, windows, pr-footprint-gate, pr-symbol-survival
 |          (required set: tools/required-checks.json)
 |  main-full.yml: push to main + nightly cron 03:30 UTC
 |  verify-receipt.yml: pull_request, recomputes tree hash, NOT required
 |  native auto-merge: armed by the lane, re-armed by pr_sweep (E12, E13)
 ^
 |  gh CLI (read and arm)
[E] Watchdog = Windows scheduled task AesopWatchdogDaemon, every 5 min
 |  action: daemons/bin/run-hidden.exe (built by install-tasks.ps1)
 |     -> bash -lc "{ bash <root>/daemons/run-watchdog.sh --once; } >> state/cron-watchdog.log 2>&1"
 |  run-watchdog.sh --once:
 |     check_halt (tools/halt.py --status, fail closed)
 |     backup-fleet.sh cycle: push unpushed commits, secret scan,
 |        heartbeat -> $CONDUCTOR_ROOT/state/.watchdog-heartbeat
 |     receipt flush | PR sweep (15 min stamp) | task-event ingest | alert bridge
 v
[F] Refinement monitor = monitor/collect-signals.mjs (MON = $AESOP_ROOT/monitor)
 |  reads watchdog heartbeat (300 s), monitor heartbeat (3600 s), alerts, git
 |  writes monitor/BRIEF.md, SIGNALS.json, ACTIONS.log, .monitor-heartbeat,
 |     PROPOSALS.md (append under lock). Launched by /power (brain side).
 v
[G] Read surfaces (read-only toward state)
 |  ui/serve.py          -> 127.0.0.1:$PORT (default 8770), HTTP + SSE
 |  mcp/server.mjs       -> stdio JSON-RPC, server name aesop-fleet
 |  dash/watchdog-gui.sh -> terminal view of the watchdog heartbeat
 v
[H] State stores
    state_root (aesop.config.json key, gitignored; else $AESOP_ROOT/state):
       tracker_events.db, state.db, tracker.json, orchestrator-status.json,
       SECURITY-ALERTS.log, FLEET-BACKUP.log, MERGE-QUEUE.log, .HALT
    CONDUCTOR_ROOT/state: .watchdog-heartbeat, .watchdog-blocked, .HALT (box-wide)
    ~/.aesop: receipt_key.pem, receipt-spool/   (outside repo)
    ~/.claude/state/task-events.jsonl           (brain hook writes, ingest reads)
```

### 9.2 Interaction edges
Each row is one edge. "Detection" is what shows the edge has failed. Line numbers are from `ab27a6cc`.

| # | Producer -> consumer | Channel | Path or port | Cadence | Failure mode | Detection |
|---|---|---|---|---|---|---|
| E01 | Orchestrator -> lane | Harness agent-call or Task dispatch | driver/claude_code_driver.py:113-123 (seam) | per dispatch | lane stalls or fails; contract says kill and re-dispatch a fresh lane (LANE-CONTRACT s0) | per-agent heartbeat (writer UNVERIFIED, 9.5); monitor reads `.heartbeats` (monitor/collect-signals.mjs:176) |
| E02 | Orchestrator session -> repo hook | Claude Code PreToolUse hook | .claude/settings.json (matcher Agent or Task) -> hooks/claude/force-model-policy.mjs | every Agent or Task call | denial blocks the call; contract says stop and report | hook denial text in the session |
| E03 | Lane -> origin | git push (foreground) | remote `origin` | per push | push refused by a gate | non-zero push exit |
| E04 | git pre-push -> gate checks | git hook, bash | hooks/pre-push-policy.sh:3333-3470 | per push | any red gate blocks; base-pinned tools come from an origin/main copy (gate_base_dir :187) | exit code; log_event lines |
| E05 | pre-push -> receipt matrix | subprocess | hooks/pre-push-policy.sh:1900 -> tools/emit_receipt.py `run_matrix` :343 | per push when gh is present | red matrix blocks (exit 3); no key material = WARN and push continues (hooks/pre-push-policy.sh:1978-1982) | `receipt_emit_skipped_no_key_material` event; receipt on head sha |
| E06 | emit_receipt -> GitHub | gh API: check-run or commit comment; spool on 422 | tools/emit_receipt.py `post_receipt` :498; spool `resolve_spool_dir` tools/receipt_common.py:188 | per push | 422 (commit not found) spools the receipt | receipt on sha (check-run); spool directory contents |
| E07 | pre-push -> autoflush poller | detached spawn | hooks/pre-push-policy.sh:2118-2122 -> tools/receipt_autoflush.py `poll_and_flush` :67, `spawn_detached` :107 | per push with a spooled receipt | poller exits; E08 is the backstop | spool still non-empty |
| E08 | watchdog -> receipt flush | subprocess | daemons/run-watchdog.sh:176 -> tools/receipt_flush.py | every watchdog cycle | fail-open (receipt only) | spool contents; flush log |
| E09 | push or PR -> GitHub CI | GitHub Actions | .github/workflows/ci.yml (matrix :160-161; jobs :144-759); nightly schedule ci.yml:38 | per push or PR, nightly | red required check blocks merge | `gh pr view --json statusCheckRollup` |
| E10 | push to main -> main-full | GitHub Actions | .github/workflows/main-full.yml:6-13 | per main push, nightly 03:30 UTC | red run does not block a merge (not in required set) | Actions run status |
| E11 | PR -> verify-receipt | GitHub Actions; fetches the receipt for the head sha | .github/workflows/verify-receipt.yml:38, job :51; tools/verify_receipt.py `fetch_receipt_for_head` :245 | per PR update | NOT a required check (tools/required-checks.json lacks it) | check status on PR |
| E12 | lane -> GitHub native auto-merge | gh CLI | `gh pr merge <N> --auto --squash` (LANE-CONTRACT s6) | once at PR open | GitHub merges when required checks are green | `gh pr view <N> --json autoMergeRequest` |
| E13 | watchdog -> PR sweep (re-arm) | subprocess, 15-min stamp | daemons/run-watchdog.sh:198 -> tools/pr_sweep.py `arm_auto_merge` :203 (args :204) | every 15 min (state/.pr-sweep-last) | exit 2 when gh auth is missing; stuck-red and DIRTY PRs raised as signals | `pr.red` / `pr.dirty` events to the machinery repo queue (path UNVERIFIED) |
| E14 | Windows Task Scheduler -> watchdog | scheduled task action | daemons/install-tasks.ps1:6 (5 min), :59-61 (launcher path), :406 (derived command); exe built :64-87 from daemons/run-hidden.cs | every 5 min | launcher exe missing = task fails closed; install stops before tasks change if csc.exe is missing | task last-run result; tools/task_cadence_check.py |
| E15 | run-watchdog -> backup-fleet cycle | subprocess (bash) | daemons/run-watchdog.sh:416 and :445 (loop); daemons/backup-fleet.sh `main` :331 | every cycle; 150 s in daemon mode | exit 1 = no heartbeat; exit 3 = BLOCKED marker | heartbeat age; `state/.watchdog-blocked` |
| E16 | backup-fleet -> GitHub | git fetch and push; `secret_scan.py --range` | daemons/backup-fleet.sh:146 `scan_unpushed_commits`, :259 `git fetch -q origin` | each cycle | secret hit blocks that repo (exit 3) | FLEET-BACKUP.log |
| E17 | backup-fleet -> watchdog heartbeat | file write | daemons/backup-fleet.sh:24-31 (rule), :56 `write_heartbeat` -> $CONDUCTOR_ROOT/state/.watchdog-heartbeat | end of each completed cycle (exit 0 or 3) | no write when CONDUCTOR_ROOT is empty (non-canonical AESOP_ROOT) | heartbeat age (E23) |
| E18 | run-watchdog -> halt check | subprocess | daemons/run-watchdog.sh:262 `check_halt` -> tools/halt.py --status | each cycle | unknown result = HALT-CHECK-FAILED, cycle skipped | FLEET-BACKUP.log |
| E19 | operator -> all loops (halt) | file sentinel | tools/halt.py `_write_sentinel` :132 -> state/.HALT (box-wide: $CONDUCTOR_ROOT/state/.HALT) | when halted | loops that read the sentinel stop | readers listed in map 2.3 |
| E20 | brain hook -> task ingest -> state store | file, then subprocess | ~/.claude/state/task-events.jsonl (tools/ingest_task_events.py:95) -> run-watchdog.sh:241 -> store under ${AESOP_STATE_ROOT:-$AESOP_ROOT/state} | each cycle | non-zero exit logged ERROR; `--once` exits 1 | state/.task-ingest-heartbeat (ingest :93, :426) |
| E21 | watchdog -> alert bridge | subprocess (errors ignored) | daemons/CLAUDE.md invariant 7 -> tools/alert_bridge.py --scan; reads SECURITY-ALERTS.log, cursor .alert-bridge-cursor | each cycle; opt-in webhook | no-op when webhook_url is unset | cursor file; delivery target UNVERIFIED |
| E22 | watchdog -> git integrity | subprocess | daemons/CLAUDE.md (run-watchdog description) -> tools/git_integrity_check.py --repos-json .watchdog-repos.json | each cycle | DAMAGED repo writes SECURITY-ALERTS.log | SECURITY-ALERTS.log |
| E23 | watchdog heartbeat -> readers | file read | monitor/collect-signals.mjs:218-226; ui/collectors.py:145; mcp/server.mjs:101; dash/watchdog-gui.sh:27; daemons/selfheal.sh:228 cycle_once (unscheduled, E33) | monitor per cycle; UI per collector tick | stale over 300 s = staleLoops signal (collect-signals.mjs:180); missing or unparseable = GAP, never fresh | the SIGNALS.json entry; mcp fleet_status |
| E24 | monitor -> monitor heartbeat | file write | monitor/collect-signals.mjs:1359 -> $AESOP_ROOT/monitor/.monitor-heartbeat | per monitor cycle (scheduler UNVERIFIED) | watchdog reads $CONDUCTOR_ROOT/monitor/.monitor-heartbeat (run-watchdog.sh:489): path mismatch unless the roots coincide | staleness over 3600 s (collect-signals.mjs:181) |
| E25 | monitor -> proposals | file append under lock | monitor/collect-signals.mjs:944 `emitProposal`, :945 PROPOSALS.md | per cycle, PROPOSE tier only | lock contention (`safeAcquireLock` :930) | operator reads PROPOSALS.md |
| E26 | browser -> dashboard | HTTP GET and POST (CSRF-checked), SSE | ui/serve.py:86 (print); ui/handler.py:1415-1416 bind 127.0.0.1; `/events` ui/handler.py:1215; origin and Host checks :116-190; port ui/config.py:68 (env PORT, default 8770) | on demand; SSE push | non-loopback Host or Origin rejected | browser error; process not listening |
| E27 | UI collector thread -> state | in-process thread | ui/sse.py:420 `start_collector_thread`; ui/config.py:104-115 (state dir: AESOP_STATE_ROOT > config state_root > AESOP_ROOT/state) | COLLECTOR_INTERVAL | stale view | `/api/state` contents |
| E28 | MCP client -> mcp/server.mjs | stdio JSON-RPC (readline on stdin and stdout) | mcp/server.mjs:115 | per tool call | read-only; missing file returns a tool error | tool result text |
| E29 | mcp -> helpers | child_process spawn | mcp/server.mjs:290 (dash-extra.mjs), :760 (gh), :912 `spawnFleetHelper` (mcp/instances-claims.py) | per tool call | spawn error returned to client (:837) | tool result text |
| E30 | config -> every reader | file read at process start | aesop.config.json key `state_root` (gitignored, .gitignore:13) via tools/state_root.py; readers mcp/server.mjs:80-86, ui/config.py:104-115, daemons/run-watchdog.sh:473-483 (exports AESOP_STATE_ROOT) | per process start | unset: tools/boot_state.py fails closed (exit 2); watchdog falls back to $AESOP_ROOT/state | state_root error text |
| E31 | writers -> event store | Python API | state_store/store.py `EventStore.append` :204 -> tracker_events.db (state_store/paths.py:37) and state.db (paths.py:38) | per write | version check inside BEGIN IMMEDIATE | `read` / `read_since` :280, :284 |
| E32 | scheduler -> merge queue (opt-in) | scheduled task AesopMergeQueue -> daemons/run-merge-queue.sh `main` :128 -> tools/merge_queue.py --advance | install-tasks.ps1 -EnableMergeQueue (opt-in); daemons/CLAUDE.md run-merge-queue entry | 5 min when enabled | halt check fails closed (:59); exceptions.jsonl | state/MERGE-QUEUE.log; .merge-queue-heartbeat |
| E33 | selfheal -> watchdog and monitor (restart) | none in repo | daemons/selfheal.sh `main` :251, `restart_watchdog` :167, `restart_monitor` :203 | would be 60 s; NOT SCHEDULED | no restart of a dead watchdog or monitor | none in repo (see T8) |

Edge count: 33 (E01-E33). E33 is listed to show a missing edge; it is not active.

### 9.3 Where things actually run
| Process | Launched by | Checkout or cwd it runs from | Env it needs (names only) | Evidence |
|---|---|---|---|---|
| Orchestrator Claude Code session | the user, interactively | the user's session cwd | CLAUDE_PROJECT_DIR (used by the repo hook entry) | repo .claude/settings.json (PreToolUse, matcher Agent or Task) |
| Lane (agent) | orchestrator dispatch through the harness | sibling worktree `<repo>-wt-<name>` | AESOP_STATE_ROOT (export explicitly, per LANE-CONTRACT s4); optional AESOP_RECEIPT_KEY | LANE-CONTRACT s4 |
| pre-push gates and emit_receipt | git, on every push | the pushing worktree; base tools from an origin/main copy | AESOP_RECEIPT_KEY, AESOP_RECEIPT_HMAC_SECRET, AESOP_HOME, AESOP_RECEIPT_SPOOL | hooks/pre-push-policy.sh:1978; tools/receipt_common.py:186-200, :269-270 |
| Watchdog (scheduled) | Task Scheduler task AesopWatchdogDaemon, through daemons/bin/run-hidden.exe and bash | the runtime checkout (`~/aesop-daemon-runtime`, a detached git worktree at ab27a6cc per `git worktree list`) | CONDUCTOR_ROOT (must be set explicitly for a non-"aesop" checkout, see T4); AESOP_ROOT derived from the script location; AESOP_STATE_ROOT optional | daemons/CLAUDE.md; backup-fleet.sh:24-31; run-watchdog.sh:473-483 |
| backup-fleet.sh cycle | run-watchdog.sh, once per cycle | same checkout as the watchdog | CONDUCTOR_ROOT; AESOP_WATCHDOG_CYCLE_CMD (test override only) | daemons/CLAUDE.md |
| Task installer | the operator, run from the runtime checkout | PowerShell 5.1 in the runtime checkout | AESOP_INSTALL_TASKS_DRY_RUN (refuses real registration) | install-tasks.ps1 header; daemons/CLAUDE.md |
| Merge queue (opt-in) | AesopMergeQueue task -> run-merge-queue.sh | cd to $AESOP_ROOT | AESOP_QUEUE_ROOT, CONDUCTOR_ROOT | daemons/run-merge-queue.sh; daemons/CLAUDE.md |
| Refinement monitor | /power (brain side), self-paced | $AESOP_ROOT (default `.`, see T14) | AESOP_ROOT, CONDUCTOR_ROOT, AESOP_MONITOR_FORCE | monitor/collect-signals.mjs:43, :107-116 |
| UI dashboard | `python ui/serve.py`, or `bin/cli.js dash` (spawns python3 ui/serve.py) | the repo checkout | PORT (default 8770), AESOP_ROOT, AESOP_STATE_ROOT | ui/serve.py:86; ui/config.py:68, :104-115; bin/CLAUDE.md:33 |
| MCP server | the MCP client, over stdio (client config UNVERIFIED) | the repo checkout | AESOP_ROOT, AESOP_STATE_ROOT | mcp/server.mjs:80-86 |
| Dash TUI | the operator | the repo checkout | AESOP_ROOT | dash/watchdog-gui.sh:27, :33 |
| Autoflush poller | spawned detached by pre-push | the pushing worktree | AESOP_RECEIPT_SPOOL | hooks/pre-push-policy.sh:2118-2122 |

### 9.4 Known traps
- **T1. Scheduled-task changes go through install-tasks.ps1 only.** Any difference in action, arguments, logon type, or interval re-registers the task (install-tasks.ps1 header, "Divergence (D45)" in daemons/CLAUDE.md). Hand edits to the task are overwritten. A task still pointing at `run-hidden.vbs` is retired and converges on the next install. Run the installer from the runtime checkout after any launcher change (daemons/CLAUDE.md).
- **T2. The launcher is built, not committed.** `daemons/bin/run-hidden.exe` is gitignored and compiled by `install-tasks.ps1` (:64-87) with the .NET Framework `csc.exe`. A missing `csc.exe` stops the install before any task is touched. A missing exe makes the task fail closed.
- **T3. The `&&` redirect gap is FIXED.** The derived watchdog command brace-groups the run so the redirect to `state/cron-watchdog.log` covers the whole run (install-tasks.ps1:403-406). A hand-supplied `-WatchdogCommand` chain must group itself, because the installer does not wrap it (daemons/CLAUDE.md, run-hidden paragraph).
- **T4. A heartbeat needs CONDUCTOR_ROOT on the runtime checkout.** backup-fleet.sh writes a heartbeat only when CONDUCTOR_ROOT is set, or when the basename of AESOP_ROOT is exactly `aesop` (:24-31). `~/aesop-daemon-runtime` is not `aesop`. Without an explicit CONDUCTOR_ROOT the watchdog runs, writes no heartbeat, and the monitor reports the heartbeat UNRESOLVED or MISSING (collect-signals.mjs:218-226, :1097-1099).
- **T5. The runtime's fetch and reset are not in the repo.** The derived command (install-tasks.ps1:406) runs only `run-watchdog.sh --once`. STATE.md:117 records a fetch and `reset --hard origin/main` each tick, which must come from a caller-supplied `-WatchdogCommand`. A bare re-run of the installer re-registers with the derived command, which has no reset, so the reset must be passed again. This is inferred from the code and was not run (see U1).
- **T6. There is no CONDUCTOR_HOME in this repo.** The collector reads CONDUCTOR_ROOT (collect-signals.mjs:220). Without it, it uses the sibling machinery repo only when AESOP_ROOT's basename is `aesop` (:222-223). The value must be the machinery repo directory itself, because the path appended is `state/.watchdog-heartbeat` (:225). Setting it to the parent of the machinery repo points the collector at the wrong place.
- **T7. The monitor and the watchdog disagree on the monitor heartbeat path.** The monitor writes `$AESOP_ROOT/monitor/.monitor-heartbeat` (collect-signals.mjs:1359). The watchdog reads `$CONDUCTOR_ROOT/monitor/.monitor-heartbeat` (run-watchdog.sh:489). They match only when the two roots coincide (also map 8.2).
- **T8. selfheal.sh is not scheduled.** install-tasks.ps1 registers three tasks and none runs selfheal.sh (also map 8.2). Nothing in the repo restarts a dead watchdog or monitor. daemons/CLAUDE.md describes selfheal as active.
- **T9. daemons/CLAUDE.md is stale on CONDUCTOR_ROOT.** Its Environment variables section says the default is "sibling of AESOP_ROOT". The same file's heartbeat paragraph and backup-fleet.sh:24-31 say the sibling is derived only for the canonical `aesop` basename. Trust the code.
- **T10. The UI is loopback only.** The bind is hard-coded to 127.0.0.1 (ui/handler.py:1415-1416), and Host and Origin are checked against loopback names (:116-190). The port comes from the generic env name `PORT` (ui/config.py:68), so setting `PORT` for any other process changes the dashboard port. There is no `/health` route in ui/handler.py, although docs/ADOPTION-CHECKLIST.md:267-268 describes one.
- **T11. state/ and aesop.config.json are gitignored.** `.gitignore:2` ignores `state/` and `.gitignore:13` ignores `aesop.config.json`. The live store, tracker, receipts and the `state_root` value are never in git, and a fresh clone has no live state. The example's `state_root` (aesop.config.example.json) is only an example.
- **T12. A missing receipt key does not block a push.** hooks/pre-push-policy.sh:1978-1982 warns and continues. The gates still run, but a push can land with no receipt. verify-receipt is not in tools/required-checks.json, so a missing receipt is not a merge gate.
- **T13. A recorded emergency override exists.** `AESOP_PUSH_RED=1` lets a red emit_receipt run push, and the receipt is stamped `pushed_red` (hooks/pre-push-policy.sh:1915 comment). It is an escape token. LANE-CONTRACT forbids lanes from bypassing gates.
- **T14. An unset AESOP_ROOT makes the monitor read `./monitor`.** collect-signals.mjs:43 defaults AESOP_ROOT to `.`, so its files land relative to the current directory.
- **T15. Native auto-merge is the merge actor.** The AesopMergeQueue task is opt-in and must stay disabled while native auto-merge runs (LANE-CONTRACT s6; daemons/CLAUDE.md invariant 12). No loop may merge with the admin bypass.
- **T16. The orchestrator's dispatch is not a subprocess.** `ClaudeCodeDriver.dispatch_worker` raises `NotImplementedError` outside the harness (driver/claude_code_driver.py:113-123, marker text :64-68). The only subprocess path is `run_command`, for tests and out-of-harness use (:141-150).
- **T17. Line numbers in map sections 1-8 are from cb972c56.** They have drifted. Example: run-watchdog.sh `main` was :306 and is now :332.

### 9.5 UNVERIFIED (not checkable from the repo, or not checked)
- **U1.** The live scheduled-task XML for AesopWatchdogDaemon: its action arguments, whether CONDUCTOR_ROOT is exported, the git fetch and reset chain, and the Hidden, IgnoreNew and 1 h settings. daemons/CLAUDE.md states the settings, but install-tasks.ps1 has no literal for them (grep for ExecutionTimeLimit and IgnoreNew returned nothing).
- **U2.** The contents and HEAD of `~/aesop-daemon-runtime`. `git worktree list` shows it as a detached worktree at ab27a6cc. It was not checked for local changes or for whether origin/main has moved.
- **U3.** `~/.claude/.heartbeats`, named in the task brief. No file in the repo references it. The repo reads `$AESOP_ROOT/monitor/.heartbeats` (collect-signals.mjs:176) and `state/.heartbeats` (dash/watchdog-gui.sh:33). Whether `~/.claude/.heartbeats` exists was not checked.
- **U4.** The per-agent heartbeat writer (brain side).
- **U5.** The refinement monitor's scheduling (`/power` plus self-pacing). Only the agent definition text was read.
- **U6.** The machinery repo checkout and its signal-hub queue, the destination of pr_sweep signals (E13).
- **U7.** The live `state_root` value in aesop.config.json (gitignored).
- **U8.** Which MCP client registers mcp/server.mjs. No `.mcp.json` exists at the repo root.
- **U9.** The live copies of brain hooks named in map 6.2, including the task-event projector that writes `~/.claude/state/task-events.jsonl`.
- **U10.** The writer of `tracker.json`. tools/stateapi_lint.py:89-92 forbids raw writes outside the facade, but the writer was not traced.
- **U11.** Whether the watchdog, monitor and merge-queue tasks are registered on the live box right now. tools/task_cadence_check.py reads this and was not run.
- **U12.** Whether the halt sentinel is honored per checkout as daemons/CLAUDE.md states. The code path in tools/halt.py was not traced.
