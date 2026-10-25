"""Idle public IP: a public IPv4 address you pay for but isn't attached to anything."""

from rules.base import BillContext, Finding, rule


@rule
def idle_public_ip(ctx: BillContext) -> list[Finding]:
    idle = ctx.groups_matching(r"IdleAddress")
    idle = idle[idle["cost"] > 0]
    if idle.empty:
        return []
    cost = ctx.per_month(idle["cost"].sum())
    hours = ctx.per_month(idle["usage_amount"].sum())
    return [Finding(
        rule_id="idle_ip",
        title="You're paying for an unused public IP address",
        explanation=(
            f"AWS charges for public IP addresses (the address the internet uses to reach a server) "
            f"even when they aren't attached to anything. You had about {hours:,.0f} hours of unused "
            f"addresses, costing {ctx.money(cost)}. Releasing them would save an estimated "
            f"{ctx.money(cost)} a month."
        ),
        confidence="high",
        steps=[
            "Open the EC2 console and go to Network & Security, then Elastic IPs.",
            "Look for addresses with no 'Associated instance'.",
            "Make sure no domain name or setting still points at that address.",
            "Select it and choose Actions, then Release Elastic IP addresses.",
        ],
        saving_by_group={int(g.id): round(ctx.per_month(g.cost), 2) for g in idle.itertuples()},
    )]
