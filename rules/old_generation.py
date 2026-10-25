"""Old-generation instances: newer instance families are usually cheaper for similar performance."""

import re

from rules.always_on import ALWAYS_ON_HOURS, scheduled_cost
from rules.base import BillContext, Finding, instance_usage, rule

# Old family -> suggested current family
NEWER_FAMILY = {
    "t1": "t3", "t2": "t3", "m1": "m7i", "m3": "m7i", "m4": "m7i",
    "c1": "c7i", "c3": "c7i", "c4": "c7i", "r3": "r7i", "r4": "r7i",
    "i2": "i4i", "d2": "d3", "g2": "g4dn", "g3": "g4dn", "p2": "g4dn", "x1": "x2idn",
}
ESTIMATED_SAVING_SHARE = 0.10


def remaining_cost(cost: float, hours: float) -> float:
    """Cost left after the always-on rule's schedule, so the two suggestions
    don't claim the same money twice."""
    return scheduled_cost(cost, hours) if hours >= ALWAYS_ON_HOURS else cost


def instance_family(instance_type: str) -> str:
    match = re.match(r"^([a-z]+\d)", instance_type or "")
    return match.group(1) if match else ""


@rule
def old_generation_instances(ctx: BillContext) -> list[Finding]:
    servers = instance_usage(ctx)
    if servers.empty:
        return []
    servers = servers.assign(family=servers["instance_type"].map(instance_family))
    findings = []
    for instance_type, group in servers[servers["family"].isin(NEWER_FAMILY.keys())].groupby("instance_type"):
        family = instance_family(instance_type)
        newer = NEWER_FAMILY[family] + instance_type[len(family):]
        cost = group["cost"].sum()
        saving_by_group: dict[int, float] = {}
        for s in group.itertuples():
            saving_by_group[int(s.group_id)] = (saving_by_group.get(int(s.group_id), 0.0)
                                                + remaining_cost(s.cost, s.hours) * ESTIMATED_SAVING_SHARE)
        saving = sum(saving_by_group.values())
        if saving <= 0:
            continue
        findings.append(Finding(
            rule_id="old_generation",
            title=f"Older server type: {instance_type}",
            explanation=(
                f"You're using {instance_type}, an older generation of virtual server. It cost "
                f"{ctx.money(cost)} this month. Newer types such as {newer} usually cost the same or "
                f"less for similar or better performance, like swapping an old car for a newer, more "
                f"efficient model. Switching could save an estimated {ctx.money(saving)} a month "
                f"(about 10%; the exact amount depends on the type you choose)."
            ),
            confidence="medium",
            steps=[
                f"Check the price of {newer} in your region on the EC2 pricing page and compare it with {instance_type}.",
                "Stop the server, then choose Actions, Instance settings, Change instance type.",
                f"Pick {newer} (or a similar current type) and start it again.",
                "Test that everything still works. Very old servers may need updated drivers first.",
            ],
            saving_by_group={gid: round(value, 2) for gid, value in saving_by_group.items()},
            resources=[r for r in group["resource"] if r],
        ))
    return findings
