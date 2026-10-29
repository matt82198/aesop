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
| Base gate copy | hooks/pre-push-policy.sh `gate_base_dir` (was :187, now stale: line 187 is in gate_trunk_ref, gate_base_dir is defined at line 222) | Archives origin/main `tools/` and `state_store/` to a temp dir so a push cannot judge itself. | Base-pinned checks; hooks/pre-push-policy.sh `gate_base_block` (was :251, now stale: line 251 is in gate_base_dir, gate_base_block is defined at line 312)) fails closed. |
| Tool status helpers | hooks/pre-push-policy.sh `gate_tool_status` (was :111, now stale: line 111 is in resolve_py_bin, gate_tool_status is defined at line 141), `gate_tool_missing_block` (was :141, now stale: line 141 is in module, gate_tool_missing_block is defined at line 171), `gate_no_python_block` (was :280, now stale: line 280 is in gate_base_dir, gate_no_python_block is defined at line 341) | Turn a missing gate tool or interpreter into a block. | Every check below. |
| Gate tamper banner | hooks/pre-push-policy.sh `check_gate_tamper` (was :258, now stale: line 258 is in gate_base_dir, check_gate_tamper is defined at line 319) | Prints GATE-CHANGED when a push edits gate files; non-blocking. | Invoked hooks/pre-push-policy.sh `run_test_mode`. |
| Branch policy | hooks/pre-push-policy.sh `check_branch_policy` (was :492, now stale: line 492 is in verify_audit_log, check_branch_policy is defined at line 553) | Branch rules; tag-only pushes are allowed from any branch (hooks/pre-push-policy.sh `verify_audit_log`). | Invoked hooks/pre-push-policy.sh `run_test_mode`. |
| Secret scan | hooks/pre-push-policy.sh `check_secret_scan` (was :683, now stale: line 683 is in get_commit_range, check_secret_scan is defined at line 758) | Runs `tools/secret_scan.py` over the pushed range. | Invoked hooks/pre-push-policy.sh `run_test_mode`; receipt part `secret-scan`. |
| Import resolution | hooks/pre-push-policy.sh `check_import_resolution` (was :819, now stale: line 819 is in check_secret_scan, check_import_resolution is defined at line 894) | Runs `tools/import_resolution_check.py`. | Invoked hooks/pre-push-policy.sh `run_test_mode`; receipt part `import-resolution-check`. |
| Conflict markers | hooks/pre-push-policy.sh `check_conflict_markers` (was :906, now stale: line 906 is in check_import_resolution, check_conflict_markers is defined at line 981) | Runs `tools/conflict_marker_check.py` over the pushed range. | Invoked hooks/pre-push-policy.sh `run_test_mode`. |
| Symbol survival | hooks/pre-push-policy.sh `check_symbol_survival` (was :989, now stale: line 989 is in check_conflict_markers, check_symbol_survival is defined at line 1064) | Runs `tools/pr_symbol_survival_check.py` (G13) from the base copy; exit 1 or 2 blocks. | Invoked hooks/pre-push-policy.sh `run_test_mode`; CI job `pr-symbol-survival`. |
| Tracker guard | hooks/pre-push-policy.sh `check_tracker_guard` (was :767, now stale: line 767 is in check_secret_scan, check_tracker_guard is defined at line 842) | Runs `tools/tracker_guard.py` check mode (zombie resurrection). | Invoked hooks/pre-push-policy.sh `run_test_mode`. |
| CLAUDE.md sync | hooks/pre-push-policy.sh `check_claudemd_sync` (was :1067, now stale: line 1067 is in check_symbol_survival, check_claudemd_sync is defined at line 1142) | Runs `tools/claudemd_sync_gate.py --check`. | Invoked hooks/pre-push-policy.sh `run_test_mode`; receipt part `claudemd-sync-gate`. |
| Tool index | hooks/pre-push-policy.sh `check_gen_tool_index` (was :1112, now stale: line 1112 is in check_symbol_survival, check_gen_tool_index is defined at line 1187) | Runs `tools/gen_tool_index.py --check` (byte identity of tools/INDEX.md). | Invoked hooks/pre-push-policy.sh `run_test_mode`; receipt part `gen-tool-index`. |
| Metrics | hooks/pre-push-policy.sh `check_metrics` (was :1157, now stale: line 1157 is in check_claudemd_sync, check_metrics is defined at line 1270) | Runs `tools/metrics_gate.py`. | Invoked hooks/pre-push-policy.sh `run_test_mode`. |
| Test suite count | hooks/pre-push-policy.sh `check_test_suite_count` (was :1202, now stale: line 1202 is in check_gen_tool_index, check_test_suite_count is defined at line 1339) | Runs `tools/verify_test_suite_count.py --check` (shard coverage). | Invoked hooks/pre-push-policy.sh `run_test_mode`; receipt part `verify-test-suite-count`. |
| CLAUDE.md headroom | hooks/pre-push-policy.sh `check_claudemd_headroom` (was :1395, now stale: line 1395 is in check_pyflakes_ratchet, check_claudemd_headroom is defined at line 1602) | Runs `tools/claudemd_lint.py --headroom`. | Invoked hooks/pre-push-policy.sh `run_test_mode`. |
| Pyflakes ratchet | hooks/pre-push-policy.sh `check_pyflakes_ratchet` (was :1256, now stale: line 1256 is in backup_only_push, check_pyflakes_ratchet is defined at line 1393) | Runs `tools/pyflakes_gate.py` against `.pyflakes-baseline.json`. Skips with a warning when the tool is absent (hooks/pre-push-policy.sh `check_metrics`). | Invoked hooks/pre-push-policy.sh `run_test_mode`; CI `ci.yml ci`. |
| Subprocess ratchet | hooks/pre-push-policy.sh `check_subprocess_guard_ratchet` (was :1327, now stale: line 1327 is in check_metrics, check_subprocess_guard_ratchet is defined at line 1534) | Runs `tools/subprocess_guard.py --check` against `.subprocess-guard-baseline.json`. | Invoked hooks/pre-push-policy.sh `run_test_mode`. |
| Encoding lint | hooks/pre-push-policy.sh `check_encoding_lint` (was :1528, now stale: line 1528 is in check_inprocess_http_timeout, check_encoding_lint is defined at line 1735) | Runs `tools/encoding_lint.py --check`. | Invoked hooks/pre-push-policy.sh `run_test_mode`; receipt part `encoding-lint`. |
| Test coverage | hooks/pre-push-policy.sh `check_test_coverage` (was :1576, now stale: line 1576 is in check_subprocess_guard_ratchet, check_test_coverage is defined at line 1783) | Runs `tools/verify_test_coverage.py --check` (orphaned tests). | Invoked hooks/pre-push-policy.sh `run_test_mode`. |
| Generated paths | hooks/pre-push-policy.sh `check_generated_paths` (was :1689, now stale: line 1689 is in log_event, check_generated_paths is defined at line 1896) | Runs the `tools/generated_paths.py` check for pushes that touch registered generated paths. | Invoked hooks/pre-push-policy.sh `run_test_mode`. |
| Generated regen | hooks/pre-push-policy.sh `check_generated_regen` (was :1789, now stale: line 1789 is in check_test_coverage, check_generated_regen is defined at line 1996) | Runs `tools/generated_push_gate.py`; a committed generated artifact must match its regenerator. | Invoked hooks/pre-push-policy.sh `run_test_mode`. |
| Linux shape | hooks/pre-push-policy.sh `check_linux_shape` (was :1621, now stale: line 1621 is in check_claudemd_headroom, check_linux_shape is defined at line 1828) | Runs `tools/linux_shape_check.py --require-wsl` on shell, workflow and Node-test changes. | Invoked hooks/pre-push-policy.sh `run_test_mode`; receipt part `linux-shape`. |
| Receipt emit | hooks/pre-push-policy.sh `check_emit_receipt` (was :1858, now stale: line 1858 is in check_linux_shape, check_emit_receipt is defined at line 2070) | Runs `tools/emit_receipt.py` after all gates pass; a red matrix blocks. | Invoked hooks/pre-push-policy.sh `run_test_mode`; see 1.3. |

### 1.2 Receipt matrix parts (`tools/emit_receipt.py` `DEFAULT_REGISTRY`, lines 57-71)
`run_matrix` (tools/emit_receipt.py `run_matrix` (was :343, now stale: line 343 is in module, run_matrix is defined at line 415)) runs each part. The pre-push check in the last column runs the same tool.

| Matrix part | Command | Pre-push check that runs the same tool | Notes |
|---|---|---|---|
| py-shard-0 .. py-shard-3 | `tools/ci_shard_runner.py n 4` | none in pre-push-policy.sh | Matrix only. |
| secret-scan | `tools/secret_scan.py .` | hooks/pre-push-policy.sh `check_secret_scan` (was :683, now stale: line 683 is in get_commit_range, check_secret_scan is defined at line 758) | |
| claudemd-sync-gate | `tools/claudemd_sync_gate.py --check` | hooks/pre-push-policy.sh `check_claudemd_sync` (was :1067, now stale: line 1067 is in check_symbol_survival, check_claudemd_sync is defined at line 1142) | |
| gen-tool-index | `tools/gen_tool_index.py --check` | hooks/pre-push-policy.sh `check_gen_tool_index` (was :1112, now stale: line 1112 is in check_symbol_survival, check_gen_tool_index is defined at line 1187) | |
| verify-test-suite-count | `tools/verify_test_suite_count.py --check` | hooks/pre-push-policy.sh `check_test_suite_count` (was :1202, now stale: line 1202 is in check_gen_tool_index, check_test_suite_count is defined at line 1339) | |
| encoding-lint | `tools/encoding_lint.py --check` | hooks/pre-push-policy.sh `check_encoding_lint` (was :1528, now stale: line 1528 is in check_inprocess_http_timeout, check_encoding_lint is defined at line 1735) | |
| import-resolution-check | `tools/import_resolution_check.py` | hooks/pre-push-policy.sh `check_import_resolution` (was :819, now stale: line 819 is in check_secret_scan, check_import_resolution is defined at line 894) | |
| sibling-import-check | `tools/sibling_import_check.py --check` | none in pre-push-policy.sh | Matrix only. |
| linux-shape | `tools/linux_shape_check.py --require-wsl` | hooks/pre-push-policy.sh `check_linux_shape` (was :1621, now stale: line 1621 is in check_claudemd_headroom, check_linux_shape is defined at line 1828) | Requires WSL. |
| browser-proofs | none on this box | none | Recorded as skipped with a reason. |
| windows-shard | none on this box | none | Recorded as skipped with a reason. |

Pre-push checks with no matrix part: conflict markers (hooks/pre-push-policy.sh `check_conflict_markers` (was :906, now stale: line 906 is in check_import_resolution, check_conflict_markers is defined at line 981)), symbol survival (hooks/pre-push-policy.sh `check_symbol_survival` (was :989, now stale: line 989 is in check_conflict_markers, check_symbol_survival is defined at line 1064)), tracker guard (hooks/pre-push-policy.sh `check_tracker_guard` (was :767, now stale: line 767 is in check_secret_scan, check_tracker_guard is defined at line 842)), metrics (hooks/pre-push-policy.sh `check_metrics` (was :1157, now stale: line 1157 is in check_claudemd_sync, check_metrics is defined at line 1270)), CLAUDE.md headroom (hooks/pre-push-policy.sh `check_claudemd_headroom` (was :1395, now stale: line 1395 is in check_pyflakes_ratchet, check_claudemd_headroom is defined at line 1602)), pyflakes ratchet (hooks/pre-push-policy.sh `check_pyflakes_ratchet` (was :1256, now stale: line 1256 is in backup_only_push, check_pyflakes_ratchet is defined at line 1393)), subprocess ratchet (hooks/pre-push-policy.sh `check_subprocess_guard_ratchet` (was :1327, now stale: line 1327 is in check_metrics, check_subprocess_guard_ratchet is defined at line 1534)), test coverage (hooks/pre-push-policy.sh `check_test_coverage` (was :1576, now stale: line 1576 is in check_subprocess_guard_ratchet, check_test_coverage is defined at line 1783)), generated paths (hooks/pre-push-policy.sh `check_generated_paths` (was :1689, now stale: line 1689 is in log_event, check_generated_paths is defined at line 1896)), generated regen (hooks/pre-push-policy.sh `check_generated_regen` (was :1789, now stale: line 1789 is in check_test_coverage, check_generated_regen is defined at line 1996)), receipt emit (hooks/pre-push-policy.sh `check_emit_receipt` (was :1858, now stale: line 1858 is in check_linux_shape, check_emit_receipt is defined at line 2070)).

### 1.3 Receipts: emit, sign, post, verify, flush
| Component | Path | Purpose | Enforced-by / consumed-by |
|---|---|---|---|
| Matrix and build | tools/emit_receipt.py `run_matrix` (was :343, now stale: line 343 is in module, run_matrix is defined at line 415), `build_receipt` (was :424, now stale: line 424 is in run_matrix, build_receipt is defined at line 565), `conclusion_for` (was :447, now stale: line 447 is in run_matrix, conclusion_for is defined at line 595), `exit_code_for` (was :451, now stale: line 451 is in run_matrix, exit_code_for is defined at line 604), `post_receipt` (was :477, now stale: line 477 is in run_matrix, post_receipt is defined at line 630), `main` (was :560, now stale: line 560 is in non_github_remote, main is defined at line 717) | Runs the matrix, builds the envelope with `tree_hash`, signs it, posts it on the head sha. Exit 3 on a red matrix with `--fail-on-red`. | Pre-push hooks/pre-push-policy.sh `check_emit_receipt` (was :1858, now stale: line 1858 is in check_linux_shape, check_emit_receipt is defined at line 2070). |
| Shared primitives | tools/receipt_common.py `CHECK_NAME` (`verify-receipt-local`), `KEY_ENV` (`AESOP_RECEIPT_KEY`), `sign`, `verify_signature`, `resolve_spool_dir` | Canonical JSON, ed25519 or HMAC-SHA256 signing, spool directory. | Emit and verify. |
| Offline verifier | tools/verify_receipt.py `verify` (was :134, now stale: line 134 is in _signature_ok, verify is defined at line 176) (tree hash recomputed and compared at `verify` (was :164, now stale: line 164 is in effective_required, verify is defined at line 176)), `fetch_receipt_for_head` (was :245, now stale: line 245 is in _parse_wrapped, fetch_receipt_for_head is defined at line 288), `main` (was :357, now stale: line 357 is in build_parser, main is defined at line 403) | Recomputes `<head>^{tree}`, checks signature, freshness and required parts. Exit 0 valid, 1 invalid, 2 unevaluable. | `.github/workflows/verify-receipt.yml` (job `verify-receipt`). |
| Flush and poller | tools/receipt_flush.py `flush_spooled_receipt`; tools/receipt_autoflush.py `poll_and_flush`, `spawn_detached` | Posts spooled receipts once the sha exists on GitHub. | Flush: `run_receipt_flush` (daemons/run-watchdog.sh `run_receipt_flush` (was :150, now stale: line 150 is in check_monitor_staleness, run_receipt_flush is defined at line 176)) every cycle. Poller: spawned from pre-push (hooks/pre-push-policy.sh `check_emit_receipt`). |

`verify-receipt` is not a required check. It is absent from `tools/required-checks.json` and from the live branch protection list (1.7).

### 1.4 Generated artifacts
| Component | Path | Purpose | Enforced-by / consumed-by |
|---|---|---|---|
| Registry | tools/generated_paths.py `REGISTRY`, `GENERATED_PATHS`, `is_generated`, `regen_matches_head` | Single list of repo-generated files and their regenerators; checks the committed artifact against HEAD. | Pre-push hooks/pre-push-policy.sh `check_generated_paths` (was :1689, now stale: line 1689 is in log_event, check_generated_paths is defined at line 1896) and hooks/pre-push-policy.sh `check_generated_regen` (was :1789, now stale: line 1789 is in check_test_coverage, check_generated_regen is defined at line 1996); `tools/auto_merge.py` `fix_branch` (conflicts resolved only over registered paths); `tools/merge_queue.py` docstring (`worktree_is_safe` restores only registered paths). |
| Push gate | tools/generated_push_gate.py | Gate module for a push that touches a generated path. | Pre-push hooks/pre-push-policy.sh `check_generated_regen` (was :1789, now stale: line 1789 is in check_test_coverage, check_generated_regen is defined at line 1996) (`check_generated_regen` (was :1807, now stale: line 1807 is in check_test_coverage, check_generated_regen is defined at line 1996)). |
| Index generator | tools/gen_tool_index.py | Writes tools/INDEX.md. | CI .github/workflows/ci.yml `ci`; pre-push hooks/pre-push-policy.sh `check_gen_tool_index` (was :1112, now stale: line 1112 is in check_symbol_survival, check_gen_tool_index is defined at line 1187). |
| Regen on main | .github/workflows/regen-on-main.yml (job `regen-on-main`, line 35) | On push to main runs `tools/regen_all.py --fix` (line 72) and opens a self-heal PR. | Workflow trigger: push to main. |

### 1.5 Documentation and PR-shape gates
| Component | Path | Purpose | Enforced-by / consumed-by |
|---|---|---|---|
| CLAUDE.md lint | tools/claudemd_lint.py | Checks domain docs for scope and size; `--headroom` mode. | CI .github/workflows/ci.yml `ci`; pre-push hooks/pre-push-policy.sh `check_claudemd_headroom` (was :1395, now stale: line 1395 is in check_pyflakes_ratchet, check_claudemd_headroom is defined at line 1602). |
| CLAUDE.md sync | tools/claudemd_sync_gate.py `check_domain_claudemd_sync`, `main` | A PR that changes domain code must also change a domain doc or INDEX.md. | CI .github/workflows/ci.yml `ci`; pre-push hooks/pre-push-policy.sh `check_claudemd_sync` (was :1067, now stale: line 1067 is in check_symbol_survival, check_claudemd_sync is defined at line 1142). |
| PR footprint | tools/pr_footprint_gate.py; CI job `pr-footprint-gate` (ci.yml `pr-footprint-gate` (was :670, now stale: line 670 is in state-drift-gate, pr-footprint-gate is defined at line 693)) | Limits PR size; runs from the base tree (`ci.yml pr-footprint-gate`). | Required check. |
| PR symbol survival | tools/pr_symbol_survival_check.py; CI job `pr-symbol-survival` (.github/workflows/ci.yml `pr-symbol-survival` (was :708, now stale: line 708 is in pr-footprint-gate, pr-symbol-survival is defined at line 731)) | G13: a symbol added by the branch must survive merges. Runs from base (.github/workflows/ci.yml `pr-symbol-survival`). | Required check; pre-push hooks/pre-push-policy.sh `check_symbol_survival` (was :989, now stale: line 989 is in check_conflict_markers, check_symbol_survival is defined at line 1064). |
| Incident report | tools/incident_report.py `--check --repo .`; CI job `incident-report-gate` (ci.yml `incident-report-gate` (was :759, now stale: line 759 is in pr-symbol-survival, incident-report-gate is defined at line 782)) | Fails closed on an unknown incident class; docs/INCIDENTS.md must equal regenerated output. | `ci.yml pr-symbol-survival`. Not required. |

### 1.6 Tests, isolation and ratchets
| Component | Path | Purpose | Enforced-by / consumed-by |
|---|---|---|---|
| Shard runner | tools/ci_shard_runner.py | Runs one of four Python test shards. | CI matrix `ci.yml ci` (`python-shard: [0,1,2,3]`); `ci.yml ci`. |
| Remote refs tripwire | tools/remote_refs_tripwire.py | Wraps Python shards; fails when a remote branch or PR attributable to the run appears. | CI `ci.yml ci`, `ci.yml windows-shard`. |
| Isolation tripwire | tools/test_isolation_tripwire.py | Runs Node tests under an isolated environment. | CI `ci.yml windows-shard` (shard 0). LANE-CONTRACT section 2. |
| Gates-wired check | tools/verify_gates_wired.py | Checks that each documented gate is referenced by a workflow. | CI `ci.yml ci`. |
| New-gate checklist | tools/new_gate_check.py | Runs the new-gate checklist, including gate inventory parity (tools/gate_inventory.py). | LANE-CONTRACT section 5. |
| Encoding ratchet | tools/encoding_lint.py `ascii_stale_entries` (was :351, now stale: line 351 is in _ascii_scope, ascii_stale_entries is defined at line 358), `ascii_findings` (was :361, now stale: line 361 is in ascii_stale_entries, ascii_findings is defined at line 385); baseline tools/encoding-ascii-baseline.json | Shrink-only ratchet on non-ASCII in tools/*.py. | Pre-push hooks/pre-push-policy.sh `check_encoding_lint` (was :1528, now stale: line 1528 is in check_inprocess_http_timeout, check_encoding_lint is defined at line 1735) and receipt part. CI does not run it directly (grep of ci.yml). |
| State API lint | tools/stateapi_lint.py; baseline .stateapi-baseline.json | Ratchet on direct state-file access. | CI `ci.yml ci`. |

### 1.7 CI workflows and branch protection
| Workflow | Triggers | Jobs (key line) | Notes |
|---|---|---|---|
| ci.yml | pull_request to main; workflow_dispatch; schedule `30 3 * * *` | docs-only-gate ci.yml `docs-only-gate`; windows-sensitive-paths ci.yml `windows-sensitive-paths`; ci ci.yml `ci` (matrix shards 0-3); windows-shard ci.yml `ci`; windows ci.yml `windows-shard`; ps1-syntax-check ci.yml `windows`; browser-proofs ci.yml `windows`; state-drift-gate ci.yml `browser-proofs`; pr-footprint-gate ci.yml `state-drift-gate`; pr-symbol-survival ci.yml `pr-footprint-gate`; incident-report-gate ci.yml `pr-symbol-survival` | Most jobs check out the PR head (`ci.yml ci`). The secret scan runs from a base copy (`ci.yml ci`). |
| main-full.yml | push to main; workflow_dispatch; schedule `30 3 * * *` | main-full-verify main-full.yml `main-full-verify`; main-full-browser-proofs main-full.yml `main-full-browser-proofs` | Nightly full matrix. |
| verify-receipt.yml | pull_request to main; workflow_dispatch | .github/workflows/verify-receipt.yml `verify-receipt` | Non-required. |
| regen-on-main.yml | push to main; workflow_dispatch | regen-on-main regen-on-main.yml `regen-on-main` | Runs `tools/regen_all.py --fix`. |
| examples.yml | pull_request and push to main, paths `examples/**` and `tools/wave_templates.py` | examples-verify examples.yml `examples-verify` | |
| pages.yml | push to main; workflow_dispatch | build pages.yml `build`; deploy pages.yml `deploy` | |
| publish.yml | release published | publish publish.yml `publish` | |
| reproduce.yml | workflow_dispatch; schedule `0 2 * * 0` | reproduce reproduce.yml `reproduce` | |
| stats-refresh.yml | schedule `0 2 * * *`; workflow_dispatch | refresh-stats stats-refresh.yml `refresh-stats` | |
| crossos-drift.yml | schedule `0 9 * * 1`; workflow_dispatch | crossos-drift crossos-drift.yml `crossos-drift` | |

**Branch protection.** `tools/required-checks.json` declares the required contexts: `ci (0)` through `ci (3)`, `windows`, `pr-footprint-gate`, `pr-symbol-survival`. `tools/required_checks_gate.py` fails on drift between this file and the enforced set. A live read of `gh api repos/matt82198/aesop/branches/main/protection/required_status_checks`, taken 2026-10-15, returned the same seven contexts (`strict: false`). `verify-receipt` and `incident-report-gate` are not required.

## 2. Daemons, installer and scheduled tasks
### 2.1 Daemons
| Component | Path | Purpose | Enforced-by / consumed-by |
|---|---|---|---|
| Watchdog entry | daemons/run-watchdog.sh `main` (was :306, now stale: line 306 is in check_halt, main is defined at line 354) | Runs one cycle with `--once`, or loops with `sleep 150` (daemons/run-watchdog.sh `main`, daemons/run-watchdog.sh `main`). Each cycle runs the backup cycle, receipt flush, PR sweep and task ingest. | Scheduled task `AesopWatchdogDaemon` (`--once`), or manual daemon mode. |
| Watchdog lock | daemons/run-watchdog.sh `acquire_lock` (was :16, now stale: line 16 is in module, acquire_lock is defined at line 41), `release_lock` (was :106, now stale: line 106 is in module, release_lock is defined at line 132) | Single instance through a mkdir lock at `state/.watchdog-lock`. | Self. |
| Monitor staleness signal | daemons/run-watchdog.sh `check_monitor_staleness` (was :118, now stale: line 118 is in reclaim_lock, check_monitor_staleness is defined at line 144) | Logs a SIGNAL when `$CONDUCTOR_ROOT/monitor/.monitor-heartbeat` (read daemons/run-watchdog.sh `main`) is missing, empty or stale. | FLEET-BACKUP.log. |
| Halt check | daemons/run-watchdog.sh `check_halt` (was :236, now stale: line 236 is in run_task_ingest, check_halt is defined at line 262) | Calls `tools/halt.py --status`; an unknown result logs HALT-CHECK-FAILED (daemons/run-watchdog.sh `check_halt`) and skips the cycle. | Fail-closed. |
| PR sweep | daemons/run-watchdog.sh `run_pr_sweep_throttled` (was :172, now stale: line 172 is in module, run_pr_sweep_throttled is defined at line 198) | Runs `tools/pr_sweep.py` at most once per 15 min (stamp `state/.pr-sweep-last`). | `tools/pr_sweep.py` `arm_auto_merge` (was :203, now stale: line 203 is in list_open_prs, arm_auto_merge is defined at line 248). |
| Task-event ingest | daemons/run-watchdog.sh `run_task_ingest` (was :200, now stale: line 200 is in run_pr_sweep_throttled, run_task_ingest is defined at line 226) | Runs `tools/ingest_task_events.py` each cycle; a non-zero exit is logged and `--once` exits 1. | Event store task stream (section 3). |
| Receipt flush | daemons/run-watchdog.sh `run_receipt_flush` (was :150, now stale: line 150 is in check_monitor_staleness, run_receipt_flush is defined at line 176) | Runs `tools/receipt_flush.py` each cycle. | Section 1.3. |
| Backup cycle | daemons/backup-fleet.sh `main`, `scan_unpushed_commits` (was :120, now stale: line 120 is in resolve_scanner, scan_unpushed_commits is defined at line 146) | Discovers repos, pushes unpushed commits, runs `secret_scan.py --range` over them. Spawned by the watchdog each cycle (override `AESOP_WATCHDOG_CYCLE_CMD`, `_credential_value_is_placeholder` (was :325, now stale: named symbol AESOP_WATCHDOG_CYCLE_CMD is not defined in tools/secret_scan.py; line 325 is in _credential_value_is_placeholder)). | FLEET-BACKUP.log. |
| Watchdog heartbeat writer | daemons/backup-fleet.sh `write_heartbeat` (was :53, now stale: line 53 is in log, write_heartbeat is defined at line 56) | Writes `$CONDUCTOR_ROOT/state/.watchdog-heartbeat`. daemons/CLAUDE.md documents the end-of-cycle rule. | Readers in 2.3. |
| Blocked marker | daemons/backup-fleet.sh `write_blocked_marker` (was :59, now stale: line 59 is in write_heartbeat, write_blocked_marker is defined at line 62), `clear_blocked_marker` (was :65, now stale: line 65 is in write_blocked_marker, clear_blocked_marker is defined at line 68) | Writes or clears `state/.watchdog-blocked` when repos are BLOCKED. | No reader verified in this map. |
| Self-heal supervisor | daemons/selfheal.sh `main` (was :200, now stale: line 200 is in restart_watchdog, main is defined at line 251), `cycle_once` (was :177, now stale: line 177 is in restart_watchdog, cycle_once is defined at line 228), `is_heartbeat_stale` (was :100, now stale: line 100 is in reclaim_lock, is_heartbeat_stale is defined at line 144), `restart_watchdog` (was :123, now stale: line 123 is in halt_sentinel_present, restart_watchdog is defined at line 167), `restart_monitor` (was :152, now stale: line 152 is in is_heartbeat_stale, restart_monitor is defined at line 203) | Reads the watchdog heartbeat (daemons/selfheal.sh `cycle_once`) and monitor heartbeat (daemons/selfheal.sh `cycle_once`); restarts stale daemons; logs state/SELFHEAL.log. | No task in daemons/install-tasks.ps1 invokes it. |
| Self-heal lock | daemons/selfheal.sh `acquire_lock` (was :24, now stale: line 24 is in module, acquire_lock is defined at line 41), `release_lock` (was :91, now stale: line 91 is in acquire_lock, release_lock is defined at line 135) | mkdir lock at `state/.selfheal-lock`. | Self. |
| Merge-queue runner | daemons/run-merge-queue.sh `main` (was :116, now stale: line 116 is in check_halt, main is defined at line 127), `check_halt` (was :57, now stale: line 57 is in module, check_halt is defined at line 60) | One bounded pass of `tools/merge_queue.py --advance` per run; no loop and no sleep (file header). | Scheduled task `AesopMergeQueue` (opt-in). |

### 2.2 Installer and task names (`daemons/install-tasks.ps1`)
| Component | Path | Purpose | Enforced-by / consumed-by |
|---|---|---|---|
| Task prefix | daemons/install-tasks.ps1 param `TaskPrefix` (line 9, default `Aesop`) | Task names are `${TaskPrefix}WatchdogDaemon`, `${TaskPrefix}RefinementMonitor`, `${TaskPrefix}MergeQueue`. | Windows Task Scheduler. |
| Register and unregister | daemons/install-tasks.ps1 `Register-DaemonTask` (was :118, now stale: line 118 is in ConvertTo-WholeMinutes, Register-DaemonTask is defined at line 258), `Unregister-DaemonTask` (was :210, now stale: line 210 is in Get-TaskDecision, Unregister-DaemonTask is defined at line 339) | Registers a task at `-IntervalMinutes`; an existing task with a different action is skipped with a warning unless `-Force`. | tests/test_install_tasks.py. |
| Audit log | daemons/install-tasks.ps1 `Append-AuditLog` (called `Append-AuditLog` (was :149, now stale: line 149 is in ConvertTo-TaskRecord, Append-AuditLog is defined at line 219), `Append-AuditLog` (was :199, now stale: line 199 is in Get-TaskDecision, Append-AuditLog is defined at line 219)) | Writes `state/install-tasks-audit.log` with action and outcome per task. | Operator. |
| Watchdog task | daemons/install-tasks.ps1 `Register-DaemonTask` (command), `Register-DaemonTask` (task name) | `bash <root>/daemons/run-watchdog.sh --once >> state/cron-watchdog.log 2>&1`, every 5 min by default. | Registered by default. |
| Monitor task | daemons/install-tasks.ps1 `-MonitorCommand` (20 min default) | Registers `${TaskPrefix}RefinementMonitor` only when a command is given. | Opt-in. |
| Merge-queue task | daemons/install-tasks.ps1 `-EnableMergeQueue` or `-MergeQueueCommand` (5 min default) | Registers `${TaskPrefix}MergeQueue`. | Opt-in. |
| Launcher | daemons/run-hidden.vbs | Runs a bash command with window style 0 and returns the child exit code. | Every task above. |

Registration scope: the default registers the watchdog only; `-EnableMergeQueue` or `-MonitorCommand` register only those; `-All` registers all three; `-Uninstall` removes all three.

### 2.3 Heartbeats and liveness files
| File | Written by | Read by |
|---|---|---|
| `$CONDUCTOR_ROOT/state/.watchdog-heartbeat` | daemons/backup-fleet.sh (was :28, now stale: line 28 is at module level, no enclosing symbol), `log` | daemons/selfheal.sh `cycle_once`; dash/watchdog-gui.sh (was :21, now stale: line 21 is at module level, no enclosing symbol); mcp/server.mjs (was :101, now stale: line 101 is at module level, no enclosing symbol), mcp/server.mjs (was :195, now stale: line 195 is at module level, no enclosing symbol); monitor/collect-signals.mjs `checkHeartbeats`; state_store/read_api.py (was :15, now stale: line 15 is at module level, no enclosing symbol) (docstring example); tools/health_checks.py `check_watchdog_heartbeat`; tools/alert_bridge.py `check_heartbeat_staleness`; ui/collectors.py `get_heartbeat_status`. |
| `monitor/.monitor-heartbeat` (under AESOP_ROOT) | monitor/collect-signals.mjs `atomicRename` (`MON` = AESOP_ROOT/monitor, `MON`) | daemons/run-watchdog.sh `main` (under CONDUCTOR_ROOT); daemons/selfheal.sh `cycle_once`; mcp/server.mjs (was :102, now stale: line 102 is at module level, no enclosing symbol), `getFleetStatus`; monitor/collect-signals.mjs (was :110, now stale: line 110 is at module level, no enclosing symbol), `checkHeartbeats`; `get_monitor_heartbeat_status`; tools/health_checks.py `check_monitor_heartbeat`. |
| `state/.task-ingest-heartbeat` | tools/ingest_task_events.py `HEARTBEAT_FILENAME` (was :82, now stale: line 82 is in module, HEARTBEAT_FILENAME is defined at line 93), `_write_heartbeat` (was :378, now stale: line 378 is in ingest, _write_heartbeat is defined at line 437) | tools/ingest_task_events.py `status` (was :236, now stale: line 236 is in _scan, status is defined at line 286); tools/boot_state.py `run_ingest` (via `ite.status`). |
| `state/.pr-sweep-last` | daemons/run-watchdog.sh (was :176, now stale: line 176 is at module level, no enclosing symbol) | daemons/run-watchdog.sh `run_pr_sweep_throttled` (was :172, now stale: line 172 is in module, run_pr_sweep_throttled is defined at line 198). |
| `state/.HALT` (box-wide and per-checkout) | tools/halt.py `_write_sentinel` (was :132, now stale: line 132 is in box_wide_sentinel_path, _write_sentinel is defined at line 140), `halt` (was :153, now stale: line 153 is in _read_sentinel, halt is defined at line 161), `halt_box_wide` (was :160, now stale: line 160 is in _read_sentinel, halt_box_wide is defined at line 168) | daemons/run-watchdog.sh `check_halt` (was :236, now stale: line 236 is in run_task_ingest, check_halt is defined at line 262); daemons/run-merge-queue.sh `check_halt` (was :57, now stale: line 57 is in module, check_halt is defined at line 60); tools/merge_queue.py `_halt_status`; tools/merge_train.py `halt_status`; tools/auto_merge.py `halt_reason` (called `refuse_if_halted`); driver/wave_loop.py `_preflight_check_ownership` (state_dir None branch). |

**Path note.** The monitor writes under `AESOP_ROOT/monitor` (monitor/collect-signals.mjs (was :76, now stale: line 76 is at module level, no enclosing symbol)). The watchdog reads under `CONDUCTOR_ROOT/monitor` (daemons/run-watchdog.sh `main`). These are the same file only when the two roots resolve to the same tree.

## 3. Event store and projections
### 3.1 Storage and write entry points
| Component | Path | Purpose | Enforced-by / consumed-by |
|---|---|---|---|
| Event store | state_store/store.py `EventStore` (class), `append` (was :204, now stale: line 204 is in _get_conn, append is defined at line 252), `read` (was :280, now stale: line 280 is in append, read is defined at line 338), `read_since` (was :284, now stale: line 284 is in append, read_since is defined at line 342), `read_all` (was :305, now stale: line 305 is in _do_append, read_all is defined at line 363), `save_snapshot` (was :331, now stale: line 331 is in _locked_write, save_snapshot is defined at line 389), `read_snapshot` (was :362, now stale: line 362 is in EventStore, read_snapshot is defined at line 420) | Append-only SQLite event log in WAL mode; version check and append inside `BEGIN IMMEDIATE`. Database file `tracker_events.db` (state_store/paths.py `TRACKER_DB_FILENAME`). | Every writer. No delete method in state_store/*.py. |
| Read facade | state_store/api.py `StateAPI`, `append`, `get`, `get_since`, `project` | Read and append facade; `project` raises ValueError for an unknown view (`project`). | ui/collectors.py; tools/state_rebuild.py; tools/boot_state.py. |
| Write facade | state_store/write_api.py `WriteAPI` | Single write entry point for tracker, orchestrator status, STATE.md and BUILDLOG. `_file_lock` (was :909, now stale: line 909 is in WriteAPI, _file_lock is defined at line 948) and the atomic renders (`_write_markdown_atomic` (was :1079, now stale: line 1079 is in _project_tracker, _write_markdown_atomic is defined at line 1118), `_render_tracker_atomic` (was :1250, now stale: line 1250 is in _append_to_markdown_atomic, _render_tracker_atomic is defined at line 1289), `_render_orchestrator_status_atomic` (was :1378, now stale: line 1378 is in _render_tracker_atomic, _render_orchestrator_status_atomic is defined at line 1417)) back it. | Callers in tools/ and driver/. |
| WriteAPI tracker entries | state_store/write_api.py `tracker_update_status`, `tracker_append_item`, `tracker_update_item` (was :324, now stale: line 324 is in tracker_append_item, tracker_update_item is defined at line 363), `tracker_archive_item` (was :389, now stale: line 389 is in tracker_update_item, tracker_archive_item is defined at line 428), `tracker_adopt_disk_items` (was :454, now stale: line 454 is in tracker_archive_item, tracker_adopt_disk_items is defined at line 493) | Tracker mutations, each appended as a `tracker` event. | tools/tracker_guard.py (imports `WriteAPI`; `enforce` reverts through it). |
| WriteAPI orchestrator entries | state_store/write_api.py `set_orchestrator_status` (was :677, now stale: line 677 is in _verify_adopted_projection, set_orchestrator_status is defined at line 716), `clear_orchestrator_status` (was :740, now stale: line 740 is in set_orchestrator_status, clear_orchestrator_status is defined at line 779) | `orchestrator_status` events. | tools/orchestrator_status.py `set_status`. |
| WriteAPI document entries | state_store/write_api.py `write_state_md` (was :776, now stale: line 776 is in set_orchestrator_status, write_state_md is defined at line 815), `append_buildlog` (was :813, now stale: line 813 is in WriteAPI, append_buildlog is defined at line 852), `rebuild_state_md` (was :871, now stale: line 871 is in append_buildlog, rebuild_state_md is defined at line 910), `rebuild_projection` (was :658, now stale: line 658 is in _disk_items_for_adopt, rebuild_projection is defined at line 697) | State markdown and BUILDLOG appends; file write precedes the event (fail-closed). | tools/ensure_state.py `ensure_state_files` (`write_state_md`); tools/state_rebuild.py state-md view. |

### 3.2 Streams and event types
| Stream | Event types (path:line of append or fold) | Writer |
|---|---|---|
| tracker | `item_created` (state_store/write_api.py `tracker_append_item` (was :307, now stale: named symbol item_created is not defined in state_store/write_api.py; line 307 is in tracker_append_item); fold state_store/projections.py `_fold_events`), `item_updated` (state_store/write_api.py `tracker_update_status`, `WriteAPI` (was :373, now stale: named symbol item_updated is not defined in state_store/write_api.py; line 373 is in WriteAPI); fold state_store/projections.py `_fold_events`), `item_archived` (state_store/write_api.py `WriteAPI` (was :434, now stale: named symbol item_archived is not defined in state_store/write_api.py; line 434 is in WriteAPI); fold state_store/projections.py `_fold_events`) | WriteAPI; `state_store/ingest.py ingest_tracker_json` backfill. |
| orchestrator_status | `phase_changed` (state_store/write_api.py `WriteAPI` (was :715, now stale: named symbol phase_changed is not defined in state_store/write_api.py; line 715 is in WriteAPI); fold state_store/projections.py `project_orchestrator_status`), `activity_changed` (state_store/write_api.py `set_orchestrator_status` (was :727, now stale: named symbol activity_changed is not defined in state_store/write_api.py; line 727 is in set_orchestrator_status); fold state_store/projections.py `project_orchestrator_status`), `status_cleared` (state_store/write_api.py `WriteAPI` (was :760, now stale: named symbol status_cleared is not defined in state_store/write_api.py; line 760 is in WriteAPI); fold state_store/projections.py `project_orchestrator_status`) | WriteAPI. |
| state_markdown | `state_md_written` (state_store/write_api.py `clear_orchestrator_status` (was :809, now stale: named symbol state_md_written is not defined in state_store/write_api.py; line 809 is in clear_orchestrator_status)), `state_md_rebuilt` (state_store/write_api.py `ensure_buildlog_exists` (was :899, now stale: named symbol state_md_rebuilt is not defined in state_store/write_api.py; line 899 is in ensure_buildlog_exists)) | WriteAPI. |
| buildlog | `buildlog_entry` (state_store/write_api.py `write_state_md` (was :848, now stale: named symbol buildlog_entry is not defined in state_store/write_api.py; line 848 is in write_state_md)) | WriteAPI. |
| claims | `claim_requested`, `claim_released` (state_store/coordination.py `fold_claims` (was :97, now stale: named symbol claim_released is not defined in state_store/coordination.py; line 97 is in fold_claims), `try_claim` (was :188, now stale: named symbol claim_released is not defined in state_store/coordination.py; line 188 is in try_claim), `try_claim`; state_store/fs_claim_log.py `KIND_CLAIM_REQUESTED`; state_store/store.py `close`) | coordination.py; fs_claim_log.py. |
| instances | `instance_registered` (state_store/instance_projection.py `register_instance`), `heartbeat`), `claim_files` (was :113, now stale: named symbol file_claim_requested is not defined in state_store/instance_projection.py; line 113 is in claim_files)), `release_files` (was :145, now stale: named symbol file_claim_released is not defined in state_store/instance_projection.py; line 145 is in release_files)) | instance_projection.py. |
| task | `task_event` (tools/ingest_task_events.py (was :79, now stale: line 79 is at module level, no enclosing symbol) constant; `api.append` tools/ingest_task_events.py `ingest`) | tools/ingest_task_events.py `status`; stream `task`, actor `task_ingest`. |
| orchestrator_goal | `goal_set` (tools/goal_state.py `GOAL_EVENT`; stream constant tools/goal_state.py `GOAL_STREAM`) | tools/goal_state.py. |

### 3.3 Source versus projection
The event streams are the source. The views below are derived from them.

| Component | Path | Kind | Readers |
|---|---|---|---|
| tracker fold | state_store/projections.py `project_tracker`, `project_tracker_with_snapshot`, `save_snapshot` | Projection, registered as `tracker` (api.py (was :15, now stale: line 15 is at module level, no enclosing symbol)). | tools/state_rebuild.py `_rebuild_tracker`, `_check_tracker`; state_store/export.py `export_tracker`. |
| orchestrator_status fold | state_store/projections.py `project_orchestrator_status` | Projection, registered as `orchestrator_status` (api.py (was :15, now stale: line 15 is at module level, no enclosing symbol)). | tools/state_rebuild.py `_rebuild_orch_status`, `_check_orch_status`; state_store/read_api.py (projection-first reads, tools/state_rebuild.py `main`). |
| agent lifecycle fold | state_store/projections.py `project_agent_lifecycle` | Exported from state_store/__init__.py (was :18, now stale: line 18 is at module level, no enclosing symbol); not registered; no caller found. | none. |
| goal fold | tools/goal_state.py `project_goal` | Folds `orchestrator_goal` outside the store's projector table. | tools/boot_state.py `read_store` (was :119, now stale: line 119 is in state_active_block, read_store is defined at line 127). |
| task stream | no projection | Raw events. | tools/boot_state.py `read_store` (was :119, now stale: line 119 is in state_active_block, read_store is defined at line 127) (`api.get(TASK_STREAM)`). |
| instances | state_store/instance_projection.py `list_active_instances`, `get_all_claimed_files`, `detect_stale_instances` | Read functions, not registered projections. | mcp/instances-claims.py; tools/instance_manager.py; tools/multi_dispatch.py; driver/wave_loop.py. |
| ledger view | not registered | Requested but missing (section 8.1). | tools/state_rebuild.py `_rebuild_ledger`, `_check_ledger`; state_store/materialize.py `materialize_ledger` (docstring). |

### 3.4 Maintenance, ingest and boot brief
| Component | Path | Purpose | Enforced-by / consumed-by |
|---|---|---|---|
| Tracker guard | tools/tracker_guard.py `cmd_seed` (was :197, now stale: line 197 is in is_zombie, cmd_seed is defined at line 231), `cmd_check` (was :244, now stale: line 244 is in cmd_seed, cmd_check is defined at line 278) (default), `cmd_enforce` (was :311, now stale: line 311 is in cmd_check, cmd_enforce is defined at line 345), `cmd_reconcile_journal` (was :393, now stale: line 393 is in cmd_enforce, cmd_reconcile_journal is defined at line 435), `cmd_adopt_disk_items` (was :476, now stale: line 476 is in cmd_reconcile_journal, cmd_adopt_disk_items is defined at line 518), `main` (was :521, now stale: line 521 is in cmd_adopt_disk_items, main is defined at line 563) | Zombie-resurrection guard; journal in `state/tracker-journal.jsonl`. `check` exits 1 on a zombie; `enforce` reverts it through WriteAPI. | Pre-push hooks/pre-push-policy.sh `check_tracker_guard` (was :767, now stale: line 767 is in check_secret_scan, check_tracker_guard is defined at line 842). |
| State rebuild | tools/state_rebuild.py `main`, `_rebuild_all`, `_check_drift` | Rebuilds views (`--all`, `--view tracker\|state-md\|orch-status\|ledger`) and checks drift (`--check`). | CI `state-drift-gate` (`ci.yml state-drift-gate (was :657, now stale: line 657 is in browser-proofs, state-drift-gate is defined at line 669)`). |
| Task-event ingest | tools/ingest_task_events.py `status`, `check` (was :214, now stale: line 214 is in _write_cursor, check is defined at line 264), `status` (was :236, now stale: line 236 is in _scan, status is defined at line 286), `main` (was :382, now stale: line 382 is in ingest, main is defined at line 441); cursor `CURSOR_FILENAME` (was :81, now stale: line 81 is in module, CURSOR_FILENAME is defined at line 92); quarantine `QUARANTINE_SUFFIX` (was :124, now stale: line 124 is in classify_kind, QUARANTINE_SUFFIX is defined at line 145) | Tail-ingests the brain task-events JSONL into the `task` stream. Cursor remembers the offset; malformed lines go to `<source>.quarantine.jsonl`. Source default `~/.claude/state/task-events.jsonl` (`DEFAULT_JSONL` (was :83, now stale: line 83 is in STREAM, DEFAULT_JSONL is defined at line 94), brain-side path). | daemons/run-watchdog.sh `run_task_ingest` (was :200, now stale: line 200 is in run_pr_sweep_throttled, run_task_ingest is defined at line 226). |
| Boot brief | tools/boot_state.py `read_store` (was :119, now stale: line 119 is in state_active_block, read_store is defined at line 127), `fit_to_cap` (was :320, now stale: line 320 is in _bound_goal, fit_to_cap is defined at line 328), `main` (was :384, now stale: line 384 is in build_parser, main is defined at line 392) | One byte-capped boot brief from the goal, task stream, tracker projection, STATE.md and BUILDLOG. | CLI. In-repo references: tests/test_boot_state.py and docs. |

### 3.5 Leases, claims and fencing
| Component | Path | Purpose | Enforced-by / consumed-by |
|---|---|---|---|
| Lease store | state_store/lease_claims.py `LeaseConflict`, `LeaseStore`, `claim`, `renew`, `release` | SQLite lease table; claims use `BEGIN IMMEDIATE` (state_store/lease_claims.py `claim`). | Through state_store/claim_backend.py `LocalLeaseBackend` (`LocalLeaseBackend`). |
| Lease renew caller | state_store/claim_backend.py `LocalLeaseBackend.renew` `renew` | Wraps `LeaseStore.renew`. | No non-test code calls `LocalLeaseBackend.renew`. |
| Coordination claims | state_store/coordination.py `try_claim` (default TTL 300 s), `release`, `current_holder` | Resource claims as events. | driver/wave_loop.py (`coordination.try_claim` `_claim_ttl_for_driver`, `coordination.release` `_run_and_capture_test_output`). |
| Filesystem claim log | state_store/fs_claim_log.py `FsClaimLog`, `fold_fs_claims` | Shared-directory claim backend for multibox. | tools/multibox_config.py `build_backend`, only when `multibox.enabled`. |
| Multi-dispatch TTL | tools/multi_dispatch.py `claim_ttl_seconds`, called `main` | Derives the claim TTL from `--timeout` and the configured floor. | tools/multi_dispatch.py. |
| Failover and fencing | state_store/failover.py `FencedWriteError`, `FencingToken`, `assert_fenced`, `fenced_write`, `elect_primary` | Primary election and generation fencing. | No non-test caller (section 8.1). |

### 3.6 Single-writer rule
| Component | Path | Purpose | Enforced-by / consumed-by |
|---|---|---|---|
| Writer discipline | state_store/write_api.py `WriteAPI` | All tracker, orchestrator and buildlog writes go through this facade. | `tools/stateapi_lint.py` ratchet (1.6). |
| STATE.md guard | tools/reconcile.py `resolve_drift` | States that STATE.md is single-writer and reconcile never writes it. | Reason string; no separate check. |
| Scheduler assumption | driver/wave_scheduler.py (was :29, now stale: line 29 is at module level, no enclosing symbol) | "SINGLE-WRITER ASSUMPTION: this pilot assumes tracker.json is NOT edited concurrently". | Assumption only, no check. |

## 4. Dispatch and lanes
### 4.1 Lane contract and its variants
| Component | Path | Purpose | Enforced-by / consumed-by |
|---|---|---|---|
| Lane contract | LANE-CONTRACT.md: section 1 "Evidence, not narration" (`1. Evidence, not narration` (was :14, now stale: line 14 is under LANE-CONTRACT.md — the standing contract every aesop lane is)); 2 "Tests" (`2. Tests` (was :24, now stale: line 24 is under Ordering: write before act, one source per fact)); 3 "Never fit green" (`3. Never fit green` (was :62, now stale: line 62 is under 2. Tests)); 4 "This repo's environment" (`4. This repo's environment (do not re-derive, do not conclude "missing")` (was :105, now stale: line 105 is under 3. Never fit green)); 4a "Before you push" (`4a. Before you push — ordered self-checks` (was :131, now stale: line 131 is under 3. Never fit green)); 4b "Native first" (`4b. AESOP NATIVE FIRST — STANDING USER RULE (aesop core)` (was :144, now stale: line 144 is under 4. This repo's environment (do not re-derive, do not conclud)); 5 "Verification" (`5. Verification` (was :168, now stale: line 168 is under 4a. Before you push — ordered self-checks)); 6 "Boundaries" (`6. Boundaries` (was :201, now stale: line 201 is under 5. Verification)); 7 "Definition of DONE" (`7. Definition of DONE — work that is not pushed does not exist` (was :251, now stale: line 251 is under 6. Boundaries)); 8 "Report format" (`8. Report format` (was :279, now stale: line 279 is under 6. Boundaries)); 9 "Linux shape" (`9. Linux shape — cross-platform test gate` (was :283, now stale: line 283 is under 6. Boundaries)) | Standing rules every lane runs under. | Read by lanes by instruction. Gates in section 1 back several rules. |
| Lean contract | LANE-CONTRACT-MIN.md | Arm B of a lane-contract A/B: walls only. Relaunches with the full contract on a fault. | Named by tools/lane_ab_report.py (docstring, line 7); no gate reads it. |
| Lanes never merge | LANE-CONTRACT.md section 6 (`5. Verification`) | Lanes push and open PRs; only the orchestrator merges, through `tools/auto_merge.py`. | Prose, plus dispatch-lint merge patterns (4.2). The enforcing hook is outside the repo (section 6). |

### 4.2 Dispatch lint and wave definitions
| Component | Path | Purpose | Enforced-by / consumed-by |
|---|---|---|---|
| Forbidden patterns | tools/dispatch_lint.py `FORBIDDEN_PATTERNS`, `find_violations` (was :253, now stale: line 253 is in is_comment_only, find_violations is defined at line 347), `scan_directory` (was :297, now stale: line 297 is in list_item_owners, scan_directory is defined at line 403), `main` (was :374, now stale: line 374 is in find_violations, main is defined at line 488) | Blocks forbidden dispatch and code patterns (`gh pr merge` without `--auto`, `--admin`, `--no-verify`, `--force`). | `hooks/pre-commit-dispatch-lint.sh`; tests/test_dispatch_lint.py. |
| Wave presets | templates/wave-presets/saas.json, data.json, library.json; tools/wave_templates.py `load_preset`, `PRESETS_DIR` | Wave-manifest presets with template variables. | CI `examples.yml` (path filter includes tools/wave_templates.py); tools/wave_manifest_lint.py. |
| Wave driver | driver/wave_loop.py `run_wave` (was :1317, now stale: line 1317 is in _orchestrator_final_catch, run_wave is defined at line 1411), `main` (was :3142, now stale: line 3142 is in _run_wave_inner, main is defined at line 3247) | Runs a wave: claims, dispatch, journal. | driver/CLAUDE.md. |
| Journal (idempotent dispatch) | driver/wave_loop.py `_write_journal_entry` (was :373, now stale: line 373 is in _item_fingerprint, _write_journal_entry is defined at line 374), `_load_journal_state` (was :427, now stale: line 427 is in _write_journal_entry, _load_journal_state is defined at line 448), `_should_skip_from_journal` (was :606, now stale: line 606 is in _get_owned_files_diff, _should_skip_from_journal is defined at line 700), `_try_resume_from_journal` (was :1679, now stale: line 1679 is in _check_cost_ceiling, _try_resume_from_journal is defined at line 1784) | Crash-only resume: a journaled item is not dispatched twice. | Driver. |
| Wave scheduler | driver/wave_scheduler.py `run_wave_scheduler`, `main` | Runs scheduled wave items. | driver/CLAUDE.md. |
| Context pack | driver/context_pack.py `ContextPackViolation`, raised `_order_sources_by_priority` | Allowlist-only orchestrator context; unknown sources raise. | Driver reads. |
| Halt in wave loop | driver/wave_loop.py `_preflight_check_ownership` | Warns and allows when `state_dir` is None; otherwise honors halt. | Gap G3 (section 8.1). |

### 4.3 Merge actors
No caller-identity check exists in `tools/auto_merge.py`, `tools/merge_train.py` or `tools/merge_queue.py`. A grep for `orchestrator`, `actor` and `lane` in these files finds only docstrings. "Who may run them" is prose (LANE-CONTRACT section 6) plus the out-of-repo hook in section 6.

| Component | Path | Purpose | Enforced-by / consumed-by |
|---|---|---|---|
| Batch merge | tools/auto_merge.py `check_pr_status`, `merge_pr`, `halt_reason`, `main` | Merges named or green PRs and verifies `state == MERGED` (`merge_pr`). Fails closed on halt (`refuse_if_halted`). | Orchestrator, per LANE-CONTRACT section 6. |
| Merge train | tools/merge_train.py `halt_status`, `check_outcome`, `merge_pr` (was :246, now stale: line 246 is in update_branch, merge_pr is defined at line 249), `run_train` (was :790, now stale: line 790 is in run_integration_train, run_train is defined at line 805), `main` (was :933, now stale: line 933 is in run_train, main is defined at line 948) | Serial merge train with halt check and check-outcome bucketing. | No in-repo caller besides tests (for example tests/test_merge_actors_box_halt_and_ref.py). |
| Merge queue | tools/merge_queue.py `_halt_status`, `_pid_alive`, `merge_and_verify`, `advance_singleton`, `run_pass`, `main` (`--advance`) | One bounded idempotent pass per run; lock `.merge-queue-lock` with pid liveness check (used tools/merge_queue.py `_reclaim_stale_lock`). | `daemons/run-merge-queue.sh`; scheduled task `AesopMergeQueue` (opt-in). |
| Auto-arm | tools/pr_sweep.py `arm_auto_merge` (was :203, now stale: line 203 is in list_open_prs, arm_auto_merge is defined at line 248), `main` (was :551, now stale: line 551 is in execute_plan, main is defined at line 621) | Arms native auto-merge on open non-draft PRs that lack it; reports BEHIND and never server-updates. | daemons/run-watchdog.sh `run_pr_sweep_throttled` (was :172, now stale: line 172 is in module, run_pr_sweep_throttled is defined at line 198). |

Receipt emit, verify and flush are in section 1.3.

## 5. Monitor, UI, MCP and dash
### 5.1 Monitor (`monitor/`)
| Component | Path | Purpose | Enforced-by / consumed-by |
|---|---|---|---|
| Signal collector | monitor/collect-signals.mjs (1339 lines); `MON`; `checkHeartbeats` (watchdog 300 s, monitor 3600 s at `checkHeartbeats`); `checkMainCiStatus` (was :618, now stale: line 618 is in detectIsolationViolations, checkMainCiStatus is defined at line 646); `checkAgentStalls` (was :701, now stale: line 701 is in checkMainCiStatus, checkAgentStalls is defined at line 729) | Walks known roots; writes BRIEF.md, SIGNALS.json, ACTIONS.log. Skips a cycle when the heartbeat is under 300 s old unless `AESOP_MONITOR_FORCE` is set (monitor/collect-signals.mjs (was :107, now stale: line 107 is at module level, no enclosing symbol)). | Refinement monitor by instruction (monitor/CHARTER.md). No in-repo scheduler (section 8.2). |
| Outputs | monitor/collect-signals.mjs `atomicRename` (BRIEF.md), `atomicRename` (SIGNALS.json), `atomicRename` (.monitor-heartbeat), `atomicRename` (.signal-state.json), monitor/collect-signals.mjs (was :1012, now stale: line 1012 is at module level, no enclosing symbol) (ACTIONS.log) | Monitor state files. | Readers in 2.3. |
| Proposals | monitor/collect-signals.mjs `emitProposal` (was :916, now stale: line 916 is in performAutoJunkQuarantine, emitProposal is defined at line 944), `safeAcquireLock` (was :902, now stale: line 902 is in performAutoJunkQuarantine, safeAcquireLock is defined at line 930) | Appends PROPOSE-tier items to PROPOSALS.md under a lock. | Operator review. |
| Charter | monitor/CHARTER.md | Fixed goal and action tiers for the refinement monitor. | Document. |

### 5.2 UI server (`ui/`)
| Component | Path | Purpose | Enforced-by / consumed-by |
|---|---|---|---|
| Entry | ui/serve.py (prints `http://127.0.0.1:<port>` at ui/serve.py (was :86, now stale: line 86 is at module level, no enclosing symbol)) | Starts the dashboard server on loopback. | Operator. |
| Request handler | ui/handler.py `DashboardHandler`; GET routes `_GET_EXACT_ROUTES` (`_GET_EXACT_ROUTES`) and prefix routes (`DashboardHandler`); POST routes (`DashboardHandler`) | JSON APIs (`/api/state` `serve_api_state`, `/api/queue`, `/api/wave/*`, `/api/tracker`, `/api/backlog`, `/api/agents`, `/api/bench`, `/api/state/events`) and static files. | Dashboard web app. |
| SSE stream | ui/handler.py `serve_events` (`text/event-stream` `serve_events`); `_STATE_SECTIONS` (data, backlog, agents, tracker, status, cost) | Pushes the six state sections to clients. | ui/web. |
| SSE broadcast | ui/sse.py `broadcast_sse`, `collector_loop`, `start_collector_thread` | Polls the collectors and broadcasts changed sections. | ui/handler.py. |
| CSRF and session | ui/csrf.py; ui/config.py `UI_SESSION_TOKEN_FILE` (`.ui-session-token`) | Session token and origin checks for mutating routes. | ui/handler.py. |
| Collectors | ui/collectors.py `get_heartbeat_status`, `get_monitor_heartbeat_status`, `get_queue_status`, `load_tracker`, `_write_api`, `_tracker_api` | Reads heartbeats, queue, tracker and the event store. | ui/sse.py; ui/handler.py. |
| Web app | ui/web/ (`src`, `dist`) | React dashboard. The committed `dist` is gated by a drift check. | CI `ci.yml ci` (build, typecheck, dist drift at `ci`). |

### 5.3 MCP server (`mcp/`)
| Component | Path | Purpose | Enforced-by / consumed-by |
|---|---|---|---|
| Fleet MCP server | mcp/server.mjs (server name `aesop-fleet`) | Exposes fleet tools over MCP. | Claude clients. |
| Tools | mcp/server.mjs `getFleetMultiboxSummary` (was :1149, now stale: named symbol fleet_status is not defined in mcp/server.mjs; line 1149 is in getFleetMultiboxSummary), `getFleetMultiboxSummary` (was :1157, now stale: named symbol fleet_agents is not defined in mcp/server.mjs; line 1157 is in getFleetMultiboxSummary), `getFleetMultiboxSummary` (was :1165, now stale: named symbol fleet_tracker is not defined in mcp/server.mjs; line 1165 is in getFleetMultiboxSummary), `getFleetMultiboxSummary` (was :1173, now stale: named symbol fleet_cost is not defined in mcp/server.mjs; line 1173 is in getFleetMultiboxSummary), `getFleetMultiboxSummary` (was :1181, now stale: named symbol fleet_cost_by_wave is not defined in mcp/server.mjs; line 1181 is in getFleetMultiboxSummary), `getFleetMultiboxSummary` (was :1189, now stale: named symbol fleet_budget is not defined in mcp/server.mjs; line 1189 is in getFleetMultiboxSummary), `getFleetMultiboxSummary` (was :1197, now stale: named symbol fleet_cost_trend is not defined in mcp/server.mjs; line 1197 is in getFleetMultiboxSummary), `getFleetMultiboxSummary` (was :1211, now stale: named symbol fleet_verify_stats is not defined in mcp/server.mjs; line 1211 is in getFleetMultiboxSummary), `getFleetMultiboxSummary` (was :1219, now stale: named symbol fleet_instances is not defined in mcp/server.mjs; line 1219 is in getFleetMultiboxSummary), `getFleetMultiboxSummary` (was :1227, now stale: named symbol fleet_claims is not defined in mcp/server.mjs; line 1227 is in getFleetMultiboxSummary), `getFleetMultiboxSummary` (was :1235, now stale: named symbol fleet_multibox_summary is not defined in mcp/server.mjs; line 1235 is in getFleetMultiboxSummary), `getFleetMultiboxSummary` (was :1243, now stale: named symbol ci_job_status is not defined in mcp/server.mjs; line 1243 is in getFleetMultiboxSummary) | Each tool reads state files or runs a read. | Clients. |
| File reads | mcp/server.mjs (was :101, now stale: line 101 is at module level, no enclosing symbol) (watchdog heartbeat), mcp/server.mjs (was :102, now stale: line 102 is at module level, no enclosing symbol) (monitor heartbeat), mcp/server.mjs (was :103, now stale: line 103 is at module level, no enclosing symbol) (SECURITY-ALERTS.log), mcp/server.mjs (was :104, now stale: line 104 is at module level, no enclosing symbol) (tracker.json), `resolveWatchdogHeartbeat` (orchestrator-status.json), `resolveWatchdogHeartbeat` (ledger OUTCOMES-LEDGER.md), `getFleetCostByWave` (.HALT) | Read-only (`readFileSync`). | Tools above. |
| Instances helper | mcp/instances-claims.py `main` (was :107, now stale: line 107 is in read, main is defined at line 162), `describe_backend` (was :61, now stale: line 61 is in module, describe_backend is defined at line 116) | Prints active instances, file claims and a summary as JSON from the state store. | Spawned by mcp/server.mjs (mcp/CLAUDE.md). |

### 5.4 Dashboard shell tools (`dash/`)
| Component | Path | Purpose | Enforced-by / consumed-by |
|---|---|---|---|
| Watchdog GUI | dash/watchdog-gui.sh `render_frame` (was :58, now stale: line 58 is in get_hb_threshold, render_frame is defined at line 68), `main` (was :117, now stale: line 117 is in render_frame, main is defined at line 127) | Terminal view of watchdog heartbeat status (reads `state/.watchdog-heartbeat`, dash/watchdog-gui.sh (was :21, now stale: line 21 is at module level, no enclosing symbol)). | Operator. |
| Agent transcript summary | dash/dash-extra.mjs `parseAgentJsonl` (was :113, now stale: line 113 is in loadCache, parseAgentJsonl is defined at line 160), `walk` (was :256, now stale: line 256 is in parseAgentJsonl, walk is defined at line 309), `loadCache` (was :86, now stale: line 86 is in module, loadCache is defined at line 108), `saveCache` (was :99, now stale: line 99 is in pruneCache, saveCache is defined at line 121) | Summarizes agent transcripts from a cache. | Dashboard. |

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
| no-orchestrator-merge-train | tools/dispatch_lint.py (was :8, now stale: line 8 is at module level, no enclosing symbol); tests/test_dispatch_lint.py |
| primary-branch-guard | tools/worktree_marker.py; tools/INDEX.md; STATE.md; BUILDLOG.md |

### 6.3 Path convention
| Reference | Where |
|---|---|
| `~/.claude/hooks/<name>.mjs` | docs/archive/spikes/tiered-cognition/ACTIVATION.md `Phase A activation (recommended first step)`; STATE.md `ACTIVE: RELEASE 0.10.0 + SELF-CLEAR (rotated 2026-10-15 16:5` |
| `~/.claude/state/task-events.jsonl` (ingest source) | tools/ingest_task_events.py `STREAM` |

## 7. Documented principles
Status: **enforced** means a code path in the tree enforces the principle. **partial** means code exists but part of the principle is not enforced or not wired. **prose** means no code path was found.

| # | Principle | Doc site | Status | Enforcing file (path:line) |
|---|---|---|---|---|
| 1 | Fail-closed gates | README.md `What it is` | enforced | hooks/pre-push-policy.sh `gate_tool_status`; tools/secret_scan.py `scan_file` |
| 2 | Secret scan on every push | docs/CARDINAL-RULES.md `8. Secret-scan & version control` | enforced | hooks/pre-push-policy.sh `check_secret_scan`; .github/workflows/ci.yml `ci` |
| 3 | Crash-only, disk is the memory | docs/CHECKPOINTING.md `Recovery workflow: On context loss or resume` | enforced | driver/wave_loop.py `_load_journal_state`; driver/wave_loop.py `_try_resume_from_journal` |
| 4 | Append-only log | state_store/CLAUDE.md `Invariants` | enforced | state_store/store.py `append`; no delete method in state_store/*.py |
| 5 | Single-writer control files | docs/CARDINAL-RULES.md `7. Control files & single-writer discipline` | partial | state_store/write_api.py `WriteAPI`; tools/reconcile.py `resolve_drift` (stated, not checked); driver/wave_scheduler.py (assumption) |
| 6 | Orchestrator isolation, bounded reads | docs/CARDINAL-RULES.md `4. Orchestrator isolation: lean context` | enforced | driver/context_pack.py `ContextPackViolation`, raised `_order_sources_by_priority` |
| 7 | Lanes never merge | LANE-CONTRACT.md section 6 (`5. Verification`) | partial | tools/dispatch_lint.py `FORBIDDEN_PATTERNS`; out-of-repo hook (section 6) |
| 8 | Merge verified against the platform | LANE-CONTRACT.md section 6 | enforced | tools/auto_merge.py `merge_pr` (`state != 'MERGED'`); tools/merge_queue.py `merge_and_verify` |
| 9 | Zombie resurrection fail-closed | docs/RECEIPTS.md `Fixes Applied` | enforced | tools/tracker_guard.py `cmd_check`, `cmd_enforce`; hooks/pre-push-policy.sh `check_secret_scan` |
| 10 | Ratchet baselines, shrink-only | LANE-CONTRACT.md `2. Tests` | enforced (the `--update-baseline` ban is prose) | tools/encoding_lint.py `ascii_findings`; tools/pyflakes_gate.py `check_ratchet` |
| 11 | Heartbeat and lease liveness | docs/GOVERNANCE.md `Heartbeat protocol` | enforced | daemons/run-watchdog.sh `acquire_lock`; state_store/lease_claims.py `claim` (`BEGIN IMMEDIATE`); state_store/instance_projection.py `detect_stale_instances` |
| 12 | Fencing, single primary | state_store/CLAUDE.md `Multibox Increment 5 (2026-10-05) - failover: stale detectio` | partial (no production caller) | state_store/failover.py `FencingToken`, `assert_fenced`, `elect_primary` |
| 13 | Signed receipts | docs/archive/RECEIPT-GATE.md `Trust boundary — what a valid receipt proves, and what it do` | partial (verify-receipt is not required) | tools/receipt_common.py `sign`; tools/verify_receipt.py `verify` |
| 14 | Content-addressed tree binding | docs/archive/RECEIPT-GATE.md | enforced | tools/verify_receipt.py `effective_required` (tree hash recomputed and compared) |
| 15 | Incident-to-trap | docs/paper/aesop-arxiv.tex | partial (fake-green only) | tests/test_traps.py `TestFakeGreenTrap`; tools/incident_report.py (ci.yml `pr-symbol-survival`) |
| 16 | Idempotent dispatch via journal | docs/MICROKERNEL.md | enforced | driver/wave_loop.py `_write_journal_entry`; driver/wave_loop.py `_should_skip_from_journal` |
| 17 | Fake green and watched-to-fail | LANE-CONTRACT.md section 5 | partial (watched-to-fail is prose) | tests/test_traps.py `TestFakeGreenTrap` |
| 18 | Never fit green | LANE-CONTRACT.md section 3 | partial (assertion relaxation not enforced) | tools/verify_test_suite_count.py (shard coverage) |
| 19 | Pride bar, never ship known-broken | docs/CARDINAL-RULES.md `3. Reliability core: inputs always produce outputs` | prose | none |

## 8. Known gaps
"Confirmed" means the tree shows the gap. "Modified" means the inventory claim was narrowed or restated to match the tree.

### 8.1 Gaps from the inventories
| ID | Gap | Status | Evidence |
|---|---|---|---|
| lane 2 (latent) | `LeaseStore.renew` (state_store/lease_claims.py `renew`) has no production caller. | Modified: reached only through `LocalLeaseBackend.renew` (state_store/claim_backend.py `renew`), which no non-test code calls. | `grep '\.renew('` outside tests returns only state_store/claim_backend.py `renew`. |
| lane 2 (UNVERIFIED) | `elect_primary`, `assert_fenced`, `fenced_write` (state_store/failover.py `fenced_write` (was :320, now stale: line 320 is in assert_fenced, fenced_write is defined at line 341), `fenced_write`, `fenced_write` (was :485, now stale: line 485 is in elect_primary, fenced_write is defined at line 341)) have no non-test callers: fencing is test-only in production. | Confirmed. | No non-test caller found. |
| lane 2 (UNVERIFIED) | driver/wave_loop.py `_preflight_check_ownership` warns only (halt disabled) when state_dir is None. | Confirmed at driver/wave_loop.py `_preflight_check_ownership`. | Warn-and-allow branch. |
| lane 1 D8 | "ledger" projection requested but unregistered. | Confirmed. | tools/state_rebuild.py `_rebuild_ledger`, `_check_ledger` call `api.project("ledger")`; state_store/api.py (was :15, now stale: line 15 is at module level, no enclosing symbol) registers only `tracker` and `orchestrator_status`; state_store/api.py `project` raises ValueError. |
| lane 4 K5 | Escape detection has no scheduled cadence. | Modified: root CLAUDE.md `RED CI Escape Detection` says detection runs "whenever the orchestrator runs idle_tick (no scheduled cadence)". | No task in daemons/install-tasks.ps1 runs idle_tick or detect_red_ci_runs. Both live in `~/scripts`, outside this repo. |
| lane 4 K8 | CANDIDATE: pr_sweep arms native auto-merge on every open non-draft PR outside the orchestrator hook scope. | Candidate; no repro. | tools/pr_sweep.py (was :11, now stale: line 11 is at module level, no enclosing symbol) (docstring); tools/pr_sweep.py `arm_auto_merge` (was :203, now stale: line 203 is in list_open_prs, arm_auto_merge is defined at line 248); called from daemons/run-watchdog.sh (was :172, now stale: line 172 is at module level, no enclosing symbol). |
| lane 3 conflict C | docs/CARDINAL-RULES.md `9. Local execution only` says "Python runs locally only (no cloud runners)". | Confirmed. | ci.yml runs Python on hosted runners (matrix at .github/workflows/ci.yml `ci`). |
| lane 3 conflict D | docs/CARDINAL-RULES.md `4. Orchestrator isolation: lean context` orchestrator read list omits tracker.json. | Confirmed. | driver/CLAUDE.md `Invariants` includes tracker.json. |
| new (not in inventories) | `LANE-CONTRACT.md` section 3 names `.encoding-baseline.json`. | Confirmed absent. | `ls` shows no such file; the encoding ratchet baseline is tools/encoding-ascii-baseline.json. |
| new (not in inventories) | Pre-push pyflakes ratchet skips with a warning when its tool is missing. | Confirmed fail-open. | hooks/pre-push-policy.sh `check_metrics`. |

### 8.2 Found while mapping (not in the inventories)
| Gap | Evidence |
|---|---|
| The monitor heartbeat path differs between writer and reader unless the two roots coincide. | Writer: monitor/collect-signals.mjs (was :76, now stale: line 76 is at module level, no enclosing symbol), `atomicRename` (AESOP_ROOT/monitor). Reader: daemons/run-watchdog.sh `main` (CONDUCTOR_ROOT/monitor). |
| No in-repo caller-identity check on the merge tools. | tools/auto_merge.py, tools/merge_train.py, tools/merge_queue.py: grep for `orchestrator`, `actor`, `lane` finds docstrings only. |
| `selfheal.sh` has no scheduled task in the repo's installer. | daemons/install-tasks.ps1 registers three tasks; none runs selfheal.sh. |
| The monitor has no in-repo scheduler. | Only `-MonitorCommand` in daemons/install-tasks.ps1 references it, and the command is supplied by the caller. |

### 8.3 Not carried (inventory claims the tree refuted or could not confirm)
| Inventory claim | Result on origin/main |
|---|---|
| lane 1 D3: boot_state.py and goal_state.py absent | Refuted: both exist in tools/. |
| lane 1 D4 and lane 2: auto_merge.py has no halt check | Refuted: tools/auto_merge.py `halt_reason`, called `refuse_if_halted`. |
| lane 1 D7: task stream has no reader | Refuted: tools/boot_state.py `read_store` (was :119, now stale: line 119 is in state_active_block, read_store is defined at line 127) reads it. No projection exists (section 3.3). |
| lane 2: multi_dispatch hard-codes claim TTL 300 | Refuted: tools/multi_dispatch.py `claim_ttl_seconds` derives the TTL from `--timeout`. |
| lane 2: merge_queue lock reclaimed on age alone | Refuted: tools/merge_queue.py `_reclaim_stale_lock` checks `_pid_alive`. |
| lane 2: no pre-push rule blocks tag pushes from main | Premise inverted: tag-only pushes are allowed from any branch (hooks/pre-push-policy.sh `verify_audit_log`). |
| lane 3 B: docs/GOVERNANCE.md `Session branches for infrastructure repos` says the orchestrator merges | Refuted: the line says the merge is delegated to a dispatched agent running `tools/auto_merge.py`. |
| lane 3 E: PrimaryGenerationMismatch and re_assume_primary named in state_store/CLAUDE.md | Refuted: absent from the doc and the code. |
| lane 3 F: MICROKERNEL.md names recovery_journal.ndjson | Refuted: the doc says "recovery journal" with no file name. |
| lane 4 K1: symbol survival and emit-receipt run the pushed tree's tools | Refuted: both use `gate_base_dir` (hooks/pre-push-policy.sh `gate_base_dir` (was :991, now stale: line 991 is in check_conflict_markers, gate_base_dir is defined at line 222), `gate_base_dir` (was :1860, now stale: line 1860 is in check_linux_shape, gate_base_dir is defined at line 222)). |
| lane 4 K2: gate tamper regex misses gen_* | Refuted: the regex in `check_gate_tamper` (hooks/pre-push-policy.sh `check_gate_tamper` (was :258, now stale: line 258 is in gate_base_dir, check_gate_tamper is defined at line 319)) matches `tools/gen_*` and `tools/generated_*`. |
| lane 4 K3: AESOP_CI_SHARD_TRIPWIRE_ARMED not scrubbed | Refuted: tools/emit_receipt.py `parse_test_count` lists it in `SCRUBBED_ENV_VARS`. |
| lane 1 D1: LANE-CONTRACT "injected" by a hook | Phrase absent from root CLAUDE.md; the injector is outside the repo. |
| lane 4 K4, K6, K7, K9, K10; the external conductor checkout watchdog and monitor copies | Refer to files outside the repo; not checkable here. |
| Runtime measurements (event-store lag, usage-watchdog staleness, live task states, stray directory) | Runtime state, not in the tree; dropped. |

## 9. Service interactions (how the machinery connects at runtime)
**Basis.** origin/main at `ab27a6cc`, read on that tree for this section. Sections 1-8 cite `cb972c56` and their line numbers have moved (for example `run-watchdog.sh` `main` is now `main` (was :332, now stale: line 332 is in run_box_fence, main is defined at line 354), not `main` (was :306, now stale: line 306 is in check_halt, main is defined at line 354)). Re-derive a cited line before relying on it.

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
 |  check_emit_receipt (hooks/pre-push-policy.sh `check_emit_receipt` (was :1900, now stale: line 1900 is in check_generated_paths, check_emit_receipt is defined at line 2070)) -> tools/emit_receipt.py run_matrix
 |     signs with ~/.aesop/receipt_key.pem; no key = WARN, push continues
 |     posts check-run / commit-comment on head sha; 422 -> spool
 |  spawns receipt_autoflush.py poller, detached (hooks/pre-push-policy.sh `check_emit_receipt`)
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
| E01 | Orchestrator -> lane | Harness agent-call or Task dispatch | driver/claude_code_driver.py `dispatch_worker` (seam) | per dispatch | lane stalls or fails; contract says kill and re-dispatch a fresh lane (LANE-CONTRACT s0) | per-agent heartbeat (writer UNVERIFIED, 9.5); monitor reads `.heartbeats` (monitor/collect-signals.mjs `checkHeartbeats`) |
| E02 | Orchestrator session -> repo hook | Claude Code PreToolUse hook | .claude/settings.json (matcher Agent or Task) -> hooks/claude/force-model-policy.mjs | every Agent or Task call | denial blocks the call; contract says stop and report | hook denial text in the session |
| E03 | Lane -> origin | git push (foreground) | remote `origin` | per push | push refused by a gate | non-zero push exit |
| E04 | git pre-push -> gate checks | git hook, bash | hooks/pre-push-policy.sh `run_test_mode` | per push | any red gate blocks; base-pinned tools come from an origin/main copy (gate_base_dir hooks/pre-push-policy.sh `gate_trunk_ref`) | exit code; log_event lines |
| E05 | pre-push -> receipt matrix | subprocess | hooks/pre-push-policy.sh `check_emit_receipt` (was :1900, now stale: line 1900 is in check_generated_paths, check_emit_receipt is defined at line 2070) -> tools/emit_receipt.py `run_matrix` (was :343, now stale: line 343 is in module, run_matrix is defined at line 415) | per push when gh is present | red matrix blocks (exit 3); no key material = WARN and push continues (hooks/pre-push-policy.sh `ensure_merge_drivers`) | `receipt_emit_skipped_no_key_material` event; receipt on head sha |
| E06 | emit_receipt -> GitHub | gh API: check-run or commit comment; spool on 422 | tools/emit_receipt.py `post_receipt` (was :498, now stale: line 498 is in module, post_receipt is defined at line 630); spool `resolve_spool_dir` tools/receipt_common.py `resolve_spool_dir` | per push | 422 (commit not found) spools the receipt | receipt on sha (check-run); spool directory contents |
| E07 | pre-push -> autoflush poller | detached spawn | hooks/pre-push-policy.sh `check_emit_receipt` -> tools/receipt_autoflush.py `poll_and_flush`, `spawn_detached` | per push with a spooled receipt | poller exits; E08 is the backstop | spool still non-empty |
| E08 | watchdog -> receipt flush | subprocess | daemons/run-watchdog.sh (was :176, now stale: line 176 is at module level, no enclosing symbol) -> tools/receipt_flush.py | every watchdog cycle | fail-open (receipt only) | spool contents; flush log |
| E09 | push or PR -> GitHub CI | GitHub Actions | .github/workflows/ci.yml (matrix .github/workflows/ci.yml `ci`; jobs `ci` to `pr-symbol-survival`); nightly schedule `schedule` | per push or PR, nightly | red required check blocks merge | `gh pr view --json statusCheckRollup` |
| E10 | push to main -> main-full | GitHub Actions | .github/workflows/main-full.yml (was :6, now stale: line 6 is at module level, no enclosing symbol) | per main push, nightly 03:30 UTC | red run does not block a merge (not in required set) | Actions run status |
| E11 | PR -> verify-receipt | GitHub Actions; fetches the receipt for the head sha | .github/workflows/verify-receipt.yml `pull_request`, job `verify-receipt`; tools/verify_receipt.py `fetch_receipt_for_head` (was :245, now stale: line 245 is in _parse_wrapped, fetch_receipt_for_head is defined at line 288) | per PR update | NOT a required check (tools/required-checks.json lacks it) | check status on PR |
| E12 | lane -> GitHub native auto-merge | gh CLI | `gh pr merge <N> --auto --squash` (LANE-CONTRACT s6) | once at PR open | GitHub merges when required checks are green | `gh pr view <N> --json autoMergeRequest` |
| E13 | watchdog -> PR sweep (re-arm) | subprocess, 15-min stamp | daemons/run-watchdog.sh (was :198, now stale: line 198 is at module level, no enclosing symbol) -> tools/pr_sweep.py `arm_auto_merge` (was :203, now stale: line 203 is in list_open_prs, arm_auto_merge is defined at line 248) (args `list_open_prs`) | every 15 min (state/.pr-sweep-last) | exit 2 when gh auth is missing; stuck-red and DIRTY PRs raised as signals | `pr.red` / `pr.dirty` events to the machinery repo queue (path UNVERIFIED) |
| E14 | Windows Task Scheduler -> watchdog | scheduled task action | daemons/install-tasks.ps1 (was :6, now stale: line 6 is at module level, no enclosing symbol) (5 min), `Get-HiddenLauncherPath` (launcher path), `Resolve-DaemonCommands` (derived command); exe built daemons/install-tasks.ps1 `Ensure-HiddenLauncher` from daemons/run-hidden.cs | every 5 min | launcher exe missing = task fails closed; install stops before tasks change if csc.exe is missing | task last-run result; tools/task_cadence_check.py |
| E15 | run-watchdog -> backup-fleet cycle | subprocess (bash) | daemons/run-watchdog.sh `main` and `main` (loop); daemons/backup-fleet.sh `main` | every cycle; 150 s in daemon mode | exit 1 = no heartbeat; exit 3 = BLOCKED marker | heartbeat age; `state/.watchdog-blocked` |
| E16 | backup-fleet -> GitHub | git fetch and push; `secret_scan.py --range` | daemons/backup-fleet.sh `scan_unpushed_commits`, `scan_unpushed_commits` (was :259, now stale: line 259 is in process_repo, scan_unpushed_commits is defined at line 146) `git fetch -q origin` | each cycle | secret hit blocks that repo (exit 3) | FLEET-BACKUP.log |
| E17 | backup-fleet -> watchdog heartbeat | file write | daemons/backup-fleet.sh (was :24, now stale: line 24 is at module level, no enclosing symbol) (rule), `write_heartbeat` -> $CONDUCTOR_ROOT/state/.watchdog-heartbeat | end of each completed cycle (exit 0 or 3) | no write when CONDUCTOR_ROOT is empty (non-canonical AESOP_ROOT) | heartbeat age (E23) |
| E18 | run-watchdog -> halt check | subprocess | daemons/run-watchdog.sh `check_halt` -> tools/halt.py --status | each cycle | unknown result = HALT-CHECK-FAILED, cycle skipped | FLEET-BACKUP.log |
| E19 | operator -> all loops (halt) | file sentinel | tools/halt.py `_write_sentinel` (was :132, now stale: line 132 is in box_wide_sentinel_path, _write_sentinel is defined at line 140) -> state/.HALT (box-wide: $CONDUCTOR_ROOT/state/.HALT) | when halted | loops that read the sentinel stop | readers listed in map 2.3 |
| E20 | brain hook -> task ingest -> state store | file, then subprocess | ~/.claude/state/task-events.jsonl (tools/ingest_task_events.py (was :95, now stale: line 95 is at module level, no enclosing symbol)) -> daemons/run-watchdog.sh `run_task_ingest` -> store under ${AESOP_STATE_ROOT:-$AESOP_ROOT/state} | each cycle | non-zero exit logged ERROR; `--once` exits 1 | state/.task-ingest-heartbeat (ingest daemons/run-watchdog.sh `acquire_lock`, daemons/run-watchdog.sh `main`) |
| E21 | watchdog -> alert bridge | subprocess (errors ignored) | daemons/CLAUDE.md invariant 7 -> tools/alert_bridge.py --scan; reads SECURITY-ALERTS.log, cursor .alert-bridge-cursor | each cycle; opt-in webhook | no-op when webhook_url is unset | cursor file; delivery target UNVERIFIED |
| E22 | watchdog -> git integrity | subprocess | daemons/CLAUDE.md (run-watchdog description) -> tools/git_integrity_check.py --repos-json .watchdog-repos.json | each cycle | DAMAGED repo writes SECURITY-ALERTS.log | SECURITY-ALERTS.log |
| E23 | watchdog heartbeat -> readers | file read | monitor/collect-signals.mjs `resolveWatchdogHeartbeat`; ui/collectors.py `get_heartbeat_status`; mcp/server.mjs (was :101, now stale: line 101 is at module level, no enclosing symbol); dash/watchdog-gui.sh `HB`; daemons/selfheal.sh (was :228, now stale: line 228 is at module level, no enclosing symbol) cycle_once (unscheduled, E33) | monitor per cycle; UI per collector tick | stale over 300 s = staleLoops signal (monitor/collect-signals.mjs `checkHeartbeats`); missing or unparseable = GAP, never fresh | the SIGNALS.json entry; mcp fleet_status |
| E24 | monitor -> monitor heartbeat | file write | monitor/collect-signals.mjs (was :1359, now stale: line 1359 is at module level, no enclosing symbol) -> $AESOP_ROOT/monitor/.monitor-heartbeat | per monitor cycle (scheduler UNVERIFIED) | watchdog reads $CONDUCTOR_ROOT/monitor/.monitor-heartbeat (daemons/run-watchdog.sh (was :489, now stale: line 489 is at module level, no enclosing symbol)): path mismatch unless the roots coincide | staleness over 3600 s (monitor/collect-signals.mjs `checkHeartbeats`) |
| E25 | monitor -> proposals | file append under lock | monitor/collect-signals.mjs `emitProposal`, `emitProposal` PROPOSALS.md | per cycle, PROPOSE tier only | lock contention (monitor/collect-signals.mjs `safeAcquireLock`) | operator reads PROPOSALS.md |
| E26 | browser -> dashboard | HTTP GET and POST (CSRF-checked), SSE | ui/serve.py (was :86, now stale: line 86 is at module level, no enclosing symbol) (print); ui/handler.py `run_server` bind 127.0.0.1; `/events` ui/handler.py `serve_events`; origin and Host checks ui/handler.py `_is_local_origin`; port ui/config.py `reload` (env PORT, default 8770) | on demand; SSE push | non-loopback Host or Origin rejected | browser error; process not listening |
| E27 | UI collector thread -> state | in-process thread | ui/sse.py `start_collector_thread`; ui/config.py `reload` (state dir: AESOP_STATE_ROOT > config state_root > AESOP_ROOT/state) | COLLECTOR_INTERVAL | stale view | `/api/state` contents |
| E28 | MCP client -> mcp/server.mjs | stdio JSON-RPC (readline on stdin and stdout) | mcp/server.mjs (was :115, now stale: line 115 is at module level, no enclosing symbol) | per tool call | read-only; missing file returns a tool error | tool result text |
| E29 | mcp -> helpers | child_process spawn | mcp/server.mjs `getFleetStatus` (dash-extra.mjs), `getCIJobStatus` (gh), `spawnFleetHelper` (was :912, now stale: line 912 is in resolvePython, spawnFleetHelper is defined at line 930) (mcp/instances-claims.py) | per tool call | spawn error returned to client (mcp/server.mjs `getCIJobStatus`) | tool result text |
| E30 | config -> every reader | file read at process start | aesop.config.json key `state_root` (gitignored, .gitignore rule `aesop.config.json`) via tools/state_root.py; readers mcp/server.mjs (was :80, now stale: line 80 is at module level, no enclosing symbol), ui/config.py `reload`, daemons/run-watchdog.sh (was :473, now stale: line 473 is at module level, no enclosing symbol) (exports AESOP_STATE_ROOT) | per process start | unset: tools/boot_state.py fails closed (exit 2); watchdog falls back to $AESOP_ROOT/state | state_root error text |
| E31 | writers -> event store | Python API | state_store/store.py `_get_conn` -> tracker_events.db (state_store/paths.py `TRACKER_DB_FILENAME`) and state.db (`INSTANCES_DB_FILENAME`) | per write | version check inside BEGIN IMMEDIATE | `read` `read` (was :280, now stale: line 280 is in append, read is defined at line 338) / `read_since` `read_since` (was :284, now stale: line 284 is in append, read_since is defined at line 342) |
| E32 | scheduler -> merge queue (opt-in) | scheduled task AesopMergeQueue -> daemons/run-merge-queue.sh `main` -> tools/merge_queue.py --advance | daemons/install-tasks.ps1 -EnableMergeQueue (opt-in); daemons/CLAUDE.md run-merge-queue entry | 5 min when enabled | halt check fails closed (`check_halt` (was :59, now stale: line 59 is in module, check_halt is defined at line 60)); exceptions.jsonl | state/MERGE-QUEUE.log; .merge-queue-heartbeat |
| E33 | selfheal -> watchdog and monitor (restart) | none in repo | daemons/selfheal.sh `main`, `restart_watchdog`, `restart_monitor` | would be 60 s; NOT SCHEDULED | no restart of a dead watchdog or monitor | none in repo (see T8) |

Edge count: 33 (E01-E33). E33 is listed to show a missing edge; it is not active.

### 9.3 Where things actually run
| Process | Launched by | Checkout or cwd it runs from | Env it needs (names only) | Evidence |
|---|---|---|---|---|
| Orchestrator Claude Code session | the user, interactively | the user's session cwd | CLAUDE_PROJECT_DIR (used by the repo hook entry) | repo .claude/settings.json (PreToolUse, matcher Agent or Task) |
| Lane (agent) | orchestrator dispatch through the harness | sibling worktree `<repo>-wt-<name>` | AESOP_STATE_ROOT (export explicitly, per LANE-CONTRACT s4); optional AESOP_RECEIPT_KEY | LANE-CONTRACT s4 |
| pre-push gates and emit_receipt | git, on every push | the pushing worktree; base tools from an origin/main copy | AESOP_RECEIPT_KEY, AESOP_RECEIPT_HMAC_SECRET, AESOP_HOME, AESOP_RECEIPT_SPOOL | hooks/pre-push-policy.sh `ensure_merge_drivers`; tools/receipt_common.py `has_key_material`, `resolve_receipt_key_path` |
| Watchdog (scheduled) | Task Scheduler task AesopWatchdogDaemon, through daemons/bin/run-hidden.exe and bash | the runtime checkout (`~/aesop-daemon-runtime`, a detached git worktree at ab27a6cc per `git worktree list`) | CONDUCTOR_ROOT (must be set explicitly for a non-"aesop" checkout, see T4); AESOP_ROOT derived from the script location; AESOP_STATE_ROOT optional | daemons/CLAUDE.md; backup-fleet.sh (was :24, now stale: line 24 is at module level, no enclosing symbol); run-watchdog.sh (was :473, now stale: line 473 is at module level, no enclosing symbol) |
| daemons/backup-fleet.sh cycle | run-watchdog.sh, once per cycle | same checkout as the watchdog | CONDUCTOR_ROOT; AESOP_WATCHDOG_CYCLE_CMD (test override only) | daemons/CLAUDE.md |
| Task installer | the operator, run from the runtime checkout | PowerShell 5.1 in the runtime checkout | AESOP_INSTALL_TASKS_DRY_RUN (refuses real registration) | daemons/install-tasks.ps1 header; daemons/CLAUDE.md |
| Merge queue (opt-in) | AesopMergeQueue task -> run-merge-queue.sh | cd to $AESOP_ROOT | AESOP_QUEUE_ROOT, CONDUCTOR_ROOT | daemons/run-merge-queue.sh; daemons/CLAUDE.md |
| Refinement monitor | /power (brain side), self-paced | $AESOP_ROOT (default `.`, see T14) | AESOP_ROOT, CONDUCTOR_ROOT, AESOP_MONITOR_FORCE | monitor/collect-signals.mjs (was :43, now stale: line 43 is at module level, no enclosing symbol), monitor/collect-signals.mjs (was :107, now stale: line 107 is at module level, no enclosing symbol) |
| UI dashboard | `python ui/serve.py`, or `bin/cli.js dash` (spawns python3 ui/serve.py) | the repo checkout | PORT (default 8770), AESOP_ROOT, AESOP_STATE_ROOT | ui/serve.py (was :86, now stale: line 86 is at module level, no enclosing symbol); ui/config.py `reload`, `reload`; bin/CLAUDE.md `Invocation modes` |
| MCP server | the MCP client, over stdio (client config UNVERIFIED) | the repo checkout | AESOP_ROOT, AESOP_STATE_ROOT | mcp/server.mjs (was :80, now stale: line 80 is at module level, no enclosing symbol) |
| Dash TUI | the operator | the repo checkout | AESOP_ROOT | dash/watchdog-gui.sh `HB`, `HB_DIR` |
| Autoflush poller | spawned detached by pre-push | the pushing worktree | AESOP_RECEIPT_SPOOL | hooks/pre-push-policy.sh `check_emit_receipt` |

### 9.4 Known traps
- **T1. Scheduled-task changes go through install-tasks.ps1 only.** Any difference in action, arguments, logon type, or interval re-registers the task (install-tasks.ps1 header, "Divergence (D45)" in daemons/CLAUDE.md). Hand edits to the task are overwritten. A task still pointing at `run-hidden.vbs` is retired and converges on the next install. Run the installer from the runtime checkout after any launcher change (daemons/CLAUDE.md).
- **T2. The launcher is built, not committed.** `daemons/bin/run-hidden.exe` is gitignored and compiled by `install-tasks.ps1` (`Ensure-HiddenLauncher`) with the .NET Framework `csc.exe`. A missing `csc.exe` stops the install before any task is touched. A missing exe makes the task fail closed.
- **T3. The `&&` redirect gap is FIXED.** The derived watchdog command brace-groups the run so the redirect to `state/cron-watchdog.log` covers the whole run (daemons/install-tasks.ps1 `Resolve-DaemonCommands`). A hand-supplied `-WatchdogCommand` chain must group itself, because the installer does not wrap it (daemons/CLAUDE.md, run-hidden paragraph).
- **T4. A heartbeat needs CONDUCTOR_ROOT on the runtime checkout.** backup-fleet.sh writes a heartbeat only when CONDUCTOR_ROOT is set, or when the basename of AESOP_ROOT is exactly `aesop` (daemons/backup-fleet.sh (was :24, now stale: line 24 is at module level, no enclosing symbol)). `~/aesop-daemon-runtime` is not `aesop`. Without an explicit CONDUCTOR_ROOT the watchdog runs, writes no heartbeat, and the monitor reports the heartbeat UNRESOLVED or MISSING (monitor/collect-signals.mjs `resolveWatchdogHeartbeat`, monitor/collect-signals.mjs (was :1097, now stale: line 1097 is at module level, no enclosing symbol)).
- **T5. The runtime's fetch and reset are not in the repo.** The derived command (daemons/install-tasks.ps1 `Resolve-DaemonCommands`) runs only `run-watchdog.sh --once`. STATE.md `ACTIVE: RELEASE 0.10.0 + SELF-CLEAR (rotated 2026-10-15 16:5` records a fetch and `reset --hard origin/main` each tick, which must come from a caller-supplied `-WatchdogCommand`. A bare re-run of the installer re-registers with the derived command, which has no reset, so the reset must be passed again. This is inferred from the code and was not run (see U1).
- **T6. There is no CONDUCTOR_HOME in this repo.** The collector reads CONDUCTOR_ROOT (monitor/collect-signals.mjs `resolveWatchdogHeartbeat`). Without it, it uses the sibling machinery repo only when AESOP_ROOT's basename is `resolveWatchdogHeartbeat`). The value must be the machinery repo directory itself, because the path appended is `state/.watchdog-heartbeat` (`resolveWatchdogHeartbeat` (was :225, now stale: named symbol aesop is not defined in monitor/collect-signals.mjs; line 225 is in resolveWatchdogHeartbeat)). Setting it to the parent of the machinery repo points the collector at the wrong place.
- **T7. The monitor and the watchdog disagree on the monitor heartbeat path.** The monitor writes `$AESOP_ROOT/monitor/.monitor-heartbeat` (collect-signals.mjs (was :1359, now stale: line 1359 is at module level, no enclosing symbol)). The watchdog reads `$CONDUCTOR_ROOT/monitor/.monitor-heartbeat` (run-watchdog.sh (was :489, now stale: line 489 is at module level, no enclosing symbol)). They match only when the two roots coincide (also map 8.2).
- **T8. selfheal.sh is not scheduled.** install-tasks.ps1 registers three tasks and none runs selfheal.sh (also map 8.2). Nothing in the repo restarts a dead watchdog or monitor. daemons/CLAUDE.md describes selfheal as active.
- **T9. daemons/CLAUDE.md is stale on CONDUCTOR_ROOT.** Its Environment variables section says the default is "sibling of AESOP_ROOT". The same file's heartbeat paragraph and backup-fleet.sh (was :24, now stale: line 24 is at module level, no enclosing symbol) say the sibling is derived only for the canonical `aesop` basename. Trust the code.
- **T10. The UI is loopback only.** The bind is hard-coded to 127.0.0.1 (ui/handler.py `run_server`), and Host and Origin are checked against loopback names (`_is_local_origin`). The port comes from the generic env name `PORT` (ui/config.py `PORT`), so setting `PORT` for any other process changes the dashboard port. There is no `/health` route in ui/handler.py, although docs/ADOPTION-CHECKLIST.md `Step 7.1: Launch Dashboard` (was :267, now stale: named symbol PORT is not defined in docs/ADOPTION-CHECKLIST.md; line 267 is in Step 7.1: Launch Dashboard) describes one.
- **T11. state/ and aesop.config.json are gitignored.** `.gitignore` rule `state/` ignores `state/` and `.gitignore` rule `aesop.config.json` ignores `aesop.config.json`. The live store, tracker, receipts and the `state_root` value are never in git, and a fresh clone has no live state. The example's `state_root` (aesop.config.example.json) is only an example.
- **T12. A missing receipt key does not block a push.** hooks/pre-push-policy.sh `ensure_merge_drivers` warns and continues. The gates still run, but a push can land with no receipt. verify-receipt is not in tools/required-checks.json, so a missing receipt is not a merge gate.
- **T13. A recorded emergency override exists.** `AESOP_PUSH_RED=1` lets a red emit_receipt run push, and the receipt is stamped `pushed_red` (hooks/pre-push-policy.sh `check_generated_paths` (was :1915, now stale: named symbol pushed_red is not defined in hooks/pre-push-policy.sh; line 1915 is in check_generated_paths) comment). It is an escape token. LANE-CONTRACT forbids lanes from bypassing gates.
- **T14. An unset AESOP_ROOT makes the monitor read `./monitor`.** collect-signals.mjs (was :43, now stale: line 43 is at module level, no enclosing symbol) defaults AESOP_ROOT to `.`, so its files land relative to the current directory.
- **T15. Native auto-merge is the merge actor.** The AesopMergeQueue task is opt-in and must stay disabled while native auto-merge runs (LANE-CONTRACT s6; daemons/CLAUDE.md invariant 12). No loop may merge with the admin bypass.
- **T16. The orchestrator's dispatch is not a subprocess.** `ClaudeCodeDriver.dispatch_worker` raises `NotImplementedError` outside the harness (driver/claude_code_driver.py `dispatch_worker`, marker text driver/claude_code_driver.py (was :64, now stale: line 64 is at module level, no enclosing symbol)). The only subprocess path is `run_command`, for tests and out-of-harness use (`run_command`).
- **T17. Line numbers in map sections 1-8 are from cb972c56.** They have drifted. Example: daemons/run-watchdog.sh `main` was `main` (was :306, now stale: line 306 is in check_halt, main is defined at line 354) and is now `main` (was :332, now stale: line 332 is in run_box_fence, main is defined at line 354).

### 9.5 UNVERIFIED (not checkable from the repo, or not checked)
- **U1.** The live scheduled-task XML for AesopWatchdogDaemon: its action arguments, whether CONDUCTOR_ROOT is exported, the git fetch and reset chain, and the Hidden, IgnoreNew and 1 h settings. daemons/CLAUDE.md states the settings, but install-tasks.ps1 has no literal for them (grep for ExecutionTimeLimit and IgnoreNew returned nothing).
- **U2.** The contents and HEAD of `~/aesop-daemon-runtime`. `git worktree list` shows it as a detached worktree at ab27a6cc. It was not checked for local changes or for whether origin/main has moved.
- **U3.** `~/.claude/.heartbeats`, named in the task brief. No file in the repo references it. The repo reads `$AESOP_ROOT/monitor/.heartbeats` (monitor/collect-signals.mjs `checkHeartbeats`) and `state/.heartbeats` (dash/watchdog-gui.sh `HB_DIR`). Whether `~/.claude/.heartbeats` exists was not checked.
- **U4.** The per-agent heartbeat writer (brain side).
- **U5.** The refinement monitor's scheduling (`/power` plus self-pacing). Only the agent definition text was read.
- **U6.** The machinery repo checkout and its signal-hub queue, the destination of pr_sweep signals (E13).
- **U7.** The live `state_root` value in aesop.config.json (gitignored).
- **U8.** Which MCP client registers mcp/server.mjs. No `.mcp.json` exists at the repo root.
- **U9.** The live copies of brain hooks named in map 6.2, including the task-event projector that writes `~/.claude/state/task-events.jsonl`.
- **U10.** The writer of `tracker.json`. tools/stateapi_lint.py (was :89, now stale: line 89 is at module level, no enclosing symbol) forbids raw writes outside the facade, but the writer was not traced.
- **U11.** Whether the watchdog, monitor and merge-queue tasks are registered on the live box right now. tools/task_cadence_check.py reads this and was not run.
- **U12.** Whether the halt sentinel is honored per checkout as daemons/CLAUDE.md states. The code path in tools/halt.py was not traced.
