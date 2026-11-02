# ADR 001: S3 with Parquet as the data lake

- **Status:** Accepted
- **Date:** 2026-11-02
- **Decider:** Gusti Rais

## Context

The platform ingests 5-minute readings from about 30 zones across 3 simulated buildings,
plus building electricity and hourly weather data. That is roughly 8,600 zone readings a day
(about 3 million a year), and it will grow if more buildings or sensor types are added.

I need storage that:

- keeps the raw data cheaply and indefinitely, so I can reprocess it when rules or models change
- can be queried with SQL without running a database server around the clock
- works with the AWS services the pipeline uses (Firehose, Glue, Athena, Lambda)
- costs close to nothing at this scale

## Decision

Store all telemetry in Amazon S3, split into two buckets:

- **Raw:** data exactly as it arrived, partitioned by date (`year=/month=/day=`), never edited.
- **Curated:** cleaned, deduplicated, 15-minute data joined with weather, written by the Glue job.

Use **Apache Parquet** (Snappy-compressed) as the file format in both zones. Firehose converts
incoming JSON to Parquet. Query with Athena. Modelled tables for the dashboard go into
PostgreSQL later (ADR 002).

## Options considered

| Option | Why not chosen |
| --- | --- |
| CSV in S3 | Simple, but Athena scans every column and byte, so queries cost more and run slower. No schema or types. |
| Straight into PostgreSQL | Good for the dashboard, but storing all raw history in a running database costs more and makes reprocessing harder. |
| Amazon Timestream | Purpose-built for time series, but adds another service to learn and pay for, and isn't in the target role's stack. |
| Redshift | Far more capacity than this data needs, and its minimum cost is high for a personal project. |

## Consequences

**Good**
- Columnar Parquet means Athena reads only the columns and date partitions a query needs.
  In the Week 1 lab, Parquet was much smaller than the same data as CSV.
- Raw data is kept unchanged, so any later step can be rerun from the start.
- Storage at this volume costs well under USD 1 a month.

**Trade-offs**
- Parquet files can't be opened in a text editor; I use pandas or Athena to inspect them.
- Many small files hurt query speed. Firehose buffering and later compaction keep file counts down.
- Schema changes need care (tested in Week 6).
