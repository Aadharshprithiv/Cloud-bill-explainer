import io

import pytest

from analysis import analyze
from app import InMemoryRequest, create_app
from explain.templates import explain_charge, format_quantity, money
from parsers.cur_parser import MAX_BYTES
from tests.conftest import make_csv


@pytest.fixture()
def client():
    return create_app({"TESTING": True}).test_client()


def upload(client, data: bytes, filename="bill.csv"):
    return client.post("/analyze", data={"bill": (io.BytesIO(data), filename)},
                       content_type="multipart/form-data")


# --- Pages --------------------------------------------------------------------

def test_home_page(client):
    html = client.get("/").get_data(as_text=True)
    assert "Try with sample bill" in html
    assert "password or access keys" in html
    assert 'type="file"' in html


def test_privacy_page(client):
    html = client.get("/privacy").get_data(as_text=True)
    assert "Processed in memory" in html
    assert "No passwords or keys" in html


def test_sample_results_page(client):
    resp = client.get("/sample")
    html = resp.get_data(as_text=True)
    assert resp.status_code == 200
    assert "$210.05" in html
    assert "~$106.09" in html
    assert "A server (size m5.large) runs 24/7" in html
    assert "Small charges in Sydney" in html
    assert 'id="chart-data"' in html
    assert "Download report" in html
    assert "sample bill" in html
    # The technical line is available for each charge.
    assert "BoxUsage:m5.large" in html


def test_upload_sample_file(client, sample_bytes):
    resp = upload(client, sample_bytes)
    html = resp.get_data(as_text=True)
    assert resp.status_code == 200
    assert "$210.05" in html
    assert "made-up data" not in html


# --- Upload errors -------------------------------------------------------------

def test_upload_without_file(client):
    resp = client.post("/analyze", data={}, content_type="multipart/form-data")
    assert resp.status_code == 400
    assert "choose a CSV file" in resp.get_data(as_text=True)


def test_upload_wrong_extension(client):
    resp = upload(client, b"anything", filename="bill.xlsx")
    assert resp.status_code == 400
    assert "Please upload a .csv file" in resp.get_data(as_text=True)


def test_upload_unrecognised_csv(client):
    resp = upload(client, b"name,age\nAlice,30\n")
    html = resp.get_data(as_text=True)
    assert resp.status_code == 400
    assert "doesn&#39;t look like an AWS bill" in html
    assert "Cost and Usage Report" in html


def test_upload_too_large(client):
    resp = upload(client, b"a" * (MAX_BYTES + 128 * 1024))
    assert resp.status_code == 413
    assert "larger than 20 MB" in resp.get_data(as_text=True)


def test_bill_content_is_html_escaped(client):
    data = make_csv([{"lineItem/ProductCode": "<script>alert(1)</script>",
                      "lineItem/UsageType": "BoxUsage:t3.micro", "lineItem/UnblendedCost": "1"}])
    html = upload(client, data).get_data(as_text=True)
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


def test_uploads_stay_in_memory():
    request = InMemoryRequest.from_values()
    stream = request._get_file_stream(total_content_length=10 * 1024 * 1024, content_type="text/csv")
    assert isinstance(stream, io.BytesIO)


# --- Analysis and template explanations --------------------------------------------

def test_analyze_sample(sample_bytes):
    report = analyze(sample_bytes)
    assert report["total_saving"] == pytest.approx(106.09, abs=0.01)
    assert len(report["findings"]) == 9
    assert report["explanation_source"] == "template"
    assert all(c["explanation"] for c in report["charges"])
    flagged = {c["usage_type"] for c in report["charges"] if c["flagged"]}
    assert {"DataTransfer-Out-Bytes", "PublicIPv4:IdleAddress", "NatGateway-Bytes"} <= flagged
    assert "Request" not in flagged
    assert sum(s["cost"] for s in report["chart"]) == pytest.approx(210.05, abs=0.02)


def test_chart_folds_small_services_into_other():
    rows = [{"lineItem/ProductCode": f"Service{i}", "lineItem/UsageType": "X",
             "lineItem/UnblendedCost": str(10 - i)} for i in range(9)]
    chart = analyze(make_csv(rows))["chart"]
    assert len(chart) == 7
    assert chart[-1] == {"label": "Other", "cost": 9.0}  # 4 + 3 + 2


@pytest.mark.parametrize("amount, unit, expected", [
    (744, "Hrs", "744 hours"),
    (99.99999999, "GB-Mo", "100 GB-months"),
    (2.5, "GB", "2.5 GB"),
    (0.0042, "GB", "0.0042 GB"),
    (2_000_000, "Requests", "2,000,000 requests"),
    (3, "Widgets", "3 Widgets"),
])
def test_format_quantity(amount, unit, expected):
    assert format_quantity(amount, unit) == expected


def test_money():
    assert money(1234.5) == "$1,234.50"
    assert money(-4) == "-$4.00"
    assert money(3, "EUR") == "EUR 3.00"


def test_template_explanations():
    base = {"usage_amount": 744, "unit": "Hrs", "cost": 71.42, "region_name": "Sydney",
            "service_name": "EC2 (virtual servers)", "usage_name": "x", "instance_type": "m5.large"}
    server = explain_charge({**base, "usage_type": "APS2-BoxUsage:m5.large"})
    assert "virtual server" in server and "m5.large" in server and "744 hours" in server and "$71.42" in server
    unknown = explain_charge({**base, "usage_type": "Mystery-Thing", "instance_type": ""})
    assert "don't have a plain-English description" in unknown
