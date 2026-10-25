import pytest

from parsers.cur_parser import parse_cur
from rules import RULES, build_context, run_rules, total_saving
from rules.always_on import WORKING_HOURS_PER_MONTH, always_on_servers
from rules.base import Finding
from rules.data_transfer import high_data_transfer
from rules.forgotten_region import forgotten_region
from rules.idle_ip import idle_public_ip
from rules.nat_gateway import nat_gateway
from rules.old_generation import old_generation_instances
from rules.s3_standard import large_s3_standard
from rules.snapshots import snapshot_buildup
from tests.conftest import make_csv


def line(product, usage_type, amount, cost, region="us-east-1", resource="", unit="Hrs",
         start="2025-10-01T00:00:00Z"):
    return {
        "lineItem/ProductCode": product, "lineItem/UsageType": usage_type,
        "lineItem/UsageAmount": amount, "pricing/unit": unit, "lineItem/UnblendedCost": cost,
        "product/region": region, "lineItem/ResourceId": resource, "lineItem/UsageStartDate": start,
    }


def context(*rows):
    return build_context(parse_cur(make_csv(list(rows))))


@pytest.fixture(scope="module")
def sample_ctx(sample_bytes):
    return build_context(parse_cur(sample_bytes))


@pytest.fixture(scope="module")
def clean_ctx():
    """A tidy small bill that should trigger no rules."""
    return context(
        line("AmazonEC2", "BoxUsage:t3.micro", 200, 2.08, resource="i-0000000000000000a"),
        line("AmazonEC2", "EBS:VolumeUsage.gp3", 8, 0.64, unit="GB-Mo"),
        line("AmazonEC2", "EBS:SnapshotUsage", 20, 1.00, unit="GB-Mo"),
        line("AmazonS3", "TimedStorage-ByteHrs", 40, 0.92, unit="GB-Mo"),
        line("AWSDataTransfer", "DataTransfer-Out-Bytes", 2, 0.18, unit="GB"),
        line("AWSLambda", "Request", 100000, 0.02, unit="Requests"),
    )


ALL_RULES = [always_on_servers, idle_public_ip, nat_gateway, snapshot_buildup, forgotten_region,
             old_generation_instances, large_s3_standard, high_data_transfer]


# --- General ----------------------------------------------------------------

def test_all_rules_registered():
    assert set(ALL_RULES) <= set(RULES)
    assert len(RULES) == len(ALL_RULES)


@pytest.mark.parametrize("rule_func", ALL_RULES, ids=lambda f: f.__name__)
def test_rule_is_silent_on_clean_bill(rule_func, clean_ctx):
    assert rule_func(clean_ctx) == []


@pytest.mark.parametrize("rule_func", ALL_RULES, ids=lambda f: f.__name__)
def test_rule_fires_on_sample_with_complete_finding(rule_func, sample_ctx):
    findings = rule_func(sample_ctx)
    assert findings, f"{rule_func.__name__} should fire on the sample bill"
    for finding in findings:
        assert finding.title and finding.explanation
        assert finding.confidence in ("high", "medium", "low")
        assert len(finding.steps) >= 2
        if finding.monthly_saving is not None:
            assert finding.monthly_saving > 0
            # Savings are always presented as estimates, never as facts.
            assert "estimated" in finding.explanation


def test_finding_rejects_unknown_confidence():
    with pytest.raises(ValueError):
        Finding(rule_id="x", title="x", explanation="x", confidence="certain", steps=[])


def test_run_rules_sorted_by_saving(sample_ctx):
    savings = [f.monthly_saving for f in run_rules(sample_ctx)]
    known = [s for s in savings if s is not None]
    assert known == sorted(known, reverse=True)
    assert savings[-1] is None  # "can't estimate" findings go last


def test_run_rules_survives_broken_rule(sample_ctx, monkeypatch):
    def broken(ctx):
        raise RuntimeError("boom")
    monkeypatch.setattr("rules.base.RULES", [broken, idle_public_ip])
    from rules.base import run_rules as run
    assert [f.rule_id for f in run(sample_ctx)] == ["idle_ip"]


def test_total_saving_does_not_double_count(sample_ctx):
    findings = run_rules(sample_ctx)
    naive = sum(f.monthly_saving or 0 for f in findings)
    total = total_saving(findings, sample_ctx)
    # The Sydney server is both always-on and in a forgotten region: counted once, capped at its cost.
    assert total < naive
    assert total == pytest.approx(106.09, abs=0.01)
    assert total <= sample_ctx.total_cost


def test_sample_main_region(sample_ctx):
    assert sample_ctx.main_region == "us-east-1"
    assert sample_ctx.months == 1


# --- Always-on server -------------------------------------------------------------

def test_always_on_sample(sample_ctx):
    findings = {f.resources[0]: f for f in always_on_servers(sample_ctx)}
    assert set(findings) == {"i-0a1b2c3d4e5f60001", "i-0f9e8d7c6b5a40001"}
    main = findings["i-0a1b2c3d4e5f60001"]
    assert main.monthly_saving == pytest.approx(71.42 * (1 - WORKING_HOURS_PER_MONTH / 744), abs=0.01)
    assert main.confidence == "medium"
    assert "m5.large" in main.title


@pytest.mark.parametrize("hours, fires", [(699, False), (700, True), (744, True)])
def test_always_on_threshold(hours, fires):
    ctx = context(line("AmazonEC2", "BoxUsage:t3.small", hours, hours * 0.0208, resource="i-0000000000000000b"))
    assert bool(always_on_servers(ctx)) is fires


def test_always_on_is_per_server_not_per_group():
    # Two servers of 400 hours each: 800 hours in the group, but neither is always on.
    ctx = context(
        line("AmazonEC2", "BoxUsage:t3.small", 400, 8.32, resource="i-0000000000000000c"),
        line("AmazonEC2", "BoxUsage:t3.small", 400, 8.32, resource="i-0000000000000000d"),
    )
    assert always_on_servers(ctx) == []


def test_always_on_two_month_bill_uses_monthly_figures():
    ctx = context(
        line("AmazonEC2", "BoxUsage:t3.small", 744, 15.48, resource="i-0000000000000000e", start="2025-10-01T00:00:00Z"),
        line("AmazonEC2", "BoxUsage:t3.small", 720, 14.98, resource="i-0000000000000000e", start="2025-11-01T00:00:00Z"),
    )
    assert ctx.months == 2
    [finding] = always_on_servers(ctx)
    monthly_cost = (15.48 + 14.98) / 2
    assert finding.monthly_saving == pytest.approx(monthly_cost * (1 - 217 / 732), abs=0.01)


# --- Idle public IP ---------------------------------------------------------------

def test_idle_ip_saves_full_cost(sample_ctx):
    [finding] = idle_public_ip(sample_ctx)
    assert finding.monthly_saving == pytest.approx(3.72, abs=0.01)
    assert finding.confidence == "high"


def test_idle_ip_matches_elastic_ip_usage_type():
    ctx = context(line("AmazonEC2", "EUW2-ElasticIP:IdleAddress", 100, 0.5, region="eu-west-2"))
    assert idle_public_ip(ctx)[0].monthly_saving == pytest.approx(0.5)


def test_in_use_ip_is_not_flagged():
    ctx = context(line("AmazonVPC", "PublicIPv4:InUseAddress", 744, 3.72))
    assert idle_public_ip(ctx) == []


# --- NAT gateway ------------------------------------------------------------------

def test_nat_gateway_sample(sample_ctx):
    [finding] = nat_gateway(sample_ctx)
    assert finding.monthly_saving == pytest.approx(33.48, abs=0.01)
    assert finding.confidence == "medium"
    assert "$35.73" in finding.explanation  # hours + data processed
    assert any("endpoint" in step for step in finding.steps)


def test_nat_gateway_data_only_is_ignored():
    assert nat_gateway(context(line("AmazonEC2", "NatGateway-Bytes", 10, 0.45, unit="GB"))) == []


# --- Snapshots ---------------------------------------------------------------------

def test_snapshots_sample(sample_ctx):
    [finding] = snapshot_buildup(sample_ctx)
    assert finding.monthly_saving == pytest.approx(4.25, abs=0.01)
    assert finding.confidence == "medium"


@pytest.mark.parametrize("cost, fires", [(5.00, False), (5.01, True)])
def test_snapshot_threshold(cost, fires):
    ctx = context(line("AmazonEC2", "EBS:SnapshotUsage", cost / 0.05, cost, unit="GB-Mo"))
    assert bool(snapshot_buildup(ctx)) is fires


def test_snapshots_summed_across_regions():
    ctx = context(
        line("AmazonEC2", "EBS:SnapshotUsage", 60, 3.0, unit="GB-Mo"),
        line("AmazonEC2", "EUW2-EBS:SnapshotUsage", 60, 3.0, region="eu-west-2", unit="GB-Mo"),
    )
    assert snapshot_buildup(ctx)[0].monthly_saving == pytest.approx(3.0)


# --- Forgotten region ---------------------------------------------------------------

def test_forgotten_region_sample(sample_ctx):
    [finding] = forgotten_region(sample_ctx)
    assert "Sydney" in finding.title
    assert finding.monthly_saving == pytest.approx(10.59, abs=0.01)
    assert finding.confidence == "medium"


def test_region_with_ten_percent_or_more_is_not_flagged():
    ctx = context(
        line("AmazonEC2", "BoxUsage:t3.micro", 300, 90.0),
        line("AmazonEC2", "APS2-BoxUsage:t3.micro", 300, 10.0, region="ap-southeast-2"),
    )
    assert forgotten_region(ctx) == []


def test_single_region_and_global_charges_not_flagged():
    ctx = context(
        line("AmazonEC2", "BoxUsage:t3.micro", 300, 90.0),
        line("AmazonRoute53", "HostedZone", 1, 0.5, region="global", unit="HostedZone"),
    )
    assert forgotten_region(ctx) == []


# --- Old generation -----------------------------------------------------------------

def test_old_generation_sample(sample_ctx):
    [finding] = old_generation_instances(sample_ctx)
    assert "t2.medium" in finding.title
    assert "t3.medium" in finding.explanation
    # 372 hours is not always-on, so the whole cost is the basis.
    assert finding.monthly_saving == pytest.approx(17.26 * 0.10, abs=0.01)
    assert finding.confidence == "medium"


@pytest.mark.parametrize("instance_type, fires", [
    ("m4.large", True), ("c4.xlarge", True), ("t2.micro", True), ("r4.large", True),
    ("m5.large", False), ("t3.micro", False), ("t4g.small", False), ("c7i.large", False),
])
def test_old_generation_families(instance_type, fires):
    ctx = context(line("AmazonEC2", f"BoxUsage:{instance_type}", 100, 10.0, resource="i-0000000000000000f"))
    assert bool(old_generation_instances(ctx)) is fires


def test_old_generation_always_on_counts_only_scheduled_hours():
    ctx = context(line("AmazonEC2", "BoxUsage:m4.large", 744, 74.4, resource="i-00000000000000010"))
    [finding] = old_generation_instances(ctx)
    assert finding.monthly_saving == pytest.approx(74.4 * 217 / 744 * 0.10, abs=0.01)


# --- S3 Standard storage --------------------------------------------------------------

def test_s3_sample(sample_ctx):
    [finding] = large_s3_standard(sample_ctx)
    assert finding.monthly_saving == pytest.approx(5.75 * 0.30, abs=0.01)
    assert finding.confidence == "low"
    assert "Intelligent-Tiering" in finding.explanation


@pytest.mark.parametrize("gb, fires", [(100, False), (101, True)])
def test_s3_threshold(gb, fires):
    ctx = context(line("AmazonS3", "TimedStorage-ByteHrs", gb, gb * 0.023, unit="GB-Mo"))
    assert bool(large_s3_standard(ctx)) is fires


def test_s3_other_storage_classes_ignored():
    ctx = context(line("AmazonS3", "TimedStorage-SIA-ByteHrs", 500, 6.25, unit="GB-Mo"))
    assert large_s3_standard(ctx) == []


# --- Data transfer ---------------------------------------------------------------------

def test_data_transfer_sample(sample_ctx):
    [finding] = high_data_transfer(sample_ctx)
    assert finding.monthly_saving is None  # we don't guess a number here
    assert finding.confidence == "low"
    assert "17%" in finding.explanation


def test_data_transfer_below_threshold():
    ctx = context(
        line("AmazonEC2", "BoxUsage:t3.micro", 300, 90.0),
        line("AWSDataTransfer", "DataTransfer-Out-Bytes", 111, 10.0, unit="GB"),
    )
    assert high_data_transfer(ctx) == []


def test_data_transfer_includes_inter_region():
    ctx = context(
        line("AmazonEC2", "BoxUsage:t3.micro", 300, 50.0),
        line("AWSDataTransfer", "USE1-APS2-AWS-Out-Bytes", 2500, 50.0, unit="GB"),
    )
    assert high_data_transfer(ctx)
