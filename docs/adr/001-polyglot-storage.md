# ADR-001: Polyglot storage over a single relational database

**Status**: Accepted

**Context**: 100k+ vehicles emitting ~1 event/sec produces ~100k events/sec
sustained, with 3x bursts. A single Postgres primary cannot sustain this
write rate once tables reach billions of rows (index write amplification,
connection/lock contention), while billing, RBAC and audit data need strict
ACID guarantees that a purely eventually-consistent store can't offer.

**Decision**: Split storage by consistency/volume requirement:
- Postgres for low-volume, strongly-consistent OLTP data.
- TimescaleDB (Postgres extension) for high-volume time-series telemetry,
  partitioned by time + VIN hash, with compression and retention policies.
- MongoDB for schema-flexible raw event landing and OEM field mappings.
- pgvector for embeddings powering the agentic-AI retrieval layer.

**Consequences**:
- (+) Each store is used for what it's good at; no single component has to
  compromise between OLTP consistency and IoT-scale write throughput.
- (+) TimescaleDB reuses the team's Postgres/SQL skills instead of adopting
  a wholly separate time-series engine.
- (−) Operational surface area increases (4 stores instead of 1); mitigated
  by keeping cross-store joins to a minimum (the API composes results in
  application code, not via cross-database queries).
- (−) Dual-write risk between Mongo and TimescaleDB in the consumer (see
  `docs/ARCHITECTURE.md` "Known MVP trade-offs").
