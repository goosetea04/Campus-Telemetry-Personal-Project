import json
from pathlib import Path

import aws_cdk as cdk
from aws_cdk.assertions import Template

from stacks.ingest_stack import IngestStack

SCHEMA = json.loads((Path(__file__).parents[1] / "schema" / "telemetry.json").read_text())


def rule_payload() -> dict:
    app = cdk.App()
    rules = Template.from_stack(IngestStack(app, "Test")).find_resources("AWS::IoT::TopicRule")
    assert len(rules) == 1
    return next(iter(rules.values()))["Properties"]["TopicRulePayload"]


def test_rule_listens_to_all_campus_devices():
    assert "FROM 'campus/+/+'" in rule_payload()["Sql"]


def test_rule_adds_every_column_the_schema_says_comes_from_the_topic():
    sql = rule_payload()["Sql"]
    topic_cols = [c for c in SCHEMA["columns"] if c["source"] == "topic"]
    assert topic_cols
    for col in topic_cols:
        assert f"topic({col['topic_segment']}) AS {col['name']}" in sql


def test_rule_uses_current_sql_version_and_has_error_action():
    payload = rule_payload()
    assert payload["AwsIotSqlVersion"] == "2016-03-23"
    assert "ErrorAction" in payload
