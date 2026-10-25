import gzip

import pytest

from parsers.cur_parser import MAX_BYTES, BillParseError, parse_cur
from parsers.grouping import cost_by_service, group_line_items
from parsers.names import (plain_region, plain_service, plain_usage_type,
                           region_from_usage_type, split_usage_prefix)
from parsers.sanitize import scrub_text
from tests.conftest import make_csv


# --- Sample bill -------------------------------------------------------------

def test_sample_bill_parses(sample_bytes):
    bill = parse_cur(sample_bytes)
    assert len(bill.items) == 31 * 17
    assert bill.total_cost == pytest.approx(210.05, abs=0.01)
    assert bill.currency == "USD"
    assert bill.period_start.strftime("%Y-%m-%d") == "2025-10-01"
    assert bill.period_end.strftime("%Y-%m-%d") == "2025-10-31"
    assert bill.warnings == []


def test_sample_bill_has_numeric_columns(sample_bytes):
    items = parse_cur(sample_bytes).items
    assert items["cost"].dtype == float
    assert items["usage_amount"].dtype == float


def test_sample_bill_covers_required_scenarios(sample_bytes):
    usage_types = set(parse_cur(sample_bytes).items["usage_type"])
    for expected in ["BoxUsage:m5.large", "BoxUsage:t2.medium", "EBS:VolumeUsage.gp3",
                     "EBS:SnapshotUsage", "TimedStorage-ByteHrs", "PublicIPv4:IdleAddress",
                     "NatGateway-Hours", "DataTransfer-Out-Bytes", "APS2-BoxUsage:t3.micro",
                     "Lambda-GB-Second", "Request"]:
        assert expected in usage_types


# --- Header variants and missing columns -------------------------------------

def test_cur2_snake_case_headers():
    data = make_csv([{
        "line_item_product_code": "AmazonEC2",
        "line_item_usage_type": "BoxUsage:t3.micro",
        "line_item_usage_amount": "10",
        "pricing_unit": "Hrs",
        "line_item_unblended_cost": "0.104",
        "product_region_code": "eu-west-2",
    }])
    bill = parse_cur(data)
    row = bill.items.iloc[0]
    assert row["product_code"] == "AmazonEC2"
    assert row["region"] == "eu-west-2"
    assert bill.total_cost == pytest.approx(0.104)


def test_headers_are_case_and_space_insensitive():
    data = b" LINEITEM/PRODUCTCODE , lineitem/unblendedcost\nAmazonS3,1.5\n"
    bill = parse_cur(data)
    assert bill.items.iloc[0]["product_code"] == "AmazonS3"
    assert bill.total_cost == pytest.approx(1.5)


def test_missing_optional_columns_still_parses_with_warnings():
    data = make_csv([
        {"lineItem/UsageType": "APS2-BoxUsage:t3.micro", "lineItem/UnblendedCost": "2.00"},
        {"lineItem/UsageType": "BoxUsage:t3.micro", "lineItem/UnblendedCost": "3.00"},
    ])
    bill = parse_cur(data)
    assert bill.total_cost == pytest.approx(5.0)
    # Region is worked out from the usage-type prefix; no prefix means us-east-1.
    assert list(bill.items["region"]) == ["ap-southeast-2", "us-east-1"]
    assert bill.items["usage_amount"].sum() == 0
    assert any("region" in w for w in bill.warnings)
    assert any("service name" in w for w in bill.warnings)
    assert bill.period_start is None


def test_tax_rows_without_usage_type_are_labelled():
    data = make_csv([
        {"lineItem/ProductCode": "AmazonEC2", "lineItem/UsageType": "", "lineItem/LineItemType": "Tax",
         "lineItem/UnblendedCost": "4.20", "product/region": ""},
    ])
    row = parse_cur(data).items.iloc[0]
    assert row["usage_type"] == "Tax"
    assert row["region"] == "global"


def test_unreadable_costs_count_as_zero_with_warning():
    data = make_csv([
        {"lineItem/ProductCode": "AmazonS3", "lineItem/UnblendedCost": "abc"},
        {"lineItem/ProductCode": "AmazonS3", "lineItem/UnblendedCost": "1.25"},
    ])
    bill = parse_cur(data)
    assert bill.total_cost == pytest.approx(1.25)
    assert any("couldn't read" in w for w in bill.warnings)


def test_negative_credits_reduce_total():
    data = make_csv([
        {"lineItem/ProductCode": "AmazonEC2", "lineItem/UsageType": "BoxUsage:t3.micro", "lineItem/UnblendedCost": "10"},
        {"lineItem/ProductCode": "AmazonEC2", "lineItem/UsageType": "", "lineItem/LineItemType": "Credit",
         "lineItem/UnblendedCost": "-4"},
    ])
    assert parse_cur(data).total_cost == pytest.approx(6.0)


def test_latin1_file_is_read():
    data = "lineItem/ProductCode,lineItem/UnblendedCost,lineItem/LineItemDescription\nAmazonS3,1,São Paulo\n"
    bill = parse_cur(data.encode("latin-1"))
    assert bill.items.iloc[0]["description"] == "São Paulo"


# --- Files we reject ---------------------------------------------------------

@pytest.mark.parametrize("data", [b"", b"   \n  "])
def test_empty_file_rejected(data):
    with pytest.raises(BillParseError, match="empty"):
        parse_cur(data)


def test_headers_only_rejected():
    with pytest.raises(BillParseError, match="no charges"):
        parse_cur(b"lineItem/ProductCode,lineItem/UnblendedCost\n")


def test_unrelated_csv_rejected_with_help():
    with pytest.raises(BillParseError) as exc:
        parse_cur(b"name,age\nAlice,30\n")
    assert "doesn't look like an AWS bill" in exc.value.user_message
    assert "Cost and Usage Report" in exc.value.user_message


def test_cost_column_required():
    with pytest.raises(BillParseError, match="UnblendedCost"):
        parse_cur(b"lineItem/ProductCode,lineItem/UsageType\nAmazonS3,TimedStorage-ByteHrs\n")


def test_compressed_file_rejected():
    with pytest.raises(BillParseError, match="unzip"):
        parse_cur(gzip.compress(b"lineItem/ProductCode,lineItem/UnblendedCost\nAmazonS3,1\n"))


def test_oversized_file_rejected():
    with pytest.raises(BillParseError, match="20 MB"):
        parse_cur(b"a" * (MAX_BYTES + 1))


# --- Grouping ----------------------------------------------------------------

def test_grouping_totals_match_bill(sample_bytes):
    bill = parse_cur(sample_bytes)
    groups = group_line_items(bill.items)
    assert len(groups) == 15  # two EBS volumes and two snapshots share a group
    assert groups["cost"].sum() == pytest.approx(bill.total_cost)
    assert list(groups["cost"]) == sorted(groups["cost"], reverse=True)
    assert list(groups["id"]) == list(range(1, 16))


def test_grouping_sums_usage_and_counts_resources(sample_bytes):
    groups = group_line_items(parse_cur(sample_bytes).items).set_index("usage_type")
    server = groups.loc["BoxUsage:m5.large"]
    assert server["usage_amount"] == pytest.approx(744)
    assert server["unit"] == "Hrs"
    assert server["instance_type"] == "m5.large"
    assert server["usage_name"] == "virtual server (EC2), size m5.large"
    assert groups.loc["EBS:SnapshotUsage", "resource_count"] == 2
    assert groups.loc["APS2-BoxUsage:t3.micro", "region_name"] == "Sydney"


def test_grouping_contains_no_identifiers(sample_bytes):
    groups = group_line_items(parse_cur(sample_bytes).items)
    text = groups.astype(str).to_csv()
    assert "123456789012" not in text
    assert "i-0a1b2c3d" not in text
    assert "arn:aws" not in text


def test_cost_by_service(sample_bytes):
    services = cost_by_service(group_line_items(parse_cur(sample_bytes).items))
    assert services[0][0] == "EC2 (virtual servers)"
    assert sum(cost for _, cost in services) == pytest.approx(210.05, abs=0.01)


def test_grouping_empty_frame():
    import pandas as pd
    assert group_line_items(pd.DataFrame()).empty


# --- Plain names ---------------------------------------------------------------

@pytest.mark.parametrize("usage_type, expected", [
    ("BoxUsage", "virtual server (EC2)"),
    ("BoxUsage:t2.medium", "virtual server (EC2), size t2.medium"),
    ("USE2-BoxUsage:t3.medium", "virtual server (EC2), size t3.medium"),
    ("EBS:SnapshotUsage", "disk backup (EBS snapshot)"),
    ("PublicIPv4:IdleAddress", "unused public IP address"),
    ("NatGateway-Hours", "NAT gateway (internet access for private servers), hourly charge"),
    ("USE1-APS2-AWS-Out-Bytes", "data sent to another AWS region"),
    ("SomethingNew-Units", "SomethingNew-Units"),  # unknown -> unchanged, never guessed
])
def test_plain_usage_type(usage_type, expected):
    assert plain_usage_type(usage_type) == expected


def test_plain_region_and_service():
    assert plain_region("ap-southeast-2") == "Sydney"
    assert plain_region("") == "Global (not tied to one location)"
    assert plain_region("xx-new-1") == "xx-new-1"
    assert plain_service("AmazonEC2") == "EC2 (virtual servers)"
    assert plain_service("AmazonNewThing") == "AmazonNewThing"


def test_usage_prefix_only_strips_known_regions():
    assert split_usage_prefix("APS2-BoxUsage:t3.micro") == ("APS2", "BoxUsage:t3.micro")
    assert split_usage_prefix("NatGateway-Hours") == (None, "NatGateway-Hours")
    assert region_from_usage_type("EUW2-TimedStorage-ByteHrs") == "eu-west-2"
    assert region_from_usage_type("TimedStorage-ByteHrs") is None


# --- Sanitising ----------------------------------------------------------------

def test_scrub_text_removes_identifiers():
    text = ("Instance i-0a1b2c3d4e5f60001 in account 123456789012, "
            "arn:aws:ec2:us-east-1:123456789012:natgateway/nat-0a1b2c3d4e5f60001 and vol-0a1b2c3d4e5f60001")
    scrubbed = scrub_text(text)
    assert "i-0a1b2c3d4e5f60001" not in scrubbed
    assert "123456789012" not in scrubbed
    assert "arn:aws" not in scrubbed
    assert "vol-0a1b" not in scrubbed
    assert "[resource]" in scrubbed and "[account]" in scrubbed


def test_scrub_text_keeps_ordinary_text():
    text = "$0.096 per On Demand Linux m5.large Instance Hour"
    assert scrub_text(text) == text
    assert scrub_text("") == ""
