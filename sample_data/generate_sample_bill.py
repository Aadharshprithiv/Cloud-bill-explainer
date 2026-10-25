"""Generate sample_aws_bill.csv: one month of realistic, entirely fake AWS CUR data.

Run from the project root:  python sample_data/generate_sample_bill.py

The account ID, resource IDs and bucket/function names are made up.
Prices are close to real us-east-1 / ap-southeast-2 on-demand prices.
"""

import csv
from datetime import datetime, timedelta
from pathlib import Path

ACCOUNT_ID = "123456789012"
MONTH_START = datetime(2025, 10, 1)
DAYS = 31  # October -> 744 hours

COLUMNS = [
    "identity/LineItemId",
    "bill/PayerAccountId",
    "lineItem/UsageAccountId",
    "lineItem/LineItemType",
    "lineItem/UsageStartDate",
    "lineItem/UsageEndDate",
    "lineItem/ProductCode",
    "lineItem/UsageType",
    "lineItem/Operation",
    "lineItem/ResourceId",
    "lineItem/UsageAmount",
    "pricing/unit",
    "lineItem/UnblendedRate",
    "lineItem/UnblendedCost",
    "lineItem/CurrencyCode",
    "lineItem/LineItemDescription",
    "product/region",
    "product/instanceType",
]

# Each item is billed once per day. daily_amount * DAYS = monthly usage.
# (product, usage_type, operation, resource_id, daily_amount, unit, rate, description, region, instance_type)
ITEMS = [
    # Main web server, on all month: 24 h/day * 31 = 744 hours.
    ("AmazonEC2", "BoxUsage:m5.large", "RunInstances", "i-0a1b2c3d4e5f60001",
     24, "Hrs", 0.096, "$0.096 per On Demand Linux m5.large Instance Hour",
     "us-east-1", "m5.large"),
    # Old-generation t2 instance, used about 12 hours a day.
    ("AmazonEC2", "BoxUsage:t2.medium", "RunInstances", "i-0a1b2c3d4e5f60002",
     12, "Hrs", 0.0464, "$0.0464 per On Demand Linux t2.medium Instance Hour",
     "us-east-1", "t2.medium"),
    # EBS disks attached to the two servers.
    ("AmazonEC2", "EBS:VolumeUsage.gp3", "CreateVolume-Gp3", "vol-0a1b2c3d4e5f60001",
     80 / DAYS, "GB-Mo", 0.08, "$0.08 per GB-month of General Purpose (gp3) provisioned storage - US East (Northern Virginia)",
     "us-east-1", ""),
    ("AmazonEC2", "EBS:VolumeUsage.gp3", "CreateVolume-Gp3", "vol-0a1b2c3d4e5f60002",
     20 / DAYS, "GB-Mo", 0.08, "$0.08 per GB-month of General Purpose (gp3) provisioned storage - US East (Northern Virginia)",
     "us-east-1", ""),
    # Snapshots that have piled up (170 GB-month -> $8.50).
    ("AmazonEC2", "EBS:SnapshotUsage", "CreateSnapshot", "arn:aws:ec2:us-east-1:123456789012:snapshot/snap-0a1b2c3d4e5f60001",
     110 / DAYS, "GB-Mo", 0.05, "$0.05 per GB-Month of snapshot data stored - US East (Northern Virginia)",
     "us-east-1", ""),
    ("AmazonEC2", "EBS:SnapshotUsage", "CreateSnapshot", "arn:aws:ec2:us-east-1:123456789012:snapshot/snap-0a1b2c3d4e5f60002",
     60 / DAYS, "GB-Mo", 0.05, "$0.05 per GB-Month of snapshot data stored - US East (Northern Virginia)",
     "us-east-1", ""),
    # Public IPv4: one attached to the web server, one sitting idle.
    ("AmazonVPC", "PublicIPv4:InUseAddress", "AssociateAddressVPC", "arn:aws:ec2:us-east-1:123456789012:ipv4pool-ec2/eipalloc-0a1b2c3d4e5f60001",
     24, "Hrs", 0.005, "$0.005 per In-use public IPv4 address per hour",
     "us-east-1", ""),
    ("AmazonVPC", "PublicIPv4:IdleAddress", "AllocateAddressVPC", "arn:aws:ec2:us-east-1:123456789012:ipv4pool-ec2/eipalloc-0a1b2c3d4e5f60002",
     24, "Hrs", 0.005, "$0.005 per Idle public IPv4 address per hour",
     "us-east-1", ""),
    # NAT gateway: hourly charge + data processed.
    ("AmazonEC2", "NatGateway-Hours", "NatGateway", "arn:aws:ec2:us-east-1:123456789012:natgateway/nat-0a1b2c3d4e5f60001",
     24, "Hrs", 0.045, "$0.045 per NAT Gateway Hour",
     "us-east-1", ""),
    ("AmazonEC2", "NatGateway-Bytes", "NatGateway", "arn:aws:ec2:us-east-1:123456789012:natgateway/nat-0a1b2c3d4e5f60001",
     50 / DAYS, "GB", 0.045, "$0.045 per GB Data Processed by NAT Gateways",
     "us-east-1", ""),
    # Data transfer out to the internet (~400 GB).
    ("AWSDataTransfer", "DataTransfer-Out-Bytes", "RunInstances", "i-0a1b2c3d4e5f60001",
     400 / DAYS, "GB", 0.09, "$0.090 per GB - first 10 TB / month data transfer out beyond the global free tier",
     "us-east-1", ""),
    # S3 Standard storage (250 GB-month) + requests.
    ("AmazonS3", "TimedStorage-ByteHrs", "StandardStorage", "my-photo-backups-2025",
     250 / DAYS, "GB-Mo", 0.023, "$0.023 per GB - first 50 TB / month of storage used",
     "us-east-1", ""),
    ("AmazonS3", "Requests-Tier1", "PutObject", "my-photo-backups-2025",
     4000, "Requests", 0.000005, "$0.005 per 1,000 PUT, COPY, POST, or LIST requests",
     "us-east-1", ""),
    # Lambda: 2M requests and 500k GB-seconds a month.
    ("AWSLambda", "Request", "Invoke", "arn:aws:lambda:us-east-1:123456789012:function:resize-images",
     2_000_000 / DAYS, "Requests", 0.0000002, "AWS Lambda - Total Requests - US East (Northern Virginia)",
     "us-east-1", ""),
    ("AWSLambda", "Lambda-GB-Second", "Invoke", "arn:aws:lambda:us-east-1:123456789012:function:resize-images",
     500_000 / DAYS, "Lambda-GB-Second", 0.0000166667, "AWS Lambda - Total Compute - US East (Northern Virginia)",
     "us-east-1", ""),
    # A small leftover test server and disk in Sydney.
    ("AmazonEC2", "APS2-BoxUsage:t3.micro", "RunInstances", "i-0f9e8d7c6b5a40001",
     24, "Hrs", 0.0132, "$0.0132 per On Demand Linux t3.micro Instance Hour",
     "ap-southeast-2", "t3.micro"),
    ("AmazonEC2", "APS2-EBS:VolumeUsage.gp3", "CreateVolume-Gp3", "vol-0f9e8d7c6b5a40001",
     8 / DAYS, "GB-Mo", 0.096, "$0.096 per GB-month of General Purpose (gp3) provisioned storage - Asia Pacific (Sydney)",
     "ap-southeast-2", ""),
]


def build_rows():
    rows = []
    line_id = 1
    for day in range(DAYS):
        start = MONTH_START + timedelta(days=day)
        end = start + timedelta(days=1)
        for (product, usage_type, operation, resource_id, amount, unit, rate,
             description, region, instance_type) in ITEMS:
            rows.append({
                "identity/LineItemId": f"sample{line_id:06d}",
                "bill/PayerAccountId": ACCOUNT_ID,
                "lineItem/UsageAccountId": ACCOUNT_ID,
                "lineItem/LineItemType": "Usage",
                "lineItem/UsageStartDate": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "lineItem/UsageEndDate": end.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "lineItem/ProductCode": product,
                "lineItem/UsageType": usage_type,
                "lineItem/Operation": operation,
                "lineItem/ResourceId": resource_id,
                "lineItem/UsageAmount": f"{amount:.10f}".rstrip("0").rstrip("."),
                "pricing/unit": unit,
                "lineItem/UnblendedRate": f"{rate:.10f}".rstrip("0").rstrip("."),
                "lineItem/UnblendedCost": f"{amount * rate:.10f}",
                "lineItem/CurrencyCode": "USD",
                "lineItem/LineItemDescription": description,
                "product/region": region,
                "product/instanceType": instance_type,
            })
            line_id += 1
    return rows


def main():
    out = Path(__file__).with_name("sample_aws_bill.csv")
    with out.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(build_rows())
    print(f"Wrote {out}")


if __name__ == "__main__":
    main()
