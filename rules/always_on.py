"""Always-on server: an EC2 instance charged 700+ hours in the month."""

from rules.base import BillContext, Finding, instance_usage, rule

ALWAYS_ON_HOURS = 700
# Weekdays, roughly 8am-6pm: 21.7 weekdays a month x 10 hours.
WORKING_HOURS_PER_MONTH = 217


def scheduled_cost(cost: float, hours: float) -> float:
    """What a server would cost if it only ran during working hours."""
    if hours <= 0:
        return cost
    return cost * min(1.0, WORKING_HOURS_PER_MONTH / hours)


@rule
def always_on_servers(ctx: BillContext) -> list[Finding]:
    findings = []
    servers = instance_usage(ctx)
    for server in servers[servers["hours"] >= ALWAYS_ON_HOURS].itertuples():
        saving = server.cost - scheduled_cost(server.cost, server.hours)
        if saving <= 0:
            continue
        size = f" (size {server.instance_type})" if server.instance_type else ""
        findings.append(Finding(
            rule_id="always_on",
            title=f"A server{size} runs 24/7",
            explanation=(
                f"This virtual server in {server.region_name} was switched on for about "
                f"{server.hours:,.0f} hours this month, which is day and night including weekends. "
                f"It cost {ctx.money(server.cost)}. If it's only needed during working hours "
                f"(Monday to Friday, about 8am to 6pm), stopping it the rest of the time could save "
                f"an estimated {ctx.money(saving)} a month. If it runs a website or anything people "
                f"use at night, it needs to stay on."
            ),
            confidence="medium",
            steps=[
                "Check whether anyone or anything needs this server outside working hours.",
                "If not, set up a schedule to stop and start it automatically, for example with "
                "AWS Instance Scheduler or an EventBridge Scheduler rule.",
                "Or stop it by hand in the EC2 console (Instances, select it, Instance state, Stop).",
                "Note: its storage disk is still charged while the server is stopped, but that is "
                "usually much cheaper than the server itself.",
            ],
            saving_by_group={server.group_id: round(saving, 2)},
            resources=[server.resource] if server.resource else [],
        ))
    return findings
