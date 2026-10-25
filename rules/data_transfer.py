"""High data transfer: data sent out of AWS is a big share of the bill."""

from rules.base import BillContext, Finding, rule

HIGH_SHARE = 0.15


@rule
def high_data_transfer(ctx: BillContext) -> list[Finding]:
    if ctx.total_cost <= 0:
        return []
    transfer = ctx.groups_matching(r"Out-Bytes")
    cost = transfer["cost"].sum()
    share = cost / ctx.total_cost
    if share <= HIGH_SHARE:
        return []
    gb = ctx.per_month(transfer["usage_amount"].sum())
    return [Finding(
        rule_id="high_data_transfer",
        title="Sending data out of AWS is a big part of your bill",
        explanation=(
            f"AWS charges when data leaves its network, for example when people download files or "
            f"visit your website. You sent about {gb:,.0f} GB out, costing {ctx.money(ctx.per_month(cost))}, "
            f"which is {share:.0%} of your bill. That can be normal for a busy site, but it's worth "
            f"checking where the traffic goes. We can't estimate a saving without knowing more."
        ),
        confidence="low",
        steps=[
            "Find out what is sending the data: big downloads, images and videos, backups to "
            "another provider, or traffic between AWS regions.",
            "Put a CDN such as Amazon CloudFront in front of your website or files; its first 1 TB a "
            "month is free and it is often cheaper than sending data directly.",
            "Compress images and files, and avoid copying data between regions unless needed.",
        ],
        related_group_ids=[int(g.id) for g in transfer.itertuples()],
    )]
