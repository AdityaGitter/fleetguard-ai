# ER Diagram — Relational Core (3NF)

```mermaid
erDiagram
    FLEET ||--o{ VEHICLE : owns
    FLEET ||--o{ DRIVER : employs
    FLEET ||--o{ SUBSCRIPTION : has
    FLEET ||--o{ APP_USER : has
    DRIVER ||--o{ VEHICLE : "assigned to"
    VEHICLE ||--o{ TRIP : records
    VEHICLE ||--o{ ALERT : raises
    DRIVER ||--o{ TRIP : drives

    FLEET {
        uuid fleet_id PK
        string name
        string segment
    }
    DRIVER {
        uuid driver_id PK
        uuid fleet_id FK
        string name
        string license_no
        numeric safety_score
    }
    VEHICLE {
        char17 vin PK
        uuid fleet_id FK
        uuid driver_id FK
        string vehicle_type
        string make
        string model
        smallint model_year
        string status
    }
    TRIP {
        bigint trip_id PK
        char17 vin FK
        uuid driver_id FK
        timestamptz started_at
        timestamptz ended_at
        numeric distance_km
    }
    ALERT {
        bigint alert_id PK
        char17 vin FK
        string alert_type
        string severity
        numeric risk_score
        string status
    }
    SUBSCRIPTION {
        uuid subscription_id PK
        uuid fleet_id FK
        string plan
        string status
    }
    APP_USER {
        uuid user_id PK
        uuid fleet_id FK
        string email
        string role
    }
```

Telemetry (TimescaleDB hypertable) is intentionally **not** in this diagram —
it relates to `VEHICLE` by `vin` as a soft foreign key (no DB-enforced FK
across the Postgres/Timescale boundary), keeping the high-volume time-series
store decoupled from the OLTP core. See `docs/ARCHITECTURE.md`.
