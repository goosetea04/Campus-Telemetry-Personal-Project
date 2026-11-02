from aws_cdk import CfnOutput, Duration, RemovalPolicy, Stack
from aws_cdk import aws_s3 as s3
from constructs import Construct


class StorageStack(Stack):
    """Raw and curated S3 zones for the data lake.

    Encryption uses S3-managed keys for now; Week 9 switches to a customer-managed KMS key.
    RemovalPolicy.DESTROY + auto_delete_objects suit a personal learning account so
    `cdk destroy` leaves nothing billing. A production stack would RETAIN data instead.
    """

    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        common = {
            "encryption": s3.BucketEncryption.S3_MANAGED,
            "block_public_access": s3.BlockPublicAccess.BLOCK_ALL,
            "enforce_ssl": True,
            "versioned": False,
            "removal_policy": RemovalPolicy.DESTROY,
            "auto_delete_objects": True,
        }

        self.raw_bucket = s3.Bucket(
            self,
            "RawBucket",
            lifecycle_rules=[
                s3.LifecycleRule(
                    id="abort-incomplete-uploads",
                    abort_incomplete_multipart_upload_after=Duration.days(1),
                )
            ],
            **common,
        )

        self.curated_bucket = s3.Bucket(self, "CuratedBucket", **common)

        CfnOutput(self, "RawBucketName", value=self.raw_bucket.bucket_name)
        CfnOutput(self, "CuratedBucketName", value=self.curated_bucket.bucket_name)
