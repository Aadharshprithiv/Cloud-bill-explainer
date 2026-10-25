"""Turn AWS technical names into plain English.

Everything here is a plain dictionary or a short list of patterns, so adding a
new name is a one-line change.
"""

import re

# lineItem/ProductCode -> plain service name
SERVICE_NAMES = {
    "AmazonEC2": "EC2 (virtual servers)",
    "AmazonVPC": "VPC (private network)",
    "AmazonS3": "S3 (file storage)",
    "AWSLambda": "Lambda (run code on demand)",
    "AWSDataTransfer": "Data transfer",
    "AmazonRDS": "RDS (managed databases)",
    "AmazonDynamoDB": "DynamoDB (database)",
    "AmazonCloudFront": "CloudFront (content delivery)",
    "AmazonRoute53": "Route 53 (domain names / DNS)",
    "AmazonCloudWatch": "CloudWatch (monitoring and logs)",
    "AWSELB": "Load balancer",
    "AmazonECS": "ECS (containers)",
    "AmazonEKS": "EKS (Kubernetes containers)",
    "AmazonSNS": "SNS (notifications)",
    "AWSQueueService": "SQS (message queues)",
    "AmazonSES": "SES (email sending)",
    "AmazonApiGateway": "API Gateway",
    "AmazonLightsail": "Lightsail (simple servers)",
    "AWSSecretsManager": "Secrets Manager",
    "awskms": "KMS (encryption keys)",
    "AmazonECR": "ECR (container image storage)",
    "AmazonEFS": "EFS (shared file storage)",
    "AWSBackup": "AWS Backup",
    "AWSCostExplorer": "Cost Explorer",
}

# product/region -> city / area people recognise
REGION_NAMES = {
    "us-east-1": "N. Virginia (USA)",
    "us-east-2": "Ohio (USA)",
    "us-west-1": "N. California (USA)",
    "us-west-2": "Oregon (USA)",
    "ca-central-1": "Canada (Central)",
    "ca-west-1": "Calgary (Canada)",
    "sa-east-1": "São Paulo (Brazil)",
    "mx-central-1": "Mexico (Central)",
    "eu-west-1": "Ireland",
    "eu-west-2": "London",
    "eu-west-3": "Paris",
    "eu-central-1": "Frankfurt",
    "eu-central-2": "Zurich",
    "eu-north-1": "Stockholm",
    "eu-south-1": "Milan",
    "eu-south-2": "Spain",
    "me-south-1": "Bahrain",
    "me-central-1": "UAE",
    "il-central-1": "Tel Aviv",
    "af-south-1": "Cape Town",
    "ap-south-1": "Mumbai",
    "ap-south-2": "Hyderabad",
    "ap-east-1": "Hong Kong",
    "ap-southeast-1": "Singapore",
    "ap-southeast-2": "Sydney",
    "ap-southeast-3": "Jakarta",
    "ap-southeast-4": "Melbourne",
    "ap-southeast-5": "Malaysia",
    "ap-northeast-1": "Tokyo",
    "ap-northeast-2": "Seoul",
    "ap-northeast-3": "Osaka",
    "global": "Global (not tied to one location)",
}

# Usage types carry a region prefix everywhere except us-east-1,
# e.g. "APS2-BoxUsage:t3.micro". Used when product/region is missing.
USAGE_PREFIX_REGIONS = {
    "USE1": "us-east-1", "USE2": "us-east-2", "USW1": "us-west-1", "USW2": "us-west-2",
    "CAN1": "ca-central-1", "CAN2": "ca-west-1", "SAE1": "sa-east-1", "MXC1": "mx-central-1",
    "EU": "eu-west-1", "EUW1": "eu-west-1", "EUW2": "eu-west-2", "EUW3": "eu-west-3",
    "EUC1": "eu-central-1", "EUC2": "eu-central-2", "EUN1": "eu-north-1",
    "EUS1": "eu-south-1", "EUS2": "eu-south-2", "MES1": "me-south-1", "MEC1": "me-central-1",
    "ILC1": "il-central-1", "AFS1": "af-south-1",
    "APS1": "ap-southeast-1", "APS2": "ap-southeast-2", "APS3": "ap-south-1",
    "APS4": "ap-southeast-3", "APS5": "ap-south-2", "APS6": "ap-southeast-4",
    "APN1": "ap-northeast-1", "APN2": "ap-northeast-2", "APN3": "ap-northeast-3",
    "APE1": "ap-east-1",
}

_PREFIX_RE = re.compile(r"^([A-Z]{2,4}\d?)-(?=.)")

# (regex on the usage type without its region prefix, plain name).
# First match wins, so put specific patterns before general ones.
# "{0}" is replaced with the first capture group (e.g. the instance type).
USAGE_TYPE_PATTERNS = [
    (r"^BoxUsage:(.+)$", "virtual server (EC2), size {0}"),
    (r"^BoxUsage$", "virtual server (EC2)"),
    (r"^(?:Dedicated|Host)Usage:(.+)$", "dedicated virtual server (EC2), size {0}"),
    (r"^SpotUsage:(.+)$", "discounted spare-capacity server (EC2 Spot), size {0}"),
    (r"^EBS:VolumeUsage\.(\w+)$", "storage disk (EBS volume, type {0})"),
    (r"^EBS:VolumeUsage$", "storage disk (EBS volume, older magnetic type)"),
    (r"^EBS:VolumeP-IOPS", "extra disk speed you pay for (EBS IOPS)"),
    (r"^EBS:VolumeP-Throughput", "extra disk throughput you pay for (EBS)"),
    (r"^EBS:SnapshotUsage", "disk backup (EBS snapshot)"),
    (r"^EBS:SnapshotArchive", "archived disk backup (EBS snapshot archive)"),
    (r"IdleAddress", "unused public IP address"),
    (r"InUseAddress", "public IP address in use"),
    (r"^NatGateway-Hours", "NAT gateway (internet access for private servers), hourly charge"),
    (r"^NatGateway-Bytes", "NAT gateway, data passed through it"),
    (r"^DataTransfer-Out-Bytes", "data sent out to the internet"),
    (r"^DataTransfer-In-Bytes", "data received from the internet"),
    (r"^DataTransfer-Regional-Bytes", "data sent between zones in the same region"),
    (r"-AWS-Out-Bytes$", "data sent to another AWS region"),
    (r"-AWS-In-Bytes$", "data received from another AWS region"),
    (r"^CloudFront-Out-Bytes|^DataTransfer-Out-Bytes-CloudFront", "data sent to CloudFront"),
    (r"^TimedStorage-ByteHrs", "file storage (S3 Standard)"),
    (r"^TimedStorage-INT-FA-ByteHrs", "file storage (S3 Intelligent-Tiering, frequent access)"),
    (r"^TimedStorage-INT", "file storage (S3 Intelligent-Tiering)"),
    (r"^TimedStorage-SIA", "file storage (S3 Standard-Infrequent Access)"),
    (r"^TimedStorage-ZIA", "file storage (S3 One Zone-Infrequent Access)"),
    (r"^TimedStorage-GlacierByteHrs|^TimedStorage-GIR|^TimedStorage-GDA", "archive storage (S3 Glacier)"),
    (r"^Requests-Tier1", "S3 requests: uploads and listings"),
    (r"^Requests-Tier2", "S3 requests: downloads and reads"),
    (r"^Requests-", "S3 requests"),
    (r"^Lambda-GB-Second", "Lambda compute time (how long your code ran x memory)"),
    (r"^Request$", "Lambda function calls"),
    (r"^Lambda-Provisioned", "Lambda kept-warm capacity"),
    (r"^InstanceUsage:(.+)$", "managed database server (RDS), size {0}"),
    (r"^RDS:GP[23]-Storage|^RDS:StorageUsage", "database disk storage (RDS)"),
    (r"^LoadBalancerUsage", "load balancer, hourly charge"),
    (r"^LCUUsage", "load balancer, traffic-based charge"),
    (r"^CW:", "monitoring (CloudWatch)"),
    (r"^HostedZone", "domain DNS zone (Route 53)"),
    (r"^DNS-Queries", "domain lookups (Route 53)"),
]
_COMPILED_PATTERNS = [(re.compile(p), name) for p, name in USAGE_TYPE_PATTERNS]


def split_usage_prefix(usage_type: str) -> tuple[str | None, str]:
    """Split "APS2-BoxUsage:t3.micro" into ("APS2", "BoxUsage:t3.micro").

    Only known region prefixes are stripped, so "NatGateway-Hours" stays whole.
    """
    match = _PREFIX_RE.match(usage_type or "")
    if match and match.group(1) in USAGE_PREFIX_REGIONS:
        return match.group(1), usage_type[match.end():]
    return None, usage_type or ""


def region_from_usage_type(usage_type: str) -> str | None:
    prefix, _ = split_usage_prefix(usage_type)
    return USAGE_PREFIX_REGIONS.get(prefix) if prefix else None


def plain_service(product_code: str) -> str:
    if not product_code:
        return "Other charges"
    return SERVICE_NAMES.get(product_code, product_code)


def plain_region(region: str) -> str:
    if not region:
        return REGION_NAMES["global"]
    return REGION_NAMES.get(region, region)


def plain_usage_type(usage_type: str) -> str:
    """Best-effort plain name; falls back to the original technical name."""
    _, core = split_usage_prefix(usage_type)
    for pattern, name in _COMPILED_PATTERNS:
        match = pattern.search(core)
        if match:
            return name.format(*match.groups())
    return usage_type or "unlabelled charge"


def instance_type_from_usage(usage_type: str) -> str | None:
    """Return "t2.medium" for "BoxUsage:t2.medium" (any region prefix)."""
    _, core = split_usage_prefix(usage_type)
    match = re.match(r"^(?:BoxUsage|SpotUsage|DedicatedUsage|HostUsage):(.+)$", core)
    return match.group(1) if match else None
