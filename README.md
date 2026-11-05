# Campus Building Telemetry Platform

An AWS data platform that ingests building sensor data, detects HVAC faults, estimates
their energy cost, and reports them on a dashboard. Built as a portfolio project
alongside the AWS Certified Data Engineer – Associate exam.

**Status:** Week 1 – simulator, storage stack and CI.

## Repo layout

| Folder | Contents |
| --- | --- |
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
# One healthy day: 3 buildings x 10 zones x 288 readings
python -m simulator.simulate --days 1 --out data/readings.parquet

# With injected faults (repeat --fault for more)
python -m simulator.simulate --days 7 \
  --fault bldg-a/zone-03:stuck_sensor \
  --fault bldg-b/zone-05:valve_open_cold \
  --fault bldg-c/zone-01:simultaneous_heat_cool \
  --out data/week.parquet
```

Each row is one zone at one 5-minute timestamp. `fault_injected` is the ground-truth
label the fault rules are tested against later; it would not exist in real building data.

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
