"""Snapshot buildup: old disk backups that keep costing money."""

from rules.base import BillContext, Finding, rule

SNAPSHOT_THRESHOLD = 5.0  # per month
ESTIMATED_DELETABLE_SHARE = 0.5


@rule
def snapshot_buildup(ctx: BillContext) -> list[Finding]:
    snaps = ctx.groups_matching(r"^EBS:Snapshot")
    cost = ctx.per_month(snaps["cost"].sum())
    if cost <= SNAPSHOT_THRESHOLD:
        return []
    saving = cost * ESTIMATED_DELETABLE_SHARE
    gb = ctx.per_month(snaps["usage_amount"].sum())
    return [Finding(
        rule_id="snapshot_buildup",
        title="Old disk backups are adding up",
        explanation=(
            f"Snapshots are saved copies of your servers' disks, like photos of the disk at a moment in "
            f"time. You're storing about {gb:,.0f} GB of them, costing {ctx.money(cost)} a month. "
            f"Old snapshots are easy to forget. If around half are no longer needed, deleting them "
            f"would save an estimated {ctx.money(saving)} a month."
        ),
        confidence="medium",
        steps=[
            "Open the EC2 console and go to Elastic Block Store, then Snapshots.",
            "Sort by 'Started' to find the oldest snapshots.",
            "Keep the ones you'd need to restore a server; delete the rest (Actions, Delete snapshot).",
            "To stop them piling up, use Amazon Data Lifecycle Manager to keep only the last few automatically.",
        ],
        saving_by_group={int(g.id): round(ctx.per_month(g.cost) * ESTIMATED_DELETABLE_SHARE, 2)
                         for g in snaps.itertuples()},
    )]
