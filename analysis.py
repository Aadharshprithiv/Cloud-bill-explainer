"""Run the whole pipeline on an uploaded bill and shape the result for the templates."""

from explain import explain_groups
from explain.templates import format_quantity
from parsers.cur_parser import parse_cur
from parsers.grouping import cost_by_service
from rules import build_context, run_rules, total_saving

CHART_MAX_SLICES = 6  # the rest fold into "Other"


def _chart_slices(services: list[tuple[str, float]]) -> list[dict]:
    positive = [(name, cost) for name, cost in services if cost > 0]
    slices = [{"label": name, "cost": round(cost, 2)} for name, cost in positive[:CHART_MAX_SLICES]]
    rest = sum(cost for _, cost in positive[CHART_MAX_SLICES:])
    if rest > 0:
        slices.append({"label": "Other", "cost": round(rest, 2)})
    return slices


def analyze(data: bytes) -> dict:
    """Parse, group, run rules and explain. Raises BillParseError for bad files."""
    bill = parse_cur(data)
    ctx = build_context(bill)
    findings = run_rules(ctx)
    explanations, explanation_source = explain_groups(ctx.groups, bill.currency)

    flagged = {gid for f in findings for gid in f.group_ids}
    charges = []
    for group in ctx.groups.to_dict("records"):
        charges.append({
            **group,
            "id": int(group["id"]),
            "quantity_text": format_quantity(group["usage_amount"], group["unit"]),
            "explanation": explanations.get(int(group["id"]), ""),
            "flagged": int(group["id"]) in flagged,
        })

    return {
        "currency": bill.currency,
        "total_cost": bill.total_cost,
        "months": ctx.months,
        "period_start": bill.period_start,
        "period_end": bill.period_end,
        "warnings": bill.warnings,
        "total_saving": total_saving(findings, ctx),
        "findings": [{
            "rule_id": f.rule_id,
            "title": f.title,
            "explanation": f.explanation,
            "confidence": f.confidence,
            "steps": f.steps,
            "saving": f.monthly_saving,
            "resources": f.resources,
        } for f in findings],
        "charges": charges,
        "chart": _chart_slices(cost_by_service(ctx.groups)),
        "explanation_source": explanation_source,
    }
