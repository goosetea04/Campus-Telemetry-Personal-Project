#!/usr/bin/env python3
import aws_cdk as cdk

from stacks.ingest_stack import IngestStack
from stacks.storage_stack import StorageStack

app = cdk.App()
env = cdk.Environment(region="ap-southeast-2")

StorageStack(
    app,
    "CampusTelemetry-Storage",
    env=env,
    description="Campus Building Telemetry Platform: raw and curated S3 zones",
)

IngestStack(
    app,
    "CampusTelemetry-Ingest",
    env=env,
    description="Campus Building Telemetry Platform: IoT Core rule for campus/+/+",
)

cdk.Tags.of(app).add("project", "campus-telemetry")

app.synth()
