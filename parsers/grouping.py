"""Group line items into one row per service + usage type + region."""

import pandas as pd

from parsers.names import (instance_type_from_usage, plain_region, plain_service,
                           plain_usage_type)

GROUP_KEYS = ["product_code", "usage_type", "region"]

GROUP_COLUMNS = ["id", *GROUP_KEYS, "usage_amount", "unit", "cost", "line_count",
                 "resource_count", "description", "instance_type",
                 "service_name", "usage_name", "region_name"]


def _first_non_empty(values: pd.Series) -> str:
    for value in values:
        if value:
            return value
    return ""


def group_line_items(items: pd.DataFrame) -> pd.DataFrame:
    """Total cost and usage per group, sorted by cost (highest first).

    Resource IDs are reduced to a count, so the result holds no identifiers.
    """
    if items.empty:
        return pd.DataFrame(columns=GROUP_COLUMNS)

    grouped = (
        items.groupby(GROUP_KEYS, sort=False, dropna=False)
        .agg(
            usage_amount=("usage_amount", "sum"),
            unit=("unit", _first_non_empty),
            cost=("cost", "sum"),
            line_count=("cost", "size"),
            resource_count=("resource_id", lambda ids: ids[ids != ""].nunique()),
            description=("description", _first_non_empty),
            instance_type=("instance_type", _first_non_empty),
        )
        .reset_index()
        .sort_values("cost", ascending=False, kind="stable")
        .reset_index(drop=True)
    )
    grouped["instance_type"] = [
        inst or instance_type_from_usage(usage) or ""
        for inst, usage in zip(grouped["instance_type"], grouped["usage_type"])
    ]
    grouped["service_name"] = grouped["product_code"].map(plain_service)
    grouped["usage_name"] = grouped["usage_type"].map(plain_usage_type)
    grouped["region_name"] = grouped["region"].map(plain_region)
    grouped.insert(0, "id", range(1, len(grouped) + 1))
    return grouped[GROUP_COLUMNS]


def cost_by_service(groups: pd.DataFrame) -> list[tuple[str, float]]:
    """[(plain service name, total cost), ...] highest first, for the donut chart."""
    if groups.empty:
        return []
    totals = groups.groupby("service_name")["cost"].sum().sort_values(ascending=False)
    return [(name, float(cost)) for name, cost in totals.items()]
