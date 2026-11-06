# Campus Building Telemetry Platform

An AWS data platform that ingests building sensor data, detects HVAC faults, estimates
their energy cost, and reports them on a dashboard. Built as a portfolio project
alongside the AWS Certified Data Engineer – Associate exam.

**Status:** Week 1 – simulator, storage stack and CI.

## Repo layout

| Folder | Contents |
| --- | --- |
| `schema/` | `telemetry.json`: column names, types and sources for the telemetry table |
| `simulator/` | Python sensor simulator with fault injection |
| `infra/` | AWS CDK (Python) stacks |
| `etl/` | Glue jobs (from Week 3) |
| `fdd/` | Fault detection rules (from Week 7) |
| `sql/` | Saved Athena and PostgreSQL queries |
| `docs/adr/` | Architecture decision records |
| `tests/` | Unit tests |

## Quick start

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt -r infra/requirements.txt
npm install -g aws-cdk

pytest                          # simulator tests
ruff check .                    # lint
```

### Generate data

```bash
# One healthy day: 3 buildings x (10 zones + 1 meter) x 288 readings
python -m simulator.simulate --days 1 --out data/readings.parquet

# With injected faults (repeat --fault for more)
python -m simulator.simulate --days 7 \
  --fault bldg-a/zone-03:stuck_sensor \
  --fault bldg-b/zone-05:valve_open_cold \
  --fault bldg-c/zone-01:simultaneous_heat_cool \
  --out data/week.parquet
```

Each row is one device at one 5-minute timestamp, identified by `building_id` and
`device_id`. `device_type` says which kind it is, and each kind fills only its own fields
(the rest are null):

| `device_type` | `device_id` | Fields |
| --- | --- | --- |
| `zone` | `zone-01` … | `zone_temp_c`, `setpoint_c`, `co2_ppm`, `heating_valve_pct`, `cooling_valve_pct` |
| `meter` | `meter-main` | `kw` (average demand over the 5 minutes), `kwh_total` (running meter reading) |

The meter is the building's electricity: base load (higher when occupied) plus the power
each zone's heating and cooling actually draws, so HVAC faults that waste energy show up there.

`ts` is UTC in ISO 8601 form
(`2026-11-10T03:05:00Z`); the simulation itself runs on Melbourne time, so a run that
starts at local midnight begins at 13:00 or 14:00 UTC the day before.

Each run also writes a labels file next to the readings (`data/week.parquet` gives
`data/week_labels.csv`) with one row per injected fault: `building_id`, `device_id`, `fault`,
`start_ts`, `end_ts`. Zones not listed are healthy. This is the ground truth the fault rules
are tested against later. It is kept out of the readings because real sensors don't report
their own faults, so it never goes to MQTT or the raw bucket.

| Fault | What the simulator does |
| --- | --- |
| `stuck_sensor` | Temperature reading never changes |
| `simultaneous_heat_cool` | Heating and cooling valves both at least 60% open |
| `valve_open_cold` | Heating valve reports 100% but no heat arrives, so the zone runs cold |

### Deploy the storage stack

```bash
cd infra
cdk bootstrap        # once per account and region
cdk synth            # check the CloudFormation it will create
cdk deploy           # creates raw and curated buckets, prints their names
```

Upload a sample day to the raw bucket, partitioned by date:

```bash
aws s3 cp data/readings.parquet \
  s3://<RawBucketName>/telemetry/year=2026/month=11/day=02/readings.parquet
```

Tear down when not in use (this **deletes the buckets and their contents**):

```bash
cdk destroy
```

## Decisions

- [ADR 001: S3 with Parquet as the data lake](docs/adr/001-s3-parquet-data-lake.md)
