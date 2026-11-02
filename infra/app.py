#!/usr/bin/env python3
import aws_cdk as cdk

from stacks.storage_stack import StorageStack

app = cdk.App()

StorageStack(
    app,
    "CampusTelemetry-Storage",
    env=cdk.Environment(region="ap-southeast-2"),
    description="Campus Building Telemetry Platform: raw and curated S3 zones",
)

cdk.Tags.of(app).add("project", "campus-telemetry")

app.synth()
