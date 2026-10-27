# Gate reference

The 23 `check_*` functions in `hooks/pre-push-policy.sh`, in the order a push meets them. For what the layers mean
and what they cannot do, read [Gates and hooks](../concepts/gates-and-hooks.md). To install the hook, read
[Install the pre-push hook](../guides/install-hook.md).

## How to recount

```bash
grep -cE '^check_[a-z_]+\(\)' hooks/pre-push-policy.sh
```

Output on this tree:

```
23
```
<!-- grep -cE '^check_[a-z_]+\(\)' hooks/pre-push-policy.sh -> 23 -->

The call order inside `main()` comes from this command, filtered to the lines of `main()`
(`hooks/pre-push-policy.sh:3830-4062`):

```bash
grep -nE '^ *(if !|check_|.*&& ! check_)' hooks/pre-push-policy.sh | grep -E '^(38[3-9][0-9]|39[0-9][0-9]|40[0-5][0-9]|406[0-2]):' | grep -E 'check_[a-z_]+'
```

It prints 22 calls, `check_aesop_root_pin` at line 3856 through `check_emit_receipt` at line 4058. <!-- 22 lines printed by the command above -->
The 23rd function, `check_escape_rate`, is not called from the chain; it runs from an EXIT trap set at
`hooks/pre-push-policy.sh:3863` through `_escape_rate_on_exit` (`hooks/pre-push-policy.sh:1970`). If the number of
functions on your tree is not 23, trust the command, not this page.

## Conventions

- **Def** is the line where the function is defined. **Call** is the line in `main()` that runs it.
- **Blocks** is `yes` when a failure exits the hook with status 1 and writes a `push_blocked` record to the audit
  log under the listed reason. The reason string appears within five lines after the call.
- Every gate script is run from the base copy of `tools/` on the trunk, not from the pushed branch
  (`hooks/pre-push-policy.sh:260-265`). Where a gate's tool is missing but `tools/` exists, the push blocks;
  where `tools/` is absent altogether the gate is skipped and logged (`hooks/pre-push-policy.sh:179-198`;
  `hooks/CLAUDE.md:62-65`).
- A push made only to crash-backup refs skips exactly two gates, `metrics` and `test_coverage`, with a printed
  note and an audit event; every other gate still runs (`hooks/pre-push-policy.sh:1277,1299-1305,3975,4019`).

## The chain

| # | Gate | Def | Call | Tool | Blocks | Audit reason on block | What it checks |
|---|---|---|---|---|---|---|---|
| 1 | `aesop_root_pin` | 51 | 3856 | none (shell) | yes | `aesop_root_pin_mismatch` | `AESOP_ROOT`, when set, names the same directory as the repository being pushed |
| 2 | `branch_policy` | 591 | 3904 | none (shell) | yes | `push_to_protected_branch` | No ref tuple targets `refs/heads/main` or `refs/heads/master`, deletes included; tag-only pushes pass |
| 3 | `gate_tamper` | 357 | 3924 | none (shell) | no | event `gate_changed_in_push` | Prints a banner when a push edits a gate file; always returns 0 |
| 4 | `secret_scan` | 796 | 3926 | `tools/secret_scan.py --range` | yes | `secret_scan_failure` | Credential patterns in the pushed range, one range per ref tuple; an absent scanner also blocks (`secret_scan_unavailable`) |
| 5 | `import_resolution` | 932 | 3935 | `tools/import_resolution_check.py --range` | yes | `import_check_failure` | Pushed Python files import modules that resolve |
| 6 | `conflict_markers` | 1019 | 3942 | `tools/conflict_marker_check.py` | yes | `conflict_marker_check_failure` | No literal unresolved conflict markers in added lines |
| 7 | `symbol_survival` | 1102 | 3949 | `tools/pr_symbol_survival_check.py` | yes | `symbol_survival_failure` | A merge commit did not drop a top-level symbol the branch added |
| 8 | `tracker_guard` | 880 | 3955 | `tools/tracker_guard.py --check` | yes | `tracker_guard_failure` | Items in terminal lanes do not re-enter active lanes |
| 9 | `claudemd_sync` | 1180 | 3961 | `tools/claudemd_sync_gate.py --check` | yes | `claudemd_sync_failure` | A domain with code changes also changed its documentation surface |
| 10 | `gen_tool_index` | 1225 | 3967 | `tools/gen_tool_index.py --check` | yes | `gen_tool_index_failure` | `tools/INDEX.md` matches each tool's `INDEX:` line |
| 11 | `metrics` | 1308 | 3975 | `tools/metrics_gate.py` | yes | `metrics_gate_failure` | Hard numeric claims in markdown carry source verification; skipped for crash-backup-only pushes |
| 12 | `claudemd_headroom` | 1640 | 3981 | `tools/claudemd_lint.py --headroom` | yes | `claudemd_headroom_failure` | The merged result of this branch and trunk keeps CLAUDE.md files under their line cap |
| 13 | `test_suite_count` | 1377 | 3987 | `tools/verify_test_suite_count.py --check` | yes | `test_suite_count_drift` | The CI shard matrix covers every tracked `tests/test_*.py` file |
| 14 | `pyflakes_ratchet` | 1431 | 3993 | `tools/pyflakes_gate.py` | yes | `pyflakes_ratchet_failure` | No new or stale pyflakes findings against the committed baseline; `AESOP_PYFLAKES_SKIP=1` skips and is logged |
| 15 | `inprocess_http_timeout` | 1509 | 4000 | `tools/inprocess_http_timeout_lint.py` | yes | `inprocess_http_timeout_failure` | No client timeout under 30 s aimed at an in-process server in tests |
| 16 | `subprocess_guard_ratchet` | 1572 | 4007 | `tools/subprocess_guard.py --check` | yes | `subprocess_guard_ratchet_failure` | No new subprocess anti-pattern in tests above the baseline |
| 17 | `encoding_lint` | 1978 | 4013 | `tools/encoding_lint.py --check` | yes | `encoding_lint_failure` | `open()` and text-mode subprocess calls set an explicit encoding |
| 18 | `test_coverage` | 2026 | 4019 | `tools/verify_test_coverage.py --check` | yes | `test_coverage_failure` | Every on-disk test file is run by some CI job; skipped for crash-backup-only pushes |
| 19 | `generated_paths` | 2189 | 4025 | `tools/generated_paths.py --check` | yes | `generated_path_hand_edit` | The push does not hand-edit a machine-generated path |
| 20 | `generated_regen` | 2289 | 4035 | `tools/generated_push_gate.py --range` | yes | `generated_artifact_stale` | Registered generated artifacts in the pushed commit are byte-identical to their generator's output |
| 21 | `linux_shape` | 2071 | 4041 | `tools/linux_shape_check.py --range` | yes | `linux_shape_check_failure` | Shell, hook, workflow and Node-test changes are proven on Linux; on a Windows host without WSL, a push touching those paths blocks |
| 22 | `emit_receipt` | 2363 | 4058 | `tools/emit_receipt.py` | yes | see below | Runs the test matrix, signs and posts a receipt; blocks on a red or unreadable result |
| 23 | `escape_rate` | 1941 | EXIT trap (3863) | none (shell) | no | event `escape_rate_ALERT` | Counts skip, opt-out and override events in the last hour and prints an alert above a threshold |

The "What it checks" column is taken from each tool's `INDEX:` line, at `tools/secret_scan.py:4`, `tools/import_resolution_check.py:4`,
`tools/conflict_marker_check.py:4`, `tools/pr_symbol_survival_check.py:4`, `tools/tracker_guard.py:3`,
`tools/claudemd_sync_gate.py:3`, `tools/gen_tool_index.py:4`, `tools/metrics_gate.py:4`,
`tools/verify_test_suite_count.py:5`, `tools/pyflakes_gate.py:3`, `tools/inprocess_http_timeout_lint.py:3`,
`tools/subprocess_guard.py:4`, `tools/claudemd_lint.py:3`, `tools/encoding_lint.py:3`,
`tools/verify_test_coverage.py:4`, `tools/generated_paths.py:3`, `tools/generated_push_gate.py:3`,
`tools/linux_shape_check.py:4` and `tools/emit_receipt.py:3`.

Order note: the chain is not the order of definition in the file, and not the order of the older list in
`docs/archive/HOOK-INSTALL.md`. Position in the table above is the position in `main()`.

## Per-gate notes

**1. aesop_root_pin.** Compares the environment variable to the repository toplevel by device and inode, not by
string, and it blocks if the pinned path does not exist (`hooks/pre-push-policy.sh:60-64`). Outside an aesop
checkout it passes (`hooks/pre-push-policy.sh:56-59`).

**2. branch_policy.** Judges the remote ref in each `<local ref> <local sha> <remote ref> <remote sha>` tuple, never
the checked-out branch (`hooks/pre-push-policy.sh:592-615`). The hook as a whole refuses an interactive terminal
before any gate runs (`hooks/pre-push-policy.sh:3844-3849`).

**3. gate_tamper.** Looks for pushed changes to `tools/*gate*.py`, `tools/secret_scan.py`, `tools/gen_*.py`,
`tools/generated_*.py`, `hooks/pre-push-policy.sh` and `.github/workflows/`, and prints `GATE-CHANGED`
(`hooks/pre-push-policy.sh:357-377`). It does not block, because gates are judged by the base copy anyway.

**4. secret_scan.** The scanner must exist or the push is blocked, not allowed
(`hooks/pre-push-policy.sh:847-853`).

**14. pyflakes_ratchet.** Exit 1 (new or stale findings) and exit 2 (tool error) both block
(`hooks/pre-push-policy.sh:1442-1444`).

**21. linux_shape.** When the checker reports `NOTICE: WSL unavailable`, a push that touches covered paths blocks
(`linux_shape_wsl_absent_covered_blocked`), a push that touches none proceeds with the notice, and a coverage probe
that cannot decide blocks (`linux_shape_coverage_probe_failed`) (`hooks/pre-push-policy.sh:2136-2156`).

**22. emit_receipt.** Runs last, and only after gates 1 to 21 have passed. An empty ref list on stdin (nothing to
push) exits 0 before this gate (`hooks/pre-push-policy.sh:4047-4052`). Reasons it can log:

| Audit reason | Meaning | Line |
|---|---|---|
| `receipt_emit_base_tool_missing` | `emit_receipt.py` absent from the base copy | 2423 |
| `receipt_emit_no_python` | no usable interpreter | 2441 |
| `receipt_emit_no_gh` | `gh` missing or not working | 2447 |
| `receipt_emit_no_key_material` | no signing key (`AESOP_RECEIPT_KEY`, `AESOP_RECEIPT_HMAC_SECRET`, or `${AESOP_HOME:-$HOME}/.aesop/receipt_key.pem`) | 2454 |
| `receipt_emit_timed_out` | matrix exceeded `AESOP_RECEIPT_TIMEOUT` (default 900 s) | 2553 |
| `receipt_red_blocked` | a required receipt part is red | 2574 |
| `receipt_emit_unreadable_blocked` | emitter exited with a status other than 0, 3 or 4 | 2584 |

Two non-blocking outcomes are on purpose: an `origin` that is not on github.com emits nothing
(`receipt_emit_non_github_origin`, line 2432), and an `origin` with no branches yet emits nothing
(`receipt_emit_empty_remote_bootstrap`, line 2463). `AESOP_PUSH_RED=1` lets a red head through, logs
`receipt_push_red_override`, and stamps the receipt `pushed_red: true` (lines 2478 and 2557-2565).

The receipt's own status is a measurement period; its hosted check is not required (`docs/archive/RECEIPT-GATE.md:3-4`).

## Audit log

`log_block` appends `{"ts","repo","event":"push_blocked","reason","user"}` plus a sequence number and the
SHA-256 of the previous line, with `GENESIS` for the first (`hooks/pre-push-policy.sh:440,1887-1902`). The
verifier is `bash hooks/pre-push-policy.sh --verify-audit-log [path]` (`hooks/pre-push-policy.sh:482,3836-3841`).
Writes take a directory lock with stale-lock recovery (`hooks/pre-push-policy.sh:394-414`).

## Environment variables that change gate behavior

| Variable | Effect | Cite |
|---|---|---|
| `AESOP_ROOT` | Fallback root outside an aesop checkout; a mismatch inside one blocks | `hooks/pre-push-policy.sh:27-33,3856` |
| `AESOP_PYFLAKES_SKIP=1` | Skips gate 14, logged as `pyflakes_ratchet_skipped_env_opt_out` | `hooks/pre-push-policy.sh:1446-1449` |
| `AESOP_HEADROOM_BASE_REF` | Base ref for gate 12 (default `origin/main`) | `hooks/CLAUDE.md:31` |
| `AESOP_RECEIPT_TIMEOUT` | Bound on the receipt matrix, default 900 s | `hooks/pre-push-policy.sh:2473` |
| `AESOP_RECEIPT_EMIT=0` | Inert; only logs `receipt_emit_optout_ignored` | `hooks/pre-push-policy.sh:2397-2399` |
| `AESOP_PUSH_RED=1` | Recorded override that lets a red head push | `hooks/pre-push-policy.sh:2478` |
| `AESOP_ESCAPE_ALERT_THRESHOLD` | Can only lower the default alert threshold, never raise it | `hooks/pre-push-policy.sh:1918-1931` |

## Tests

`bash hooks/pre-push-policy.sh --test` runs the hook's own behavioral cases. On this tree: `PASSED: 32`, `FAILED: 0`
(`hooks/pre-push-policy.sh:2611`). <!-- bash hooks/pre-push-policy.sh --test -> PASSED: 32, FAILED: 0 --> Case 31 checks that a
gate whose base copy is missing blocks, and case 32 that the receipt gate runs the base copy of its emitter
(`hooks/pre-push-policy.sh:3721,3769`). The Claude Code hooks have co-located tests; see
[Gates and hooks](../concepts/gates-and-hooks.md).

## Server-side counterpart

None of the above is a required check. Required checks are declared in `tools/required-checks.json` and enforced by
repository settings on GitHub; a fork gets none of them (`tools/required-checks.json:6-12`). See
[Install the pre-push hook](../guides/install-hook.md), section 4.
