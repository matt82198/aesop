# Multi-Instance Roadmap

Architectural path from single-box SQLite to multi-instance coordination.

**Phase 0.5 is shipped.** Multi-instance coordination across separate boxes no
longer requires a network database: it runs on a shared *directory*, with each
instance keeping its own local SQLite. Everything beyond Phase 0.5 — the
Postgres backend, multi-writer OCC over a network store, cross-region
federation — remains a design sketch. **None of Phase 1, 2 or 3 is scheduled**,
and Phase 0.5 removed the reason that made Phase 1 look urgent.

## Current Architecture: Single-Box SQLite WAL

**Status**: Production, measured safe

### Design

- **State backend**: SQLite WAL (Write-Ahead Logging)
- **Concurrency model**: SQLite PRAGMA journal_mode=WAL + busy_timeout=5000 + BEGIN IMMEDIATE transactions
- **API layer**: StateAPI facade over EventStore + projections
- **Durability**: Append-only event log; projections (tracker.json, orchestrator status) derived from events
- **Git role**: Read-only export (rendered snapshots, no state writes from git)

### Measured Safety (2026-07-18 Spike, Single-Box)

- **Concurrent writers**: 4 simultaneous processes on one host
- **Throughput**: ~704 events/sec under contention (single-host micro-benchmark; real-world wave throughput averages ~100 events/sec)
- **Safety**: 0 lock errors across 800 events per writer (3200 total)
- **OCC support**: Optimistic Concurrency Control via expected_version assertions; fail-closed on mismatch
- **Connection pooling**: Thread-local connection reuse (one cached connection per thread per EventStore instance)

### Limitations

- **Single host only, for the event store**: the SQLite file-lock is local, so a
  single `state.db` cannot be shared between machines. This limitation is real
  and permanent (see the WAL-over-network verdict under Phase 0.5) — Phase 0.5
  works *around* it rather than removing it.
- ~~**No distributed leasing**: multi-instance coordination would require a
  network-accessible backend (e.g. Postgres)~~ — **this claim was wrong, and
  Phase 0.5 falsifies it.** Distributed leasing needs a *shared serialisation
  point*, not a *network database*. A shared directory whose filenames are
  unique by construction is such a point: mutual exclusion is decided by a
  deterministic fold over the directory listing, requiring no file locking, no
  exclusive-create, and no server-side transaction. See Phase 0.5 below.
- **Scaling ceiling**: Not yet measured under sustained load; the ~704 ev/s micro-benchmark is well above current real-world throughput (~100 ev/s)

---

## Phase 0.5: Shared-Filesystem Coordination (SHIPPED)

**Status**: Implemented, increments 0-7. Off by default (`multibox.enabled: false`).

**Goal**: 3-5 Aesop instances on separate boxes safely sharing coordination
state — no Postgres, no consensus protocol, no network locking.

### The WAL-over-network verdict (this is what drives the design)

**Do not put `state.db` on the share.** SQLite WAL mode needs a shared-memory
index (`-shm`) that is mmap-backed and only coherent between processes *on the
same host*; SQLite's own documentation states WAL does not work over a network
filesystem. The alternatives were examined and rejected:

- NFSv3 advisory locking (fcntl) is emulated by lockd and historically
  unreliable — a lost lock reply silently yields two writers.
- SMB2/3 byte-range locks are server-mediated and better, but client-side
  attribute and directory caching still breaks SQLite's cache-invalidation
  assumptions.
- `PRAGMA locking_mode=EXCLUSIVE` makes WAL work without `-shm`, but permits
  exactly one connection — which defeats the point.
- Falling back to `journal_mode=DELETE` restores lock-based coherence in theory
  but still rests on the same unreliable advisory locking, and would regress the
  measured single-box concurrency story (~704 ev/s).

So the verdict is **not** "shared storage is unusable"; it is "**a shared
*database* is unusable, a shared *directory* is not**".

### The design

Each instance keeps its **own local SQLite** (WAL, unchanged, local disk — the
standing single-box decision holds verbatim). The shared filesystem carries
**only a claim log**, designed to need no file locking and no exclusive-create:

> Every claim record is written to a filename that is unique by construction:
> `claims/<lamport>-<epoch_ms>-<instance_id>-<uuid4>.json`. No two writers ever
> contend for the same name, so no filesystem atomicity primitive (O_EXCL,
> link(), rename(), flock) is required. Mutual exclusion is decided by a
> **deterministic fold over the directory listing**.

The only property the shared filesystem must supply is that a written+fsynced
file becomes visible in another host's directory listing within a bounded time
D. That is far weaker than POSIX lock semantics — and, crucially, it is
**measurable**.

Because D > 0, a naive "write claim, list, I'm lowest, go" double-grants under
concurrent claims. So a claim waits a **settle window** (`settle_seconds`,
default 5s) before re-listing and folding, and grants only if it is still the
winner for every requested path; otherwise it writes its own tombstone and fails
closed.

### Why this is trustworthy rather than merely plausible

- **The assumption is measured, not assumed.** `tools/multibox_preflight.py`
  measures p99 visibility delay and clock skew on the actual share.
- **The measurement is enforced.** Enabling multibox runs a hard startup gate
  (`tools/multibox_config.py`) that refuses to proceed unless the event-store DB
  is on local storage, the measured p99 delay is below `settle_seconds`, and the
  measured skew is below `max_skew_seconds`. Fail-closed; the refusal names the
  mount options that fix it (NFS: `nfsvers=4.1,actimeo=1,lookupcache=none`;
  SMB: `cache=none`).
- **The mechanism is proven load-bearing, not decorative.**
  `tools/verify_multibox.py` runs in CI and asserts both that 200 seeded rounds
  with delay <= settle produce zero double grants **and** that the same harness
  with delay > settle *does* double-grant. A safety property that cannot fail
  proves nothing; this one can, and does, on demand.
- **Blast radius is bounded.** Worst case is two instances on one file —
  recoverable by the existing merge-train machinery, not state corruption, since
  each instance's event store is local and independent.

### What it does not do

No Postgres. No Raft/Paxos/etcd/ZooKeeper — leases, TTL and a monotonic fencing
generation only. No shared SQLite over a network (actively *prevented* by the
preflight guard). No cross-region federation, and no more than ~5 boxes.

### Where it lives

- Design: `docs/design/MULTIBOX-DESIGN.md` (per-increment status table)
- Config: `aesop.config.json` -> `multibox` block; see `aesop.config.example.json`
- Seam: `tools/multibox_config.py` (parse + hard gate + backend selection)
- Backends: `state_store/claim_backend.py` (local), `state_store/fs_claim_log.py` (shared-FS)
- Proof: `tools/verify_multibox.py`, `tests/multibox_sim.py`

---

## Phase 1: Read-Your-Writes + Multi-Instance Reader (Not Scheduled)

**Goal**: Enable two Aesop instances on separate boxes to coordinate via shared Postgres; primary instance writes, secondary (read-only) follows.

**Not scheduled, and no longer load-bearing.** Phase 0.5 already delivers cross-box coordination, so Phase 1 is now only about a *shared queryable event history*, not about making multibox possible at all. It would be worth doing if a team wanted one durable store for all instances' events; it is not a prerequisite for anything shipped.

### Scope

1. **Postgres EventStore backend**
   - Swap `state_store.store.py` EventStore class: `SQLiteEventStore` → `PostgresEventStore`
   - Same append/read interface, different implementation
   - StateAPI facade unchanged (no caller churn)

2. **Instance identity + coordination**
   - Instance identity is already shipped by Phase 0.5: `state_store/identity.py` exports `get_instance_id()` and `get_identity_with_epoch()` (a persisted id plus a monotonic boot epoch used as a fencing token). There is no `InstanceID` class.
   - Orchestrator reads `InstanceID` on startup
   - Lease-by-append (already implemented): `try_claim(store, "orchestrator_lock", instance_id, ttl=300s)`
   - **Primary**: Claims the lock, drives orchestration, appends events
   - **Secondary**: Reads events, projects state, follows primary

3. **Read-your-writes guarantee**
   - Primary appends to Postgres; secondary reads same Postgres WAL
   - Read stalls until commit (Postgres default READ COMMITTED)
   - No race between primary write and secondary read

### Trade-offs vs. Alternatives

**Alternative A: Distributed transaction log (e.g., etcd, Kafka)**
- Pro: More sophisticated ordering guarantees
- Con: New operational dependency, learning curve, cost
- Chosen: Postgres because it's already managed by most teams; TTL leasing via timestamps is sufficient

**Alternative B: Replicated SQLite (sqlite3-replication)**
- Pro: Minimal API changes
- Con: Replication lag still means secondary cannot coordinate on primary's writes
- Chosen: Postgres because secondary needs ACID read-your-writes for safety

### Implementation Hooks

- **Config**: aesop.config.json gains `state_backend: "postgres"` + connection string
- **Migration**: `tools/migrate_sqlite_to_postgres.py` backfills events from SQLite→Postgres
- **Fallback**: Old code continues to work on SQLite (config default)

---

## Phase 2: Multi-Writer Coordination (Not Scheduled)

**Goal**: Enable N instances to safely coordinate writes to the same stream (e.g., shared audit log, distributed leasing).

### Scope

1. **Optimistic Concurrency Control (OCC) over Postgres**
   - StateAPI already supports `append(..., expected_version=N)`
   - Postgres: implement OCC in SQL (read max version, check assertion, append atomically under SERIALIZABLE isolation)
   - Backward compatible (expected_version=None disables OCC)

2. **Conflict resolution protocol**
   - `ConcurrencyConflict(expected_version, actual_version)` exception on mismatch
   - Caller re-reads, extracts new version, retries
   - Works across network: no special RPC layer needed (SQL exception carries both versions)

3. **Distributed leasing refinement**
   - Current: Single orchestrator tries_claim() and holds lock for duration
   - Multi-writer: Each writer tries_claim(), one succeeds; others back off or queue
   - TTL expiry still enforced at fold time (no clock sync needed)

### Trade-offs

**Alternative A: Consensus (Raft, Paxos)**
- Pro: Guaranteed leader election
- Con: Operational burden, debugging challenges, network partition handling
- Chosen: TTL leasing because orchestrator restarts are acceptable (checkpoint recovery < 1min)

**Alternative B: Exclusive locks (SELECT ... FOR UPDATE)**
- Pro: Database-native, no retry loop
- Con: Serializes all writes; blocks readers during contention
- Chosen: OCC because non-blocking reads improve observability during write stalls

### Implementation Hooks

- **SQL upgrade**: Postgres schema gains version_expected parameter
- **StateAPI**: OCC interface already stable, no caller churn
- **Tests**: `test_state_store_occ_multiwriter.py` validates retries, convergence, no lost writes

---

## Phase 3: Cross-Region Federation (Stretch, Not Scheduled)

**Goal**: Multiple regional Aesop clusters; events replicate asynchronously across regions; local reads always succeed.

### Architectural Sketch

- **Primary region**: Writes to local Postgres, publishes events to message bus (Kafka, Pub/Sub)
- **Secondary regions**: Consume published events, write to regional Postgres replicas
- **Read consistency**: Local read is read-your-writes within region; cross-region reads are eventual
- **Conflict resolution**: Last-write-wins on event timestamp (not absolute consistency)

### Coordination Challenges

- Clock skew across regions → logical timestamps (Lamport, HLC, CRDT vector clocks)
- Partitions → choose between availability (accept writes in any partition) or consistency (reject writes if partition detected)
- Bootstrapping → secondary regions must not write before replication catches up

### Decision: Not yet committed

Phase 3 is contingent on:
1. Measuring Phase 1 + 2 in production (multi-instance stability, failure modes)
2. User feedback (is cross-region failover a real requirement, or is single-region with HA sufficient?)
3. Operational burden (Kafka/Pub/Sub adds complexity; does it justify the benefit?)

---

## Migration Path: SQLite → Network Backend → Federated

### Rollout (Not Scheduled)

No backend migration is currently planned. The wave-by-wave rollout dates originally sketched here (Waves 26-31) have passed without the work being started, because single-box SQLite remains sufficient for current throughput and team size. When a migration is warranted (see decision tree below), a concrete rollout plan will be drafted at that time.

### Rollback Plan

Each phase maintains SQLite compatibility:
- Config `state_backend: "sqlite"` (default in early waves) or `"postgres"`
- `tools/export_to_sqlite.py` snapshot Postgres events back to SQLite if needed
- EventStore interface is identical; caller code needs 0 changes

### Data Integrity Checks

Before each phase transition:
1. `tools/verify_state_consistency.py` — compare SQLite vs. Postgres event counts, checksums
2. `tools/verify_projection_roundtrip.py` — ingest→project→export reproducibility
3. `tools/verify_occ_safety.py` — run 100 concurrent writers (Phase 2 only), check no lost writes

---

## Rationale: Why Not Postgres Today?

**Costs** (Phase 1):
- New infrastructure dependency (Postgres cluster, backups, monitoring, CI fixtures)
- CI test duration increases (each test suite needs a DB; parallel sharding adds latency)
- Operational learning curve (connection pooling, query optimization, replication lag debugging)

**Benefits Today** (Single-box SQLite is sufficient):
- No multi-instance coordination needed yet (orchestrator runs on one box)
- Simplicity: stdlib sqlite3, no external deps, easier debugging
- Performance: local file I/O is faster than network round-trip; ~704 events/sec (single-host micro-benchmark) is well above current wave throughput (~100 events/sec average)

**When to Migrate**:
1. **User demand** for multi-region failover
2. **Measured ceiling**: Waves hit 500+ events/sec sustained (stress-test infrastructure, don't speculate)
3. **Operational readiness**: Team confident in Postgres administration, backup/restore procedures tested

### Decision Tree

```
Does aesop need to coordinate across multiple hosts?
  NO → Stay on SQLite. Revisit in 6 months.
  YES:
    Is one host sufficient for failover (read-only replica, offline recovery)?
      YES → Phase 1 (Postgres reader, SQLite writer). Simpler.
      NO → Phase 2 (Multi-writer OCC). Requires careful retry logic.
    Is global coordination important (writes in any region)?
      YES → Phase 3 (Federation). Highest complexity.
      NO → Phase 1/2 sufficient.
```

---

## References

- **Current implementation**: `state_store/` (EventStore, StateAPI, projections, OCC)
- **Testing**: `tests/test_state_store*.py` (concurrency, round-trip, hardening)
- **Design docs**: `docs/TEAM-STATE.md` (full architecture, migration plan), `docs/design/MULTIBOX-DESIGN.md` (Phase 0.5 coordination design + per-increment status)
- **Config**: `aesop.config.json` → `state_backend` (phase deployment switch)
- **Spike results** (2026-07-18): Postgres connection pool scaling, query latency under contention (internal note; link if shared)

---

## Summary

**Current (SQLite WAL)**: Production-ready, ~704 events/sec measured (single-host micro-benchmark; real-world ~100 ev/s), OCC shipped, thread-local connection pooling, claims-stream compaction. The event store is per-instance and local — that is a deliberate invariant, not a gap.

**Phase 0.5 (shared-filesystem coordination)**: SHIPPED, off by default. 3-5 boxes coordinate through an append-only claim log on a shared directory, with mutual exclusion decided by a deterministic fold. Enabling it requires passing a hard, measured preflight. This is the phase that falsified "no distributed leasing without Postgres".

**Phase 1 (network backend, read-only follower)**: Design sketch only, **not scheduled**. Would give all instances one shared queryable event history; it is no longer what makes multi-instance possible.

**Phase 2 (Multi-writer OCC)**: Design sketch only, not scheduled. OCC interface already stable on SQLite; a network backend would implement the same semantics in SQL.

**Phase 3 (Cross-region federation)**: Stretch goal, contingent on user demand + Phase 1/2 stability.

**No Postgres-phase commits until measured production data supports the cost-benefit trade-off. A local-per-instance SQLite event store plus a shared claim log is sufficient for current workloads and team sizes.**
