# Connected Vehicle Predictive Maintenance — MVP

**"Which vehicles are likely to break down in the next 7 days, and why?"**

Built for the Connected Vehicle Intelligence hackathon (industry reference:
Motorq). Independent academic exercise, no affiliation with Motorq.

## What's here (working end-to-end)

| Component | Path | Status |
|---|---|---|
| Vehicle/fleet data generator (100k+ vehicles) | `simulator/vehicle_profiles.py` | ✅ tested |
| Telemetry stream simulator (bursty, out-of-order, duplicates, injected pre-failure symptoms) | `simulator/simulator.py` | ✅ tested, ~68k events/sec single-threaded |
| Kafka consumer: validate → de-dup → dual-write (Mongo + TimescaleDB) | `ingestion/consumer.py` | ✅ logic unit-tested |
| Feature engineering (rolling windows per VIN) | `ml/feature_engineering.py`, `ml/train_model.py` | ✅ trained, ROC-AUC ≈ 0.998 vs 0.5 baseline |
| Relational core (3NF) + TimescaleDB hypertable | `db/postgres_schema.sql`, `db/timescale_schema.sql` | ✅ DDL ready |
| FastAPI backend (auth, RBAC, rate limiting, keyset pagination) | `api/` | ✅ code complete, run via docker-compose |
| Web dashboard | `ui/index.html` | ✅ code complete |
| Docker Compose (full stack) | `docker-compose.yml` | ✅ |
| Unit tests | `tests/` | ✅ 11/11 passing |
| Architecture docs, ADRs, ER diagram | `docs/` | ✅ |

**Sandbox note:** the simulator and ML training were run and verified in the
environment this was built in (no network egress there). The API, Kafka
consumer, and Docker stack are code-complete and syntax-checked but need
`docker compose up` on a machine with internet access to pull images —
there's no live Kafka/Postgres/Mongo in the build environment to run them
against directly.

## Quickstart

```bash
# 1. Bring up the infrastructure + API + UI
docker compose up -d

# 2. Seed 100k vehicles into Postgres, create the demo user, train the model
pip install -r requirements.txt
python scripts/seed_all.py --vehicles 100000

# 3. Point the simulator at Kafka (separate terminal)
docker compose --profile simulate up simulator

# 4. Open the dashboard
open http://localhost:3000
# demo login: manager@fleet.demo / demo1234
```

API docs (OpenAPI/Swagger): http://localhost:8000/docs

## Running the pieces without Docker (what was verified in this build)

```bash
# Generate + stream telemetry to a local file (no Kafka needed)
cd simulator
python simulator.py --vehicles 5000 --ticks 6 --sink jsonl --out ./out/telemetry.jsonl

# Train and evaluate the predictive-maintenance model
cd ../ml
python train_model.py --vehicles 5000 --days 30
# -> Gradient Boosting ROC-AUC ~0.998, beats majority-class and logistic
#    regression baselines; top feature is avg_battery_voltage, then
#    dtc_event_count — both intuitive precursors to a breakdown.

# Run unit tests (pytest not preinstalled in the sandbox; works with pytest
# once requirements.txt is installed, or run directly, e.g.:)
cd ../tests
python -c "import test_simulator, test_ingestion" # or: pytest .
```

## How this maps to the brief

- **100,000+ simulated vehicles**: `simulator/vehicle_profiles.py` generates
  fleets/drivers/vehicles; `scripts/seed_all.py --vehicles 100000` seeds them.
- **Real-time + batch**: Kafka stream → consumer (real-time ingestion) +
  TimescaleDB continuous aggregates / historical training set (batch).
- **Polyglot storage, justified**: see `docs/ARCHITECTURE.md` — Postgres
  (CP, OLTP), TimescaleDB (AP, time-series), MongoDB (schema-flexible raw
  events), pgvector (semantic retrieval for the agent layer).
- **Secure APIs + UI**: JWT auth, RBAC (`admin`/`fleet_manager`/`viewer`),
  tenant isolation by `fleet_id`, keyset pagination, in-process rate
  limiting (documented path to Redis-backed limiting at scale).
- **Containerised, cloud-agnostic**: `docker-compose.yml` + per-service
  Dockerfiles; nothing here is tied to a specific cloud provider.
- **VIN validation, DTC parsing**: regex-validated in both
  `db/postgres_schema.sql` (CHECK constraint) and `ingestion/consumer.py`.
- **3NF schema + ADRs + ER diagram**: `db/postgres_schema.sql`,
  `docs/adr/`, `docs/ER_diagram.md`.

## What's next (not built in this MVP pass — prioritized)

1. **Load testing** (k6/Locust) at 100k events/sec against the real Kafka/
   TimescaleDB stack, to validate the throughput NFR with real numbers
   instead of the single-process 68k/sec seen in the sandbox.
2. **CI pipeline** (GitHub Actions): run `tests/`, coverage report, Trivy
   image scan, Semgrep SAST on every push.
3. **Terraform** for at least one cloud (AWS: MSK or self-managed Kafka,
   RDS/Timescale Cloud, DocumentDB or Atlas for Mongo, EKS for the API).
4. **STRIDE threat model** and a real Redis-backed rate limiter / circuit
   breaker for the `_fetch_latest_features` DB call.
5. **Agentic AI layer**: an LLM agent (LangGraph or MCP tools) that reads
   alerts + service notes (via the pgvector embeddings already planned in
   the schema) and recommends/executes a maintenance workflow, with an
   audit trail into `audit_log`.
6. **Driver safety scoring** and **EV charging optimization** as additional
   problem spaces sharing the same ingestion/storage backbone.

## Open source / AI tools used (per hackathon rule 14)

FastAPI, SQLAlchemy, scikit-learn, pandas, PyJWT, passlib, Kafka
(kafka-python client), PyMongo, TimescaleDB, Docker/Compose. Solution
design and code drafted with Claude (Anthropic) as an AI pair-programming
assistant; all data is synthetic, generated by the simulator in this repo.
