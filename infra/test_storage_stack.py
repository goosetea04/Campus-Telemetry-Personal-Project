import aws_cdk as cdk
from aws_cdk.assertions import Template

from stacks.storage_stack import StorageStack


def template() -> Template:
    app = cdk.App()
    return Template.from_stack(StorageStack(app, "Test"))


def test_two_buckets():
    template().resource_count_is("AWS::S3::Bucket", 2)


def test_buckets_encrypted_and_private():
    t = template()
    for bucket in t.find_resources("AWS::S3::Bucket").values():
        props = bucket["Properties"]
        assert "BucketEncryption" in props
        pab = props["PublicAccessBlockConfiguration"]
        flags = ("BlockPublicAcls", "BlockPublicPolicy", "IgnorePublicAcls", "RestrictPublicBuckets")
        assert all(pab[k] for k in flags)
