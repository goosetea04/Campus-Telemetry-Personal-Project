import aws_cdk as cdk
from aws_cdk.assertions import Template

from stacks.ingest_stack import IngestStack


def rule_payload() -> dict:
    app = cdk.App()
    rules = Template.from_stack(IngestStack(app, "Test")).find_resources("AWS::IoT::TopicRule")
    assert len(rules) == 1
    return next(iter(rules.values()))["Properties"]["TopicRulePayload"]


def test_rule_listens_to_all_campus_devices():
    assert "FROM 'campus/+/+'" in rule_payload()["Sql"]


def test_rule_adds_ids_from_topic():
    sql = rule_payload()["Sql"]
    assert "topic(2) AS building_id" in sql
    assert "topic(3) AS device_id" in sql


def test_rule_uses_current_sql_version_and_has_error_action():
    payload = rule_payload()
    assert payload["AwsIotSqlVersion"] == "2016-03-23"
    assert "ErrorAction" in payload
