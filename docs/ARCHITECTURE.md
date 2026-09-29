# Architecture — Connected Vehicle Predictive Maintenance

## Problem framed
"Which vehicles are likely to break down in the next 7 days, and why?" —
for fleet managers running a mixed ICE/HYBRID/EV fleet.

## High-level flow

```
[Simulator] --(Kafka: vehicle.telemetry.raw, keyed by VIN)--> [Consumer]
                                                                  |
                                     +----------------------------+----------------------------+
                                     |                                                          |
                              [MongoDB: raw_events]                                   [TimescaleDB: telemetry]
                              (audit / replay / schema drift)                    (hypertable, 5-min continuous aggregate)
                                                                                              |
                                                                                    [Feature pipeline]
                                                                                              |
                                                                                 [GradientBoosting model]
                                                                                              |
                                                                                     [Postgres: alert]
                                                                                              |
                                                                                        [FastAPI]
                                                                                        /        \
                                                                                 [Web dashboard]  [3rd-party API clients]
```

## Why polyglot storage

| Store | Data | Why |
|---|---|---|
| Postgres (OLTP core) | Fleet, Driver, Vehicle, Trip, Alert, Subscription, AppUser, AuditLog | Needs ACID: billing, RBAC, audit trail must never be inconsistent. Low write volume (thousands/day, not thousands/sec). |
| TimescaleDB (hypertable) | Raw + 5-min rolled-up telemetry | High write volume (100k+ events/sec target). Time+VIN partitioning avoids hot chunks. Compression + retention policy gives a hot/warm/cold lifecycle without a separate system. |
| MongoDB | Raw landing-zone events, per-OEM field-mapping documents, free-text service notes | Schema varies by OEM and changes over time (SDV trend) — forcing it into a rigid relational schema would mean constant migrations. |
| pgvector (colocated with Postgres for MVP) | Embeddings of alerts/service notes | Powers the agentic-AI "find similar issues" retrieval without standing up a separate vector DB for an MVP-scale corpus. |
| Redis | Rate-limit counters, session/token cache | Sub-millisecond, ephemeral — wrong fit for a durable store. |

This directly answers "why a single SQL DB fails here" (Section 4.1 of the
brief): write amplification and lock contention on a single Postgres primary
would collapse well before 100k events/sec; splitting the OLTP core from the
time-series firehose removes that bottleneck while keeping strict consistency
where it actually matters (money, access, audit).

## CAP / PACELC choices

- **Billing, RBAC, audit (Postgres)** — CP. A missed billing write is worse
  than a slow one.
- **Raw telemetry (TimescaleDB)** — AP under partition; PACELC: else-latency
  favored over strict cross-replica consistency. A telemetry point delayed
  or (rarely) dropped during a network partition doesn't block the fleet
  from operating; a stuck OLTP write would.
- **Alerts** — written to Postgres (needs to be durable and queryable with
  strong consistency for ack/audit workflows) but *derived* from an
  eventually-consistent telemetry stream — consistent with an event-sourced
  read model pattern.

## Data lifecycle (hot/warm/cold)

| Tier | Retention | Storage | Access pattern |
|---|---|---|---|
| Hot | 7 days, uncompressed | TimescaleDB | Dashboards, real-time alerting |
| Warm | 8–91 days, compressed (Timescale native compression) | TimescaleDB | Ad-hoc investigation, recent trend charts |
| Cold | 91 days–2 years | `telemetry_5min` continuous aggregate only; raw dropped by retention policy | Model retraining, compliance reporting |
| Archive (optional, not in MVP) | 2+ years | Parquet on object storage (S3/GCS), queried via DuckDB/Spark | Long-horizon analytics |

Estimated cost driver: at ~1 KB/event and 100k vehicles emitting once/sec,
raw volume is ~8.6 TB/day. Compression (Timescale native, typically 10–20x
on time-series columns) brings the 91-day warm tier to roughly 40–80 TB
rather than 780 TB — this is why raw data does not live forever.

## Reliability patterns implemented / designed

- **Idempotent ingestion**: `(vin, seq, ts)` unique constraint + upsert
  (`ON CONFLICT DO NOTHING`) absorbs the simulator's intentional ~0.5%
  duplicate rate and real-world OEM retry behavior.
- **Partitioning**: Kafka topic keyed by VIN (ordering per vehicle);
  TimescaleDB space-partitioned by VIN hash across 16 partitions to spread
  ingest load and avoid a single hot chunk during shift-start bursts.
- **Back-pressure**: consumer commits Kafka offsets only after a batch is
  durably written (see `ingestion/consumer.py::flush`), so a slow downstream
  store naturally throttles consumption instead of losing data.
- **Dead-letter queue**: schema-invalid events are routed to
  `vehicle.telemetry.deadletter` instead of silently corrupting storage or
  crashing the consumer.
- **Circuit breaker / graceful degradation** (documented, not yet wired in
  MVP code): the API's `_fetch_latest_features` should short-circuit to a
  cached last-known risk score if TimescaleDB is slow/unavailable, rather
  than blocking the request.

## Known MVP trade-offs (explicitly out of scope for this pass)

- Dual-write to Mongo + TimescaleDB in the consumer is not exactly-once
  across both stores; production would use a single source of truth (e.g.
  Kafka-backed outbox/CDC) and derive both projections from it.
- The rate limiter is in-process (per API replica); production needs a
  shared Redis-backed token bucket (noted in `api/auth.py`).
- Terraform/Helm/STRIDE threat model, k6 load tests, and 80%+ coverage are
  called out in the hackathon deliverables but not built in this MVP pass —
  see `README.md` "What's next" for the prioritized list.
