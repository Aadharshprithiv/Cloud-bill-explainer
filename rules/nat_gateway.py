"""NAT gateway: often one of the most expensive items on a small project."""

from rules.base import BillContext, Finding, rule


@rule
def nat_gateway(ctx: BillContext) -> list[Finding]:
    hours = ctx.groups_matching(r"^NatGateway-Hours")
    if hours.empty or hours["cost"].sum() <= 0:
        return []
    data = ctx.groups_matching(r"^NatGateway-Bytes")
    hourly_cost = ctx.per_month(hours["cost"].sum())
    total_cost = hourly_cost + ctx.per_month(data["cost"].sum())
    share = total_cost / ctx.per_month(ctx.total_cost) if ctx.total_cost > 0 else 0
    return [Finding(
        rule_id="nat_gateway",
        title="A NAT gateway is costing more than you might expect",
        explanation=(
            f"A NAT gateway lets servers in a private network reach the internet, a bit like a "
            f"shared front door. It is charged for every hour it exists, whether or not it's used, "
            f"plus for the data passing through it. Yours cost {ctx.money(total_cost)} this month "
            f"({share:.0%} of your bill). For small projects it is often one of the most "
            f"expensive items. Removing or replacing it could save up to an estimated "
            f"{ctx.money(hourly_cost)} a month in hourly charges."
        ),
        confidence="medium",
        steps=[
            "Check whether your servers really need to sit in a private network. For a small "
            "project, a server in a public subnet with a tight security group is often enough.",
            "If they only need to reach AWS services such as S3 or DynamoDB, use free "
            "'gateway VPC endpoints' instead.",
            "If you do need it, a small NAT instance (for example t4g.nano) costs a few dollars a month.",
            "Delete NAT gateways you no longer use in the VPC console (NAT gateways, Actions, Delete), "
            "then release the Elastic IP it used.",
        ],
        saving_by_group={int(g.id): round(ctx.per_month(g.cost), 2) for g in hours.itertuples()},
        related_group_ids=[int(g.id) for g in data.itertuples()],
    )]
