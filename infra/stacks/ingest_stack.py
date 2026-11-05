from aws_cdk import RemovalPolicy, Stack
from aws_cdk import aws_iam as iam
from aws_cdk import aws_iot as iot
from aws_cdk import aws_logs as logs
from constructs import Construct

TOPIC_FILTER = "campus/+/+"

# Payloads don't carry their own IDs; the topic campus/{building}/{device} does.
# Copy both segments into every record so they survive into Parquet.
RULE_SQL = f"SELECT *, topic(2) AS building_id, topic(3) AS device_id FROM '{TOPIC_FILTER}'"


class IngestStack(Stack):
    """IoT Core rule for campus/{building}/{device} messages.

    For now the rule writes enriched messages to CloudWatch Logs so building_id and device_id
    can be checked by eye. Week 2 step 4 replaces that action with Firehose -> S3 raw.
    """

    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        messages = logs.LogGroup(
            self,
            "RuleOutput",
            retention=logs.RetentionDays.ONE_WEEK,
            removal_policy=RemovalPolicy.DESTROY,
        )
        errors = logs.LogGroup(
            self,
            "RuleErrors",
            retention=logs.RetentionDays.ONE_WEEK,
            removal_policy=RemovalPolicy.DESTROY,
        )

        role = iam.Role(
            self,
            "RuleRole",
            assumed_by=iam.ServicePrincipal(
                "iot.amazonaws.com",
                conditions={"StringEquals": {"aws:SourceAccount": self.account}},
            ),
        )
        for group in (messages, errors):
            role.add_to_policy(
                iam.PolicyStatement(
                    actions=["logs:CreateLogStream", "logs:DescribeLogStreams", "logs:PutLogEvents"],
                    resources=[group.log_group_arn],
                )
            )

        def to_logs(group: logs.LogGroup) -> iot.CfnTopicRule.ActionProperty:
            return iot.CfnTopicRule.ActionProperty(
                cloudwatch_logs=iot.CfnTopicRule.CloudwatchLogsActionProperty(
                    log_group_name=group.log_group_name, role_arn=role.role_arn
                )
            )

        self.rule = iot.CfnTopicRule(
            self,
            "TelemetryRule",
            rule_name="campus_telemetry_ingest",  # letters, digits and underscores only
            topic_rule_payload=iot.CfnTopicRule.TopicRulePayloadProperty(
                sql=RULE_SQL,
                aws_iot_sql_version="2016-03-23",
                rule_disabled=False,
                actions=[to_logs(messages)],
                error_action=to_logs(errors),
            ),
        )
