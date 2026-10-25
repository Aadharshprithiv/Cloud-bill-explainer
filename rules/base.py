"""Shared pieces for the waste rules: the Finding result, the bill context and the registry.

To add a rule: create a module in rules/, write a function that takes a
BillContext and returns a list of Findings, decorate it with @rule, and import
the module in rules/__init__.py.
"""

import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, field

import pandas as pd

from explain.templates import money
from parsers.cur_parser import ParsedBill
from parsers.grouping import group_line_items
from parsers.names import split_usage_prefix

log = logging.getLogger(__name__)

CONFIDENCE_LEVELS = ("high", "medium", "low")


@dataclass
class Finding:
    rule_id: str
    title: str
    explanation: str
    confidence: str
    steps: list[str]
    # Estimated monthly saving per charge group id. Empty when we can't estimate.
    saving_by_group: dict[int, float] = field(default_factory=dict)
    # Resources the user can look for in the AWS console (shown in the UI only).
    resources: list[str] = field(default_factory=list)
    # Charges the finding is about but doesn't claim a saving for.
    related_group_ids: list[int] = field(default_factory=list)

    def __post_init__(self):
        if self.confidence not in CONFIDENCE_LEVELS:
            raise ValueError(f"confidence must be one of {CONFIDENCE_LEVELS}")

    @property
    def monthly_saving(self) -> float | None:
        """Estimated monthly saving, or None when it can't be estimated."""
        if not self.saving_by_group:
            return None
        return round(sum(self.saving_by_group.values()), 2)

    @property
    def group_ids(self) -> list[int]:
        """Every charge this finding is about."""
        return list(dict.fromkeys([*self.saving_by_group, *self.related_group_ids]))


@dataclass
class BillContext:
    items: pd.DataFrame   # line items (canonical columns from the parser)
    groups: pd.DataFrame  # grouped charges (see parsers.grouping)
    total_cost: float
    currency: str = "USD"
    months: int = 1       # calendar months covered, used to turn totals into monthly figures

    @property
    def main_region(self) -> str | None:
        regional = self.groups[self.groups["region"] != "global"]
        if regional.empty:
            return None
        return regional.groupby("region")["cost"].sum().idxmax()

    def money(self, amount: float) -> str:
        return money(amount, self.currency)

    def per_month(self, amount: float) -> float:
        return amount / self.months

    def groups_matching(self, pattern: str) -> pd.DataFrame:
        """Groups whose usage type (without region prefix) matches a regex."""
        regex = re.compile(pattern)
        mask = self.groups["usage_type"].map(lambda u: bool(regex.search(split_usage_prefix(u)[1])))
        return self.groups[mask]


def build_context(bill: ParsedBill) -> BillContext:
    months = bill.items["usage_start"].dropna().dt.strftime("%Y-%m").nunique()
    return BillContext(
        items=bill.items,
        groups=group_line_items(bill.items),
        total_cost=bill.total_cost,
        currency=bill.currency,
        months=max(1, months),
    )


def instance_usage(ctx: BillContext) -> pd.DataFrame:
    """Hours and cost per EC2 instance (BoxUsage), one row per resource.

    Rows without a resource ID are treated as one instance per usage type and region.
    Columns: group_id, resource, instance_type, region, region_name, hours, cost.
    """
    columns = ["group_id", "resource", "instance_type", "region", "region_name", "hours", "cost"]
    items = ctx.items
    is_box = items["usage_type"].map(lambda u: split_usage_prefix(u)[1].startswith("BoxUsage"))
    box = items[is_box & (items["product_code"].isin(["AmazonEC2", ""]))]
    if box.empty:
        return pd.DataFrame(columns=columns)

    group_ids = {
        (row.product_code, row.usage_type, row.region): (row.id, row.instance_type, row.region_name)
        for row in ctx.groups.itertuples()
    }
    box = box.assign(resource=box["resource_id"].where(box["resource_id"] != "", "(no ID)"))
    per_resource = (
        box.groupby(["product_code", "usage_type", "region", "resource"], sort=False)
        .agg(hours=("usage_amount", "sum"), cost=("cost", "sum"))
        .reset_index()
    )
    rows = []
    for row in per_resource.itertuples():
        group_id, instance_type, region_name = group_ids[(row.product_code, row.usage_type, row.region)]
        rows.append({
            "group_id": group_id,
            "resource": "" if row.resource == "(no ID)" else row.resource,
            "instance_type": instance_type,
            "region": row.region,
            "region_name": region_name,
            "hours": ctx.per_month(row.hours),
            "cost": ctx.per_month(row.cost),
        })
    return pd.DataFrame(rows, columns=columns)


RuleFunc = Callable[[BillContext], list[Finding]]
RULES: list[RuleFunc] = []


def rule(func: RuleFunc) -> RuleFunc:
    """Register a waste rule."""
    RULES.append(func)
    return func


def run_rules(ctx: BillContext) -> list[Finding]:
    """Run every registered rule. Findings with the biggest saving come first."""
    findings = []
    for rule_func in RULES:
        try:
            findings.extend(rule_func(ctx))
        except Exception:  # one broken rule must not break the whole report
            log.exception("Waste rule %s failed", rule_func.__name__)
    return sorted(findings, key=lambda f: f.monthly_saving if f.monthly_saving is not None else -1,
                  reverse=True)


def total_saving(findings: list[Finding], ctx: BillContext) -> float:
    """Total estimated monthly saving without double counting.

    Several findings can point at the same charge (an always-on server that is
    also in a forgotten region). A charge can never save more than it costs.
    """
    cost_by_group = {gid: ctx.per_month(cost) for gid, cost in zip(ctx.groups["id"], ctx.groups["cost"])}
    per_group: dict[int, float] = {}
    for finding in findings:
        for group_id, saving in finding.saving_by_group.items():
            per_group[group_id] = per_group.get(group_id, 0.0) + saving
    return round(sum(min(saving, max(cost_by_group.get(gid, 0.0), 0.0))
                     for gid, saving in per_group.items()), 2)
