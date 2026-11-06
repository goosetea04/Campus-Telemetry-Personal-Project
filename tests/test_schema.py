"""The simulator's output must match schema/telemetry.json, the contract the Glue table is built from."""

import json
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from simulator.simulate import COLUMNS, METER_FIELDS, PAYLOAD_FIELDS, ZONE_FIELDS, SimConfig, simulate

SCHEMA = json.loads((Path(__file__).parents[1] / "schema" / "telemetry.json").read_text())
COLS = SCHEMA["columns"]

# Glue type -> Arrow types that pandas may write for it (pandas 3 strings can be large_string).
ARROW_TYPES = {"string": {pa.string(), pa.large_string()}, "double": {pa.float64()}}


def names(predicate) -> list[str]:
    return [c["name"] for c in COLS if predicate(c)]


def test_schema_lists_simulator_columns_in_order():
    assert names(lambda c: True) == COLUMNS


def test_schema_device_fields_match_simulator():
    assert names(lambda c: c["devices"] == ["zone"]) == ZONE_FIELDS
    assert names(lambda c: c["devices"] == ["meter"]) == METER_FIELDS


def test_every_reading_is_double():
    assert {c["type"] for c in COLS} <= set(ARROW_TYPES)
    assert names(lambda c: c["type"] == "double") == ZONE_FIELDS + METER_FIELDS


def test_topic_columns_name_their_segment():
    topic_cols = [c for c in COLS if c["source"] == "topic"]
    assert [(c["name"], c["topic_segment"]) for c in topic_cols] == [("building_id", 2), ("device_id", 3)]


def test_message_payload_is_every_non_topic_column():
    assert names(lambda c: c["source"] == "payload") == PAYLOAD_FIELDS


def test_every_column_is_documented():
    assert all(c["comment"] for c in COLS)


@pytest.fixture(scope="module")
def parquet_schema(tmp_path_factory) -> pa.Schema:
    path = tmp_path_factory.mktemp("schema") / "readings.parquet"
    simulate(SimConfig(buildings=1, zones_per_building=1)).to_parquet(path, index=False)
    return pq.read_schema(path)


@pytest.mark.parametrize("col", COLS, ids=lambda c: c["name"])
def test_parquet_output_matches_schema_type(parquet_schema, col):
    assert parquet_schema.field(col["name"]).type in ARROW_TYPES[col["type"]]
