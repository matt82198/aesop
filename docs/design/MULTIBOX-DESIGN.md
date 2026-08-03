# Multibox Coordination Design — Lease-by-Append Over Shared Filesystems

**Status**: Phase 0.5 MVP (Increments 0-7, distributed-ledger team-state synchronization)

**Problem**: How do 3-5 Aesop instances on separate machines safely share coordination state without Postgres, without consensus protocols, and without network locking?

**Solution**: Distributed leasing via shared filesystem (NFS/SMB) using deterministic fold-decided mutual exclusion and lease-by-append protocol — the same principle that powers the single-box SQLite-based coordination, lifted to operate over a shared directory.

---

## 1. Architecture Overview

Each Aesop instance **keeps its own local SQLite database** (WAL mode, single-machine guarantees intact). The **shared filesystem carries only a claim log** — a directory of immutable claim records, one file per lease request. Mutual exclusion is decided deterministically by folding the directory listing, with no file locks, no exclusive-create primitives, and no reliance on network advisory locking.

### Core Invariants

| Invariant | Origin | Load-Bearing For |
|---|---|---|
| Separator split-brain rejected (`dir/f` vs `dir\f`) | Historical #47c967b | Case-sensitive tiers (Linux) + case-insensitive tiers (Windows) coexist safely |
| Case split-brain rejected (e.g., `README.md` vs `README.MD`) | Historical #47c967b | Heterogeneous box teams (Windows + Linux) over shared FS |
| Check-and-insert atomic; no window between them | Historical #6311288b | Prevent TOCTOU: read lease state, then claim in one shot |
| Losing claimant retracts its own request | RS5 design | Fail-closed: if you lose the race, you withdraw immediately |
| Expired holder is reclaimable at fold time | RS3 design | Stale leases auto-expire; no zombie holds |
| Any read/append failure → no grant (fail-closed) | Throughout | Transient FS errors never grant a false positive |
| **[NEW]** Canonicalization is host-independent | Inc 1 | Same file path canonicalizes to the same string on Windows, Linux, macOS |
| **[NEW]** No double-grant under bounded visibility delay | Inc 6 | Settled window + fold-decided epoch ordering prevents concurrent grants |

---

## 2. The WAL-over-Network Verdict

**Decision: Do not put `state.db` on the shared filesystem.**

### Why WAL Mode Breaks Over Network FS

- SQLite's WAL mode uses a shared-memory index (`-shm`) backed by memory-mapped I/O, which achieves coherence only between processes **on the same host**. Network filesystems cannot provide this guarantee.
- NFSv3 advisory locking (fcntl) is emulated by the lockd daemon and is historically unreliable; a lost lock reply silently permits two writers.
- SMB2/3 byte-range locks are server-mediated (better reliability) but client-side attribute/directory caching still breaks SQLite's cache-invalidation assumptions.
- Exclusive locking (`PRAGMA locking_mode=EXCLUSIVE`) makes WAL work without `-shm` but permits only one connection — defeating multibox.
- Fallback to rollback journals (`journal_mode=DELETE`) restores lock-based coherence in theory but depends on the same unreliable advisory locking and regresses measured single-box concurrency.

### Design Around It

Each instance uses **local SQLite with WAL unchanged** (the single-box decision stands verbatim). The shared filesystem carries only the **claim log** — a lightweight record stream designed to require **no file locking and no exclusive-create**:

> Every claim record is written to a filename unique by construction:
>
> ```
> claims/<lamport>-<epoch_ms>-<instance_id>-<uuid4>.json
> ```
>
> No two writers ever contend for the same name, so no FS-level atomicity primitive (O_EXCL, link(), rename()) is required. Mutual exclusion is decided **by a deterministic fold over the directory listing** — the same approach already used to decide leases in the single-box coordination layer.

The only property the shared FS must supply:

> *A file that has been written and fsynced becomes visible in another host's directory listing within a bounded time D.*

This is **far weaker** than POSIX lock semantics and is **measurable** via preflight probes (Increment 0).

---

## 3. Lease-by-Append + Settle Window Protocol

Because directory visibility delay D > 0, a naive "write claim, list dir, I'm the lowest, grant" protocol double-grants under concurrent claims:

```
t0: Instance A writes claim_A
t0: Instance B writes claim_B
t1 < t0+D: Instance A lists dir (sees only claim_A), folds, grants ← WRONG!
t1 < t0+D: Instance B lists dir (sees only claim_B), folds, grants ← WRONG!
t2 > t0+D: Both instances are now running, both think they hold the lock
```

### Safe Protocol

1. **t0: Write `claim_requested` record**
   - Unique filename by sort key: `(lamport_counter, epoch_ms, instance_id, uuid4)`
   - fsync the file, then fsync the parent directory (POSIX; no-op on Windows)

2. **t0 → t0+settle: Sleep**
   - `settle = D_p99 * safety_factor` (config default 5s)
   - Must exceed measured directory-cache TTL on the specific FS
   - This is the *only* clock-dependent step; ordering itself needs no clock sync

3. **t0+settle: Revalidate and Refold**
   - Force-revalidate the directory (clear local cache, read FS directly)
   - Re-list directory, fold the full record set
   - Timeout any records with `age(now, epoch_ms) > ttl + max_skew`

4. **Decision**
   - Grant **if and only if** this instance's record is the winner for every requested path **and** no competing record with an earlier sort key exists
   - Otherwise write a `claim_released` tombstone for your own record and fail closed

### Sort Key Ordering

Sort key `(lamport_counter, epoch_ms, instance_id, uuid4)` is a **deterministic total order**:
- **Lamport counter**: causality-aware, clock-skew immune
- **Epoch ms**: wall-clock timestamp for TTL expirations (allows skew margin)
- **Instance ID**: tiebreaker on same host
- **UUID4**: final tiebreaker, globally unique

This ordering is identical on all machines; two instances folding the same directory listing will always compute the same winner, even if their clocks differ.

### Why This is Lease-by-Append (Unchanged)

The core principle from the single-box `coordination.py` carries over:
- **Append target** moved from SQLite's `claims` table to a shared directory
- **Begin-immediate locking** replaced by a deterministic settle window
- **Fold-decided mutual exclusion** applies to both
- **Every invariant** (lowest wins, TTL expiry at fold time, phantom-holder retract on loss, fail-closed on error) transfers identically

---

## 4. Handling Cross-Box Concerns

### Heterogeneous Paths and Case Sensitivity (Increment 1)

**Problem**: Two instances — one Windows, one Linux — claim the same file under different names:
- Windows canonicalizes `README.MD` via `os.path.normcase()` → `readme.md`
- Linux does not → keeps `README.MD`
- Both instances think they have *different* claims and grant the lock on the same file

**Solution**: `canonical_claim_path()` in `state_store/paths.py`

```python
def canonical_claim_path(path, repo_root=None, case_policy="platform|insensitive|sensitive"):
    """
    Repo-relative path with repo_root, forward slashes always, NFC-normalized Unicode.
    case_policy:
      - "platform" (tier L): byte-identical to os.path.normcase (Windows: case-fold, Linux: no-op)
      - "insensitive" (tier S): always case-fold (over-collision trades throughput for safety)
      - "sensitive" (future): never case-fold (Linux semantics)
    """
```

- **Tier L (single box)** defaults `"platform"` → zero behavior change
- **Tier S (shared FS)** forces `"insensitive"` → both Windows and Linux canonicalize to the same string

### Clock Skew and TTL (Increment 4b)

**Problem**: Two instances have wall clocks that differ by Δt; a leased resource expires in T seconds on the true timeline but Instance A thinks it expires in T+Δt.

**Solution**: Order by **lamport + uuid** (skew-immune), fold by **wall clock with skew margin**

```python
def fold_fs_claims(records, now, max_skew):
    """
    Lease is live until: epoch_ms + ttl + max_skew
    max_skew measured by preflight (Increment 0); exceeding it fails startup.
    Skew only lengthens leases (stalls throughput), never shortens (cannot double-grant).
    """
```

---

## 5. Implementation Increments

All increments:
- Are independently shippable (green on `npm run test:all`)
- File-scoped (no side effects outside their domain)
- Are inert behind `multibox.enabled=false` (default) until Increment 7

### Increment 0 — Multibox Preflight Probe + Network-FS Guard

**Files**: `tools/multibox_preflight.py` (new), `tests/test_multibox_preflight.py` (new)

**Functionality**:
- `detect_fs_kind(path)` — POSIX: parse `/proc/mounts` for network FS markers (nfs, cifs, smbfs, etc.); Windows: UNC prefix or remote drive type via ctypes. Returns `local|network|unknown`; unknown treated as network (fail-closed).
- `assert_local_sqlite(db_path)` — raises if event-store DB is on a network FS. **Rules-to-code**: WAL-over-SMB failure mode becomes an enforced precondition.
- `measure_visibility_delay(shared_dir, samples=N)` — probe files + force-revalidated listing to measure directory-cache delay; reports p50/p95/p99. Single-process mode for CI.
- `measure_clock_skew(shared_dir)` — measure wall-clock skew across instances.
- CLI: `--check --shared-dir DIR --db PATH [--json]` → exits 0 (clean), 1 (findings), 2 (error).

**Tests**: /proc/mounts fixtures, ctypes Windows shim, tmpdir probes (delay ~0, hermetic).

**Flag**: Advisory-only; hard gate in Increment 7.

**Status**: Designed, not yet built.

---

### Increment 1 — Canonical Repo-Path Normalization (PR #684)

**Status**: In development (PR #684 open).

**Files**: `state_store/paths.py` (new), edits to `state_store/lease_claims.py`, `tests/test_state_store_paths.py` (new)

**Functionality**:
- `canonical_claim_path(path, repo_root=None, case_policy="platform"|"insensitive"|"sensitive")` → str
  - Repo-relative if `repo_root` given
  - Forward slashes always
  - `../` and `./ ` collapsed
  - NFC-normalized Unicode
  - case-folded iff `case_policy` says so

- `case_policy` sourced from CONFIG (not `os.name`):
  - Tier L defaults to `"platform"` → byte-identical to today, zero shipped-path change
  - Tier S forces `"insensitive"` (over-collision: spurious conflict costs throughput; missed collision costs correctness)

- `lease_claims._normalize_path` becomes a thin alias; all 18 existing tests pass untouched.

**Tests**:
- Four historical #47c967b regressions through the new function
- NEW heterogeneity guard — identical output with `os.name` monkeypatched to `'nt'` and `'posix'`
- Unicode NFC/NFD equivalence (macOS SMB clients store NFD)
- Trailing/mixed-separator idempotence

**Flag**: None; tier-L default preserves behavior exactly.

**Status**: Designed, not yet built.

---

### Increment 2 — ClaimBackend Seam + Atomic Claim on Dispatch Path (PR #685)

**Status**: In development (PR #685 open).

**Files**: `state_store/claim_backend.py` (new: protocol + `LocalLeaseBackend` adapter), edits to `tools/multi_dispatch.py`, `tests/test_claim_backend.py` (new), `tests/test_multi_dispatch_claim.py` (new)

**Functionality**:
- Protocol: `claim(paths, instance_id, ttl) → lease_id | raise ClaimConflict`; `renew`; `release`; `holder(paths) → instance_id | None`
- Mirrors `LeaseStore` semantics; atomicity inherited, not rewritten
- `get_backend(config)` returns `LocalLeaseBackend` unless `multibox.enabled`
- **Fixes defect (a)**: `multi_dispatch` stops read-then-append against `instance_projection`, calls `backend.claim()` — one atomic operation — when flag on. Flag off keeps legacy path byte-for-byte.
- `instance_projection.claim_files()` docstring: **advisory only** (projection/dashboard feed, not mutual exclusion)

**Tests**:
- Adapter contract suite runnable against ANY backend (reused verbatim by Increment 4a — the TDD lever)
- `multi_dispatch` conflict test (exit 1, NO claim record on conflict)
- Flag-off regression test

**Flag**: `multibox.enabled` selects atomic path; default off.

**Status**: Designed, not yet built.

---

### Increment 3 — Durable Instance Identity + Epoch/Fencing Heartbeat (PR #686)

**Status**: In development (PR #686 open).

**Files**: `state_store/identity.py` (new), edits to `tools/instance_manager.py`, `tests/test_state_store_identity.py` (new)

**Functionality**:
- Persisted identity: `$AESOP_STATE_ROOT/instance-id` (config override `multibox.instance_id`)
- In-memory: `<stable_id>:<epoch>`, where `epoch` is a persisted monotonic boot counter
- Restart bumps epoch; enables **release_own_stale(prior_epochs)** on startup to reclaim immediately
- Epoch is the fencing token consumed by Increment 5

- `instance_manager` heartbeat stays **ONE-SHOT** on daemon cadence. No `--loop` mode (watcher-linter enforces this)

**Tests**:
- ID stability across two processes sharing a state root
- Epoch monotonicity across simulated restarts
- Corrupt/missing/unwritable ID file falls back to ephemeral WITHOUT raising (fail-open on identity; bad ID must not brick a solo box, but mutual exclusion fails closed downstream)
- `AESOP_STATE_ROOT` respected

**Flag**: Persisted identity always (strict improvement); epoch-fencing consumed under flag only.

**Status**: Designed, not yet built.

---

### Increment 4a — FsClaimLog: Shared-Filesystem Lease-by-Append

**Files**: `state_store/fs_claim_log.py` (new), `tests/test_fs_claim_log.py` (new)

**Functionality**:
- Record: `{v, kind: claim_requested|claim_released|heartbeat, paths[], instance_id, epoch, lamport, epoch_ms, ttl}` — JSON, one file per record, immutable
- `fold_fs_claims(records, now, max_skew) → {canonical_path: instance_id}` — **pure function** (entire TDD surface: no FS, no clock, no sleeps). Mirrors `coordination.fold_claims`: TTL-expiry-at-fold + tombstones
- Implements the Increment 2 `ClaimBackend` protocol → contract suite runs unmodified
- `claim()` = write request → sleep settle → revalidate-list → fold → grant or self-tombstone
- `renew()` = new record extending deadline (append-only, never mutate)
- `release()` = tombstone

- Corrupt/truncated record → treated as **live claim by unknown holder** for mtime + TTL (fail-closed to "someone might hold this")

**Tests**:
- Fold unit table: lowest-key wins; tombstone releases; expired reclaimable; legacy record without TTL never expires; corrupt blocks
- `FsClaimLog` on tmpdir with injectable clock and `settle=0`
- Full Increment 2 contract suite
- Four historical #47c967b split-brain regressions replayed with `case_policy="insensitive"`

**Flag**: Reachable only when `multibox.transport == "shared-fs"`.

**Status**: Designed, not yet built.

---

### Increment 4b — Durability, Clock Skew, GC

**Files**: Edits to `state_store/fs_claim_log.py`, `tests/test_fs_claim_log_durability.py` (new)

**Functionality**:
- **Durability**: Temp name in same dir, flush + `os.fsync(file)`, `os.replace()` to final unique name, then **fsync parent dir** on POSIX (else the entry may not be peer-visible even after data is durable). Windows: `os.replace()` + `FlushFileBuffers` via ctypes; dir-fsync no-op.
- **Clock skew**: Ordering is lamport/uuid (skew-immune); TTL uses wall clock → needs a bound. Each record carries writer `epoch_ms`; fold treats lease live until `epoch_ms + ttl + max_skew`. Skew only lengthens leases, never shortens (cannot double-grant).
- **GC**: `compact(retain_seconds)` deletes tombstoned + long-expired records; only safe for records older than `ttl + max_skew + settle`; never deletes a record it cannot prove expired.

**Tests**:
- fsync call-order via monkeypatched `os` shim
- Skew matrix: ahead/behind/at/past bound (no early expiry)
- Truncated-JSON fixture fail-closed
- GC idempotence + never-delete-live (including skewed-clock case)

**Status**: Designed, not yet built.

---

### Increment 5 — Stale-Instance Detection + Stale-Primary Failover

**Files**: `state_store/failover.py` (new), edits to `state_store/instance_projection.py`, `tests/test_failover.py` (new)

**Functionality**:
- **Heartbeats tier S**: Instances record atomically replaced (not appended). Tier L keeps event append. `detect_stale_instances` gains transport-aware source; threshold semantics + 300s default unchanged.
- **Primary election**: `elect_primary(backend, now) → (instance_id, generation)`. Primary holds reserved resource `orchestrator_lock` via the same claim protocol. On TTL expiry, any live instance may take over. Takeover bumps a generation counter in the claim record.
- **Fencing**: Every coordination write carries `(instance_id, epoch, generation)`. A write with `generation` below the fold's current generation is **REJECTED**. Split-brain guard at primary level: a partitioned old primary that returns cannot resume driving. No consensus — shared log is arbiter, monotonic generation is the fence.
- **Stale-claim reclamation**: Already fold_claims TTL behavior; Increment 5 makes staleness observable in fleet dashboards.

**Tests**:
- Pure `elect_primary` over synthetic record lists: sole instance elected; lapsed primary → exactly one successor; returning old primary FENCED (gen N rejected after fold shows N+1); 3-way simultaneous takeover → one winner under deterministic sort key; generation never decreases

**Flag**: `multibox.enabled`; tier L untouched.

**Status**: Designed, not yet built.

---

### Increment 6 — Simulated-Multibox CI Harness

**Files**: `tests/multibox_sim.py` (new), `tests/test_multibox_integration.py` (new), `tools/verify_multibox.py` (new CI proof)

**Functionality**:

Real NFS/SMB differs from local tmpdir in exactly three observable ways — delayed, reordered, partial visibility — all simulatable at the directory-listing boundary:

- `DelayedShareView(root, delay, jitter, seed)` — per-instance view whose `listdir` hides records younger than `delay` (seeded jitter). Writes pass through; only reads lag.
- `PartitionedShareView` — one instance's records invisible to peers for a window, then appear (SMB reconnect model).
- `TornWriteShareView` — truncated record injection.
- Instances = separate subprocess runs of a small driver (per `tools/subprocess_guard.py` G6: explicit cwd, list argv, no shell).

**Assertions**:
1. **No double-grant** across 200 seeded runs with delay ≤ settle — the load-bearing property
2. **Falsifiability** — with delay > settle the harness DOES observe a double-grant (settle window proven load-bearing, not ceremony)
3. **Liveness** — killed instance's claims reclaimed within `ttl + max_skew + settle`
4. **Failover** — exactly one successor; revived old primary fenced
5. **Convergence** — all folds agree at quiescence
6. **Real-FS smoke** on undecorated tmpdir with `settle=0.05`

Seeded, deterministic, no network; runs under `npm run test:py`; `verify_multibox.py` = exit-0/1 CI proof.

**Status**: Designed, not yet built.

---

### Increment 7 — Flag Flip, Config, Docs, Wiring

**Files**: `aesop.config.example.json` (config block), `state_store/CLAUDE.md` (updated), `tools/CLAUDE.md` (updated), `tests/CLAUDE.md` (updated), `docs/TEAM-STATE.md` (updated), `mcp/server.mjs`, `tests/mcp-multibox.test.mjs`

**Functionality**:
- **Config block**: 
  ```json
  {
    "multibox": {
      "enabled": false,
      "transport": "local",
      "shared_dir": null,
      "settle_seconds": 5.0,
      "max_skew_seconds": 2.0,
      "lease_ttl_seconds": 300,
      "heartbeat_seconds": 30,
      "case_policy": "insensitive",
      "instance_id": null
    }
  }
  ```

- **Hard preflight gate**: Enabling multibox runs `multibox_preflight --check` at startup; refuses unless:
  1. DB is on local storage
  2. Measured p99 visibility delay < `settle_seconds`
  3. Measured clock skew < `max_skew_seconds`
  Fail-closed. Converts the central assumption (bounded visibility delay) into an enforced, measured precondition.

- **Docs**: Update `docs/TEAM-STATE.md` to reflect Phase 0.5 (shipped), correct "no distributed leasing without Postgres" claim which Phase 0.5 falsifies, and restate Phase 1+ (Postgres) as explicitly unscheduled.

- **MCP**: `fleet_multibox_summary` reads the active backend.

**Tests**:
- Config parse defaults/precedence (env > config > default)
- Preflight refusal on each of the three conditions
- MCP tests for shared-fs backend
- All metadata gates (CLAUDE.md drift, test suite count) green

**Status**: Designed, not yet built.

---

## 6. Sequencing and File Ownership

```
    Inc 0 --+
    Inc 1 --+--> Inc 2 --> Inc 4a --> Inc 4b --> Inc 6 --> Inc 7
    Inc 3 --+                   +----> Inc 5 -----+
```

- **Inc 0, 1, 3** fully parallel on code files BUT `state_store/CLAUDE.md` contended by Inc 1+3 (and `tools/CLAUDE.md` by Inc 0) — sequence the doc-touchers or assign a single doc writer.
- **Inc 2** needs Inc 1 (canonical paths)
- **Inc 4a** needs Inc 2 (contract suite is its harness)
- **Inc 5** needs Inc 3 (epoch) + Inc 4a (records)
- **Inc 6** needs Inc 4b + 5
- **Inc 7** last

---

## 7. Riskiest Technical Assumption

**That cross-box directory-visibility delay on NFS/SMB is bounded and measurable, so a fixed settle window makes a fold-decided claim safe.**

If a client caches a listing unboundedly (aggressive `acdirmax`, unbroken SMB directory lease, stale-after-reconnect), two instances each fold a listing where the other's request is invisible → both grant.

### Mitigations

1. **Measure, don't assume** (Increment 0) + **refuse to enable past bounds** (Increment 7)
2. **Prove the mechanism load-bearing** (Increment 6 assertion 2 — falsifiable, not decorative)
3. **Document required mount options** in the preflight failure message:
   - **NFS**: `nfsvers=4.1,actimeo=1,lookupcache=none`
   - **SMB**: `cache=none` / `directoryCacheLifetime=0`
4. **Bounded blast radius**: Worst case = two instances on one file — recoverable via merge-train machinery, not state corruption (event store is local per-instance)

### Secondary: Clock Skew

Clock skew beyond `max_skew` is folded INTO lease deadline (skew only lengthens); preflight refuses above bound. NTP management is out of scope.

---

## 8. Explicit Non-Goals

- No Postgres/backend swap (Phase 1+ stays unscheduled)
- No Raft/Paxos/etcd/ZooKeeper — leases + TTL + monotonic fencing generation only
- No shared SQLite over network (actively prevented by Increment 0 guard)
- No cross-region/federation / >5-box claims
- No new runtime dependencies (stdlib only)

---

## 9. Critical Path Files

- `state_store/lease_claims.py` — atomic LeaseStore (single-box foundation)
- `state_store/coordination.py` — event-sourced fold_claims (Increment 1-7 inherit this semantics)
- `state_store/instance_projection.py` — heartbeat + stale detection
- `tools/multi_dispatch.py` — dispatch path (Increment 2 atomic claim here)
- `tests/test_lease_claims.py` — regression suite (18 tests, guard split-brain + TOCTOU)

---

## 10. Known Defects (Identified During Grounding; Fixed by Increments)

### Defect (a): `multi_dispatch` TOCTOU Race

**Origin**: Same race class as historical #6311288b, reintroduced one layer up.

**Current behavior**: `check_conflict()` (read) and `claim_files()` (append) are separate operations with no lock between them. `instance_projection.claim_files()` performs **no mutual-exclusion check at all** — it is a pure advisory append that always returns True.

**Fixed by**: **Increment 2** — atomic `backend.claim()` replaces the read-then-append pattern.

---

### Defect (b): `_normalize_path` Host-Dependent → Heterogeneous Split-Brain

**Origin**: Third route to historical #47c967b (split-brain via path canonicalization).

**Current behavior**: 
- Windows: `lease_claims._normalize_path()` applies `os.path.normcase()` (case-fold)
- Linux: `lease_claims._normalize_path()` does not case-fold
- Two boxes with same file under different names → both claim it successfully

**Fixed by**: **Increment 1** — repo-canonical `canonical_claim_path()` with config-driven case policy, independent of `os.name`.

---

## References

- **SQLite WAL documentation**: https://www.sqlite.org/wal.html (Network filesystem limitations, `-shm` coherency)
- **POSIX advisory locking**: POSIX.1-2017 section 6.2.1 (fcntl locks, NFS limitations)
- **SMB/CIFS caching**: MS-SMB2 section 3.3.5 (directory attribute caching)
- **Lamport timestamps**: Lamport, L. (1978). "Time, Clocks, and the Ordering of Events in a Distributed System"
- **Lease-based coordination**: Pattern developed in Aesop single-box layer (state_store/coordination.py), adapted for shared FS

---

**Generated with Claude Code** — Aesop Docs Lane 2026-08-02
