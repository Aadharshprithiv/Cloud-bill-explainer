"""Forgotten region: a small amount of spend in a region other than the main one."""

from parsers.names import plain_region
from rules.base import BillContext, Finding, rule

SMALL_REGION_SHARE = 0.10
MIN_COST = 0.01


@rule
def forgotten_region(ctx: BillContext) -> list[Finding]:
    main = ctx.main_region
    if main is None or ctx.total_cost <= 0:
        return []
    findings = []
    regional = ctx.groups[~ctx.groups["region"].isin([main, "global"])]
    for region, groups in regional.groupby("region"):
        cost = ctx.per_month(groups["cost"].sum())
        share = groups["cost"].sum() / ctx.total_cost
        if cost < MIN_COST or share >= SMALL_REGION_SHARE:
            continue
        items = ", ".join(groups.sort_values("cost", ascending=False)["usage_name"].head(3))
        findings.append(Finding(
            rule_id="forgotten_region",
            title=f"Small charges in {plain_region(region)}",
            explanation=(
                f"Most of your spending is in {plain_region(main)}, but {ctx.money(cost)} "
                f"({share:.0%} of your bill) comes from {plain_region(region)} ({region}). "
                f"Small charges in another location are often something left running after a test, "
                f"here: {items}. If it's not needed, removing it would save an estimated "
                f"{ctx.money(cost)} a month."
            ),
            confidence="medium",
            steps=[
                f"In the AWS console, use the region menu (top right) to switch to {plain_region(region)}.",
                "Check EC2 (instances, volumes, snapshots, Elastic IPs), S3 and any other services listed here.",
                "Delete anything you don't recognise or no longer need. Take a snapshot first if unsure.",
            ],
            saving_by_group={int(g.id): round(ctx.per_month(g.cost), 2) for g in groups.itertuples()
                             if g.cost > 0},
        ))
    return findings
