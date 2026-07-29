# Multi-Instance Roadmap

Architectural path from single-box SQLite to multi-instance coordination. Current state (2026-07-29) and planned phases.

## Current Architecture: Single-Box SQLite WAL

**Status**: Production, measured safe

### Design

- **State backend**: SQLite WAL (Write-Ahead Logging)
- **Concurrency model**: SQLite PRAGMA journal_mode=WAL + busy_timeout=5000 + BEGIN IMMEDIATE transactions
- **API layer**: StateAPI facade over EventStore + projections
- **Durability**: Append-only event log; projections (tracker.json, orchestrator status) derived from events
- **Git role**: Read-only export (rendered snapshots, no state writes from git)

### Measured Safety (2026-07-18 Spike)

- **Concurrent writers**: 4 simultaneous processes
- **Throughput**: ~704 events/sec under contention
- **Safety**: 0 lock errors across 800 events per writer (3200 total)
- **OCC support** (Phase 2): Optimistic Concurrency Control via expected_version assertions; fail-closed on mismatch

### Limitations

- **Single host only**: SQLite file-lock is local; no cross-machine readers/writers
- **No distributed leasing**: Multi-instance coordination via Postgres required
- **Scaling ceiling**: ~1000 events/sec before network latency + orchestrator overhead dominates (not yet measured)

---

## Phase 1: Read-Your-Writes + Multi-Instance Reader (2026-08-XX, Planned)

**Goal**: Enable two Aesop instances on separate boxes to coordinate via shared Postgres; primary instance writes, secondary (read-only) follows.

### Scope

1. **Postgres EventStore backend**
   - Swap `state_store.store.py` EventStore class: `SQLiteEventStore` → `PostgresEventStore`
   - Same append/read interface, different implementation
   - StateAPI facade unchanged (no caller churn)

2. **Instance identity + coordination**
   - `state_store.identity.InstanceID(hostname, pid, nonce)` to tag each process
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

## Phase 2: Multi-Writer Coordination (2026-09-XX, Planned)

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

## Phase 3: Cross-Region Federation (2026-10-XX+, Stretch)

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

## Migration Path: SQLite → Postgres → Federated

### Wave-by-Wave Rollout

**Wave 26** (2026-07-23): Postgres Phase 1 branch cut; internal testing on staging cluster
**Wave 27** (2026-07-30): Phase 1 rolled to 10% of production; monitor for connection pool exhaustion, query latency
**Wave 28** (2026-08-06): Phase 1 rolled to 50% of production; if green, 100% by end of week
**Wave 29-30**: Phase 2 (OCC multi-writer); testing on staging, gradual rollout
**Wave 31+**: Phase 3 (federation); contingent on user demand + measured stability

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
- Performance: local file I/O is faster than network round-trip; 704 events/sec is well above current wave throughput (~100 events/sec average)

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
- **Design doc**: `docs/TEAM-STATE.md` (full architecture, migration plan)
- **Config**: `aesop.config.json` → `state_backend` (phase deployment switch)
- **Spike results** (2026-07-18): Postgres connection pool scaling, query latency under contention (internal note; link if shared)

---

## Summary

**Current (SQLite WAL)**: Production-ready, single-box only, 704 events/sec measured, OCC support planned for Phase 2.

**Phase 1 (Postgres, read-only follower)**: Planned 2026-08, enables multi-instance reads; no conflict risk.

**Phase 2 (Multi-writer OCC)**: Planned 2026-09, enables N writers via version-checked appends; retry loop needed.

**Phase 3 (Cross-region federation)**: Stretch goal, contingent on user demand + Phase 1/2 stability.

**No phase commits until measured production data supports the cost-benefit trade-off.**
