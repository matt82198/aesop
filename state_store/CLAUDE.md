# state_store/ — event-sourced state layer (SQLite WAL, projections, git-as-export)

## Universal rules (every domain)
- Feature branch only, never main; every push gated by `python tools/secret_scan.py --staged` exit 0.
- Tests never pollute cwd or global git config; temp dirs only; dummy secrets are runtime-concatenated, never literal.
- In worktrees use ABSOLUTE paths under the worktree for every write.
- Domain docs stay minimal-but-complete; update this file in the same PR as code it describes.

## Purpose & Status
Durable substrate moving aesop's coordination/state off git (which cannot scale to a team due to single-writer control files, hot-file merge conflicts, no transactions/concurrency). State becomes an append-only event log with per-stream versioning; current state is a projection; git is demoted to a rendered, diffable **export**. Status (2026-07-14): additive prototype. The live `ui/` tracker path is UNCHANGED. Full architecture & migration design: `docs/TEAM-STATE.md`.

## API Surface (state_store.api.StateAPI)
**Facade over EventStore + projections; backend swap seam (currently SQLite WAL).**
- `append(stream, event_type, payload, actor="system", expected_version=None) → int`: Append one event; return its new per-stream version. **OCC support (Phase 2)**: if `expected_version` is provided, the append succeeds ONLY if the stream's current max version equals `expected_version`; otherwise raises `ConcurrencyConflict` WITHOUT writing (fail-closed, atomic). Enables writers to serialize reads: "I read version N; I will append only if it's still N when I try."
- `get(stream) → list`: Return all events in ``stream`` ascending by version.
- `get_since(stream, after_version) → list`: Return events with version > `after_version`. Enables snapshot + tail-replay for any stream.
- `close()`: Release the underlying thread-local cached connection. Safe to call multiple times; the next operation lazily reopens.
- `project(view) → dict`: Fold the same-named stream through its projector into current state. Registered views: "tracker" (via `project_tracker`).
- **Exceptions:** `ConcurrencyConflict(expected_version, actual_version)` — raised by `append()` when OCC check fails; carries both versions so caller can rebase and retry.

## Concurrency Model & Measured Safety
**Multi-writer safe via SQLite WAL + atomicity:**
- `PRAGMA journal_mode=WAL` — many readers; serialized writers via write lock.
- `PRAGMA busy_timeout=5000` — retry for 5s on contention before erroring.
- `BEGIN IMMEDIATE` in `append()` — atomic read-max-version-then-insert; two writers never collide or duplicate a version.
- **Measured safety (2026-07-18 spike, single-box):** 4 concurrent writers, 800 events each (3200 total), 0 lock errors, ~704 events/sec throughput. This is a single-host micro-benchmark; real-world wave throughput averages ~100 events/sec.

**Optimistic Concurrency Control (Phase 2, 2026-07-21):**
- `append(..., expected_version=N)` — writer asserts "stream is at version N"; append succeeds only if true.
- **Atomicity:** The version check and append both happen under `BEGIN IMMEDIATE`, so no TOCTOU window.
- **Failure mode:** On version mismatch, raises `ConcurrencyConflict(expected, actual)` WITHOUT writing any event (fail-closed).
- **Retry protocol:** Caller re-reads the stream, extracts the new version from `ConcurrencyConflict.actual_version` or re-count events, and retries `append(..., expected_version=new_version)`.
- **Backward compatible:** `expected_version=None` (default) disables OCC; old code remains unchanged and unaffected.
- **Use case:** Multiple orchestrators coordinating on multi-instance state (e.g., distributed tracing, multi-writer audit log) can use OCC to prevent lost updates when racing to extend the same stream.

## Module Layout
- **read_api.py** — `ReadAPI` facade over state surfaces; read-only. Delegates to existing parsers: tracker snapshot, orchestrator status, heartbeat freshness via `tools/common`, ledger rows via `tools/fleet_ledger`. Never forks logic.
- **write_api.py** — `WriteAPI(state_dir)` facade for tracker mutations (state consolidation write path). Exposes tracker CRUD and markdown operations: `tracker_append_item(item_dict)`, `tracker_update_status(item_id, new_status, note)`, `tracker_update_item(item_id, update_data)` (general-purpose patch), `tracker_archive_item(item_id)` (soft-delete), and `rebuild_projection()`. 
  - **Markdown write path (Inc 1+2)**: `write_state_md(content)`, `append_buildlog(line)`, `ensure_buildlog_exists(header=...)` (idempotent create, never overwrites; `header` lets migrated legacy writers keep their historical header byte-for-byte), `rebuild_state_md(content, force)`. File written atomically first, event (`state_md_written`/`buildlog_entry`) appended only on success. Migrated callers (Inc 2): `tools/buildlog.py`, `tools/ensure_state.py`, `tools/eod_sweep.py`.
  - **tracker_* methods**: All append events atomically AND render tracker.json projection via `materialize_tracker()` under a file lock (tempfile + os.replace). Fail-closed: event append failure blocks projection write.
  - **OCC (Optimistic Concurrency Control)**: Detects concurrent modification before atomic write: if on-disk hash differs from both start-of-operation hash and computed projection hash, raises `WriteConflict` (fail-closed). Corrupt JSON on disk also raises `WriteConflict` (fail-closed, not fail-open). Baseline hash captured at operation START (before event append) so the check window covers the entire operation.
  - **ID collision detection**: `tracker_append_item` with explicit id rejects duplicates (raises `ValueError` before appending) to prevent duplicate events for the same logical item.
  - **Self-healing recovery**: `rebuild_projection()` force-renders from the event store, bypassing OCC, to recover orphaned events (event in store, missing from projection).
- **store.py** — `EventStore(db_path)`: append-only SQLite log with thread-local connection pooling. `append(stream, type, payload, actor, expected_version=None)` returns new version or raises `ConcurrencyConflict` on OCC mismatch; `read(stream)` / `read_since(stream, after_version)` / `read_all()` return event rows; `close()` releases the cached connection. Corrupt JSON payloads are skipped with stderr log; snapshot read/write for tail-replay optimization.
- **__init__.py** — Public exports: `EventStore`, `StateAPI`, `ConcurrencyConflict`, `project_tracker`, `export_tracker`, `ingest_tracker_json`.
- **projections.py** — `project_tracker(events)`: folds `item_created` / `item_updated` / `item_archived` into the full `tracker.json` shape, preserving first-seen order.
- **api.py** — `StateAPI(db_path)`: the backend swap seam (currently SQLite WAL). Callers use this only; backend implementation hidden. Passes through OCC, connection lifecycle (`close()`), and tail-read (`get_since()`) support transparently.
- **export.py** — `export_tracker(api, out_path)`: render the projection back to a git-tracked JSON snapshot (indent=2, ascii-escaped to match the live file).
- **ingest.py** — `ingest_tracker_json(api, path)`: backfill one `item_created` per existing item; validates event structure at boundary.
- **identity.py** — Multi-instance identity: `get_instance_id()` returns ephemeral form (hostname:pid:nonce); `get_identity_with_epoch(state_root)` returns durable (stable_id, epoch) persisted to $AESOP_STATE_ROOT/instance-id. Epoch is monotonic boot counter, fencing token for multibox. **Fail-closed on corruption**: corrupt existing file raises `IdentityCorruptionError`. **Fail-open on creation**: fresh box (no prior file) or unwritable directory on new create falls back to ephemeral (allows solo mode). Distinction preserves monotonicity: stale crashed instance cannot claim epoch=1 after restart.
- **coordination.py** — Lease-by-append claims for multi-writer coordination: `try_claim(store, resource, instance_id, ttl)` / `release` / `current_holder` / `fold_claims` / `compact_claims` via fail-closed event appends. Accepts a StateAPI (`.get`) OR a raw EventStore (`.read`) — RS3-W fix: try_claim previously required `.get()` so every EventStore claim fail-closed to False (dead gate). TTL expiry is ENFORCED at fold time: a claim past `ts + ttl` is ignored/reclaimable (crashed holders cannot hold forever; legacy events without ts/ttl never expire). RS5 F2: a try_claim whose post-append read/fold raises retracts its own claim_requested (best-effort) before returning False — a failed claim attempt never strands a phantom holder for a full TTL. Claims-stream compaction: `compact_claims(store)` snapshots the active claim events for O(tail) reads instead of O(total). Prevents concurrent orchestrators from colliding.
- **lease_claims.py** — `LeaseStore(db_path)`: atomic file-path leasing for multi-instance coordination on a single box using a dedicated SQLite leases table (not event-sourced, for atomicity + TTL efficiency). API: `claim(paths, instance_id, ttl_seconds)→lease_id` (atomically claims set of paths; raises `LeaseConflict` if any held by another active instance), `renew(lease_id, instance_id, ttl_seconds)` (extends TTL; fails if not holder), `release(lease_id, instance_id)` (releases lease; frees paths for others). Deterministic time injection (clock parameter) enables testing without sleeps. Expiry-steal-on-claim: leases past deadline are reclaimable immediately at claim time (no background thread). All operations fail-closed: exceptions propagate.

## Invariants
- **Append-only**: never mutate/delete events; state changes are new events.
- **Per-stream version is 1-based and gapless** (enforced atomically).
- **git as export, not source**: nothing here reads git for state.
- **Round-trip fidelity**: ingest → project → export reproduces the same items (tested against the real `state/tracker.json`).

## CI Isolation & Concurrency Gotcha
**SQLite tests deadlock under parallel CI shards** (false positive; no code defect). When running the unittest suite under parallel CI shards, multiple test files may contend on filesystem-level WAL locks. **Solution:** Use `_retry_on_db_lock(func, max_retries=3, delay=0.1)` wrapper for DB initialization and appends; apply exponential backoff. Real fix = per-shard DB isolation (future work). On CI re-run, the shard passes.

## Test Commands
Run from repo root:
- `python -m unittest tests.test_state_store` — Core API, concurrency, round-trip tests.
- `python -m unittest tests.test_state_store_occ` — OCC multi-process tests (Phase 2): exactly-one-succeeds, no-write-on-conflict, retry-convergence, backward-compat.
- `python -m unittest tests.test_state_store_concurrency` — Phase 1 multi-process coordination tests (claims, leases).
- `python -m unittest tests.test_state_store_hardening` — Corrupt event handling, input validation.
- `python -m unittest tests.test_state_store_snapshots` — Snapshot read/write and tail-replay.
- `npm run test:py` — All Python test suites (includes state_store).

## Agent Lifecycle Events (Wave-29)

**New event types** (additive, appended by UI collectors on agent phase changes):
- `agent_dispatched` — payload `{agent_id, timestamp}` — marks agent dispatch start
- `agent_working` — payload `{agent_id, timestamp}` — marks work in progress (thinking/tool-use)
- `agent_done` — payload `{agent_id, timestamp}` — marks completion
- `agent_stalled` — payload `{agent_id, timestamp}` — marks stall/error detected

**Projection**: `project_agent_lifecycle(events)` folds these into per-agent lifecycle state with transition history (state + timestamp). Enables Activity view to show agents entering/leaving states over time.

## Multi-instance coordination (MVP — this increment)

**New module: instance_projection.py** — Event-sourced instance lifecycle tracking:
  - Event types: `instance_registered`, `instance_heartbeat`, `instance_failed`, `file_claim_requested`, `file_claim_released`
  - Projection tables: instances (id, hostname, pid, status, heartbeats), file_claims (instance+paths)
  - Core API: `register_instance()`, `heartbeat()`, `claim_files()`, `release_files()`, `list_active_instances()`, `detect_stale_instances()`, `get_claimed_files()`, `get_all_claimed_files()`
  - Stale detection: configurable threshold (default 300s); crashed instances' claims become reclaimable after TTL
  - Fail-closed: all operations fail gracefully, returning False or empty collections on error

**Instance coordination is a prerequisite** for multi-machine orchestration (team-scale single-project development). Used by `tools/instance_manager.py` (CLI) and `tools/multi_dispatch.py` (dispatch guard).

## Multibox Increment 1 (2026-08-02) — canonical repo-path normalization

**Inc 1 fixes defect (b): `_normalize_path()` was host-platform-dependent, causing heterogeneous-box split-brain.** Two instances (Windows + Linux) would canonicalize the same path differently and both claim it (47c967b P0 recurrence). **New module: paths.py** — `canonical_claim_path(path, repo_root=None, case_policy="platform"|"insensitive"|"sensitive")` -> repo-relative (if repo_root given), forward-slash-only, .. collapsed, NFC-normalized Unicode, case-folded per policy (not os.name). **lease_claims._normalize_path now thin alias** with case_policy="platform" for backward compatibility (all 18 existing tests pass untouched). **Tests**: 22 new in test_state_store_paths.py (four 47c967b regressions through canonical form, heterogeneity guard with monkeypatched os.name, Unicode NFC/NFD equivalence, separator idempotence) + 2 new heterogeneity regressions in test_lease_claims.py. **Invariant**: same path normalizes identically whether running on Windows or Linux (when case_policy specified); default "platform" preserves exact byte-for-byte behavior.

## Multibox Increment 2 (2026-08-02) — atomic dispatch claims seam (TOCTOU fix)

**Inc 2 fixes defect (a): multi_dispatch TOCTOU (time-of-check to time-of-use) race.** check_conflict() + claim_files() were separate ops with no lock; concurrent claims on same path both succeeded. **New module: claim_backend.py** — `ClaimBackend` protocol (claim/renew/release/holder) + `LocalLeaseBackend` adapter over LeaseStore (atomicity inherited via BEGIN IMMEDIATE). **get_backend(config)** returns LocalLeaseBackend if multibox.enabled=True, else None (advisory path). **tools/multi_dispatch.py updated**: calls backend.claim() atomically when flag on (exit 1 on ClaimConflict, no record written), keeps legacy check_conflict+claim_files byte-for-byte when flag off. **instance_projection.claim_files() docstring**: marked advisory-only (projection/dashboard feed, not mutual exclusion). **Tests**: 10 new in test_multi_dispatch_claim.py (TOCTOU concurrent-claims race, conflict-no-record-written, flag-off legacy path) + 22-test contract suite in test_claim_backend.py (reusable by Inc 4a FsClaimLog). **Invariant**: exactly one concurrent claimant succeeds; loser gets ClaimConflict with fail-closed record.



## Increment 1 (state consolidation, 2026-07-30) — canonical materializer + state_rebuild

**Inc 1 consolidates all view rendering to ONE place:**
- **materialize.py** — canonical pure projector functions for all views (tracker.json, orchestrator-status.json, STATE.md, ledger). Each view is `(projection_dict) -> bytes` (deterministic, idempotent, testable). `materialize_all(api, state_dir)` renders all views atomically under WriteAPI's file lock.
- **tools/state_rebuild.py** — CLI `--all | --view NAME | --check` for rebuilding and verifying materialized views. `--check` is the CI drift gate (renders to memory, diffs disk, exit 1 on drift).
- **ui/collectors.py (refactored)** — Deleted independent `save_tracker()` + `.tracker-render-dirty` self-heal. All tracker CRUD now routes through WriteAPI, which calls `materialize_tracker()` under the same lock. `load_tracker()` still reads the materialized tracker.json as a cache; mutations keep it in sync.
- **export.py (thin alias)** — DEPRECATED; now wraps `materialize_tracker()` for backward compatibility.
- **DOC DRIFT FIX**: state_store/CLAUDE.md and ui/CLAUDE.md both claimed `tracker.json` is "git-committed" — it is not; corrected to "materialized view, git-ignored, rebuildable" (R2 rule).

**Outcome**: Single canonical render path (materialize + WriteAPI), OCC-protected CRUD, deterministic views. Baseline delta: ~0-2 keys (this increment buys safety, not ratchet points).

## Increment 2 (state consolidation, 2026-07-30) — `orchestrator_status` into the store

**Inc 2 moves orchestrator_status into the event store:**
- **projections.py** — `project_orchestrator_status(events)` folds `phase_changed`, `activity_changed`, `status_cleared`, and historical `meta`/`phase_set` events into byte-compatible orchestrator-status.json shape: `{id, role, activity, phase, updated_at}`.
- **api.py** — registered `project_orchestrator_status` in `_PROJECTORS` dict (already exported by `__init__.py`).
- **write_api.py** — `set_orchestrator_status(activity, phase, id, role)` and `clear_orchestrator_status()` append events first, then materialize the view atomically. Fail-closed: event append failure blocks projection write. Uses `_project_orchestrator_status()` and `_render_orchestrator_status_atomic()` helpers.
- **read_api.py** — `read_orchestrator_status()` reads from projection first (if DB present), falls back to materialized file if DB absent. Preserves fail-open-to-None and future-timestamp-is-stale (`age < -120`) semantics.
- **materialize.py** — `materialize_orchestrator_status(projection)` renders projection to bytes (indent=2, newline-terminated, deterministic).
- **tools/orchestrator_status.py** — CLI delegates to WriteAPI (set/clear commands); stdout strings remain byte-identical for shell tests (`[OK] Status updated: ...` and `[OK] Status cleared`).
- **tools/healthcheck.py** — routed `_check_orchestrator_status` → `_check_orchestrator_status_api` to use ReadAPI (projection-first with file fallback).

**Event types** (appended to `orchestrator_status` stream):
- `phase_changed`: payload `{phase, timestamp, actor}` (new, Inc 2)
- `activity_changed`: payload `{activity, timestamp, actor}` (new, Inc 2)
- `status_cleared`: payload `{}` (new, Inc 2)
- `meta` / `phase_set`: payload `{phase}` (historical, from reconcile.py --resolve; folded forward)

**Byte-compatibility**: The projection renders byte-identical to the current orchestrator-status.json shape. Views written by `materialize_orchestrator_status()` are deterministic and idempotent.

**Baseline movement**: 2 keys retired (43 → 41). Materialization views (status-json, tracker-json, state-md-write) are legitimately appended to baseline (they write derived caches from projections).

## Increment 3 (durable identity + epoch fencing, 2026-08-02) — multibox coordination baseline

**Core API**: `get_identity_with_epoch(state_root)` persists durable identity (stable_id, epoch) to $AESOP_STATE_ROOT/instance-id as JSON. Epoch is a monotonic boot counter; simulated restart increments it. **Distinction (fail-closed on corruption)**: Fresh box (no prior file) creates epoch=1; corrupt existing file raises `IdentityCorruptionError` (fail-closed) to preserve epoch monotonicity for Inc 4+ fencing. **Fail-open exception**: only OSError on FRESH box creation falls back to ephemeral (allows solo mode on unwritable state root). Corrupt file when prior id existed is a hard error; caller must handle recovery. Tests: fresh-box creation, corrupt-with-prior-file raises, epoch monotonicity across restarts, AESOP_STATE_ROOT respected, backward-compat shape. `release_own_stale(state_root, stable_id, prior_epochs)` API placeholder for Inc 5 coordinated reclaim. **Export**: `IdentityCorruptionError` added to state_store.__init__ public API.

## Multibox Increment 4a (2026-08-02) - FsClaimLog: shared-filesystem lease-by-append

**New module: fs_claim_log.py** carries the claim log (and ONLY the claim log) onto a shared FS; `state.db` stays on local disk because SQLite WAL needs an mmap-backed `-shm` index coherent only within one host. Records are **immutable JSON, one file per event**, named `<lamport>-<epoch_ms>-<instance_id>-<uuid4>.json` - **unique by construction, so the backend uses NO filesystem mutual-exclusion primitive at all** (no advisory file locks, no exclusive-create, no hardlink tricks); `instance_id` is sanitized in the filename only (':' is illegal on Windows), the true value is always read from the record body. Kinds: `claim_requested` | `claim_released` (tombstone) | `heartbeat` (renew). **`fold_fs_claims(records, now, max_skew, detail=False)` is a PURE function over a list of dicts** - no FS, no clock, no sleeps - and is the entire correctness surface; it mirrors `coordination.fold_claims`: lowest sort key **(lamport, epoch_ms, instance_id, uuid)** wins each path (deterministic total order, no clock sync needed for *ordering*), TTL enforced **at fold time** so a crashed holder is reclaimable, a tombstone releases the lease it names and a heartbeat cannot resurrect it, a heartbeat extends the deadline **without changing the sort key**, legacy records with no `ttl` never expire, and **`max_skew` is only ever ADDED** to `epoch_ms/1000 + ttl` so a skewed peer clock stalls throughput but can never double-grant. **`FsClaimLog` implements the Inc 2 `ClaimBackend` protocol**, so `tests/test_claim_backend.py::ClaimBackendContractTests` runs against it unmodified (imported and re-parametrized, not copied); `claim()` runs the **settle-window protocol** - write request + fsync, sleep `settle_seconds` (must exceed the measured p99 cross-box directory-visibility delay; Inc 0 measures, Inc 7 gates), force a fresh listing, fold, grant iff *this exact record* wins **every** requested path, else append our OWN tombstone and raise `ClaimConflict` (RS5 F2 retract: a losing request never strands a phantom holder for a full TTL). `renew()` APPENDS a heartbeat and never mutates a record; `release()` appends a tombstone; injectable `clock`/`sleep` make every path testable without real sleeps; all keys go through `canonical_claim_path(case_policy="insensitive")` (over-colliding costs throughput, under-colliding costs correctness). **Corrupt records fail CLOSED, unlike `store.py`**: a truncated/unparseable/empty record might be somebody's live claim on the path we are about to take, so it folds into a live claim by an unknown holder covering **every** path (reserved key `FS_UNKNOWN_PATH` = `"*"`, holder `FS_UNKNOWN_HOLDER` = `"<unknown>"`) until `mtime + default_ttl + max_skew`; an unreadable directory folds the same way, because an I/O error must never read as "nothing is held". **Tests** (67 in `tests/test_fs_claim_log.py`): 26 pure-fold table cases (sort-key precedence at each of the four levels, tombstones, expiry/reclaim, ttl-less legacy, skew lengthens-never-shortens, heartbeat extension, corrupt blocks then expires, order-independence), 25 tmpdir cases with an injected clock and settle=0 (record shape, filename uniqueness, lamport monotonicity incl. adopting a peer's high-water mark, self-tombstone on loss, settle window observed, source-level assertion that no FS locking primitive is used), the 11 imported Inc 2 contract tests, and 5 replaying the 47c967b split-brain regressions (separator, case, renew-on-expired, renew-on-released) through FsClaimLog. **Flag**: reachable only when `multibox.transport == "shared-fs"` (Inc 7). **Deferred to Inc 4b**: temp-name + `os.replace` + parent-dir fsync durability, the measured skew matrix, and `compact(retain_seconds)` GC.

## Next (cutover, follow-up — NOT this increment)
**Phase 1 (middle)**: Tracker dual-read (StateAPI for CRUD, export job keeps `tracker.json` rendered).
**Phase 2 (cutover complete)**: Flip all readers to API; remove git fallback.
**Phase 3 (optional, contingent on team scale needs)**: Backend swap behind StateAPI (e.g. Postgres). Not scheduled; single-box SQLite is sufficient for current throughput (~100 ev/s real-world vs ~704 ev/s measured ceiling). See `docs/MULTI-INSTANCE-ROADMAP.md` for the decision tree.

Map of all domains: /CLAUDE.md
