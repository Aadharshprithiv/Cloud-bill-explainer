"""Large S3 Standard storage: Intelligent-Tiering may be cheaper for rarely used files."""

from rules.base import BillContext, Finding, rule

LARGE_STORAGE_GB_MONTH = 100
ESTIMATED_SAVING_SHARE = 0.30


@rule
def large_s3_standard(ctx: BillContext) -> list[Finding]:
    storage = ctx.groups_matching(r"^TimedStorage-ByteHrs$")
    storage = storage[storage["product_code"].isin(["AmazonS3", ""])]
    gb = ctx.per_month(storage["usage_amount"].sum())
    if gb <= LARGE_STORAGE_GB_MONTH:
        return []
    cost = ctx.per_month(storage["cost"].sum())
    saving = cost * ESTIMATED_SAVING_SHARE
    return [Finding(
        rule_id="large_s3_standard",
        title="Lots of files in standard S3 storage",
        explanation=(
            f"You're keeping about {gb:,.0f} GB in S3 Standard, the storage class meant for files you "
            f"use often. It cost {ctx.money(cost)}. If many of these files are rarely opened (old "
            f"backups, archives), S3 Intelligent-Tiering moves them to cheaper storage automatically. "
            f"That could save an estimated {ctx.money(saving)} a month, but only if the data really "
            f"isn't accessed often."
        ),
        confidence="low",
        steps=[
            "Think about which buckets hold files you rarely open, such as backups or old uploads.",
            "In the S3 console, open the bucket, go to Management and create a lifecycle rule that "
            "moves objects to Intelligent-Tiering.",
            "Intelligent-Tiering has a tiny monitoring fee per file, so it suits larger files best.",
            "Delete files you don't need at all; that saves the most.",
        ],
        saving_by_group={int(g.id): round(ctx.per_month(g.cost) * ESTIMATED_SAVING_SHARE, 2)
                         for g in storage.itertuples()},
    )]
