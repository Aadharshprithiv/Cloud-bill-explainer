"""Read and validate an AWS Cost and Usage Report (CUR) CSV.

Works entirely in memory. Accepts both the legacy CUR headers
("lineItem/UnblendedCost") and CUR 2.0 / Data Exports headers
("line_item_unblended_cost").
"""

import io
from dataclasses import dataclass, field

import pandas as pd

from parsers.names import region_from_usage_type

MAX_BYTES = 20 * 1024 * 1024

# canonical name -> header variants we accept (compared case-insensitively)
COLUMN_ALIASES = {
    "product_code": ["lineItem/ProductCode", "line_item_product_code"],
    "usage_type": ["lineItem/UsageType", "line_item_usage_type"],
    "usage_amount": ["lineItem/UsageAmount", "line_item_usage_amount"],
    "unit": ["pricing/unit", "pricing_unit"],
    "cost": ["lineItem/UnblendedCost", "line_item_unblended_cost"],
    "region": ["product/region", "product_region", "product/regionCode", "product_region_code"],
    "resource_id": ["lineItem/ResourceId", "line_item_resource_id"],
    "description": ["lineItem/LineItemDescription", "line_item_line_item_description"],
    "usage_start": ["lineItem/UsageStartDate", "line_item_usage_start_date"],
    "line_item_type": ["lineItem/LineItemType", "line_item_line_item_type"],
    "account_id": ["lineItem/UsageAccountId", "line_item_usage_account_id"],
    "currency": ["lineItem/CurrencyCode", "line_item_currency_code"],
    "operation": ["lineItem/Operation", "line_item_operation"],
    "instance_type": ["product/instanceType", "product_instance_type"],
}

# Shown to the user when something is missing (in the order they matter most).
FRIENDLY_COLUMN_NAMES = {
    "product_code": "service name (lineItem/ProductCode)",
    "usage_type": "usage type (lineItem/UsageType)",
    "usage_amount": "usage amount (lineItem/UsageAmount)",
    "unit": "unit (pricing/unit)",
    "region": "region (product/region)",
    "resource_id": "resource ID (lineItem/ResourceId)",
    "description": "description (lineItem/LineItemDescription)",
    "usage_start": "usage date (lineItem/UsageStartDate)",
}

TEXT_COLUMNS = ["product_code", "usage_type", "unit", "region", "resource_id",
                "description", "line_item_type", "account_id", "currency",
                "operation", "instance_type"]
NUMERIC_COLUMNS = ["usage_amount", "cost"]

EXPECTED_FILE_HELP = (
    "We expect an AWS Cost and Usage Report (CUR) saved as a .csv file. "
    "In the AWS console, open Billing and Cost Management, then Data Exports "
    "(or Cost and Usage Reports), and download the CSV for one month. "
    "It should have columns such as lineItem/ProductCode, lineItem/UsageType "
    "and lineItem/UnblendedCost."
)


class BillParseError(ValueError):
    """Raised with a message that is safe and friendly to show to the user."""

    def __init__(self, message: str):
        super().__init__(message)
        self.user_message = message


@dataclass
class ParsedBill:
    items: pd.DataFrame  # one row per line item, canonical column names
    total_cost: float
    currency: str = "USD"
    period_start: pd.Timestamp | None = None
    period_end: pd.Timestamp | None = None
    warnings: list[str] = field(default_factory=list)


def _read_csv(data: bytes) -> pd.DataFrame:
    if not data or not data.strip():
        raise BillParseError("The file is empty. " + EXPECTED_FILE_HELP)
    if len(data) > MAX_BYTES:
        raise BillParseError("The file is larger than 20 MB. Please upload one month of your bill at a time.")
    if b"\x00" in data[:4096]:
        # Zipped / gzipped / spreadsheet files all contain null bytes early on.
        raise BillParseError(
            "This looks like a compressed or spreadsheet file, not a plain CSV. "
            "If you downloaded a .csv.gz or .zip file, unzip it first. " + EXPECTED_FILE_HELP
        )
    for encoding in ("utf-8-sig", "latin-1"):
        try:
            return pd.read_csv(io.BytesIO(data), dtype=str, keep_default_na=False,
                               encoding=encoding, skipinitialspace=True)
        except UnicodeDecodeError:
            continue
        except (pd.errors.ParserError, pd.errors.EmptyDataError) as exc:
            raise BillParseError("We couldn't read this file as a CSV. " + EXPECTED_FILE_HELP) from exc
    raise BillParseError("We couldn't read the text in this file. " + EXPECTED_FILE_HELP)


def _guess_region(usage_type: str) -> str:
    """Region for a row with no product/region, from the usage-type prefix.

    "APS2-BoxUsage:t3.micro" -> ap-southeast-2. Usage types without a prefix
    belong to us-east-1; rows with no usage type at all (tax, fees) are global.
    """
    if not usage_type:
        return "global"
    return region_from_usage_type(usage_type) or "us-east-1"


def _map_columns(raw: pd.DataFrame) -> dict[str, str]:
    """Return {canonical_name: actual_header} for the columns we recognise."""
    by_lower = {str(col).strip().lower(): col for col in raw.columns}
    found = {}
    for canonical, aliases in COLUMN_ALIASES.items():
        for alias in aliases:
            if alias.lower() in by_lower:
                found[canonical] = by_lower[alias.lower()]
                break
    return found


def parse_cur(data: bytes) -> ParsedBill:
    """Parse raw CSV bytes into a ParsedBill, or raise BillParseError."""
    raw = _read_csv(data)
    columns = _map_columns(raw)

    if "cost" not in columns or not ({"product_code", "usage_type"} & columns.keys()):
        raise BillParseError(
            "This doesn't look like an AWS bill. We couldn't find the cost column "
            "(lineItem/UnblendedCost) and the service or usage columns. " + EXPECTED_FILE_HELP
        )
    if raw.empty:
        raise BillParseError("The file has column headers but no charges in it. " + EXPECTED_FILE_HELP)

    items = pd.DataFrame(index=raw.index)
    for canonical in COLUMN_ALIASES:
        if canonical in columns:
            items[canonical] = raw[columns[canonical]]
        else:
            items[canonical] = "" if canonical in TEXT_COLUMNS or canonical == "usage_start" else 0

    for col in TEXT_COLUMNS:
        items[col] = items[col].astype(str).str.strip()

    warnings = []
    for col in NUMERIC_COLUMNS:
        parsed = pd.to_numeric(items[col], errors="coerce")
        bad = int(parsed.isna().sum() - (items[col].astype(str).str.strip() == "").sum())
        if bad > 0 and col == "cost":
            warnings.append(f"{bad} row(s) had a cost we couldn't read and were counted as $0.")
        items[col] = parsed.fillna(0.0).astype(float)

    missing_region = items["region"] == ""
    items.loc[missing_region, "region"] = items.loc[missing_region, "usage_type"].map(_guess_region)

    # Rows like tax or credits often have no usage type; give them a readable label.
    no_usage = items["usage_type"] == ""
    items.loc[no_usage, "usage_type"] = items.loc[no_usage, "line_item_type"].where(
        items.loc[no_usage, "line_item_type"] != "", "Other")

    items["usage_start"] = pd.to_datetime(items["usage_start"], errors="coerce", utc=True)

    for col in ("product_code", "usage_type", "region", "usage_amount", "unit"):
        if col not in columns:
            warnings.append(f"The {FRIENDLY_COLUMN_NAMES[col]} column was missing, so some details may be less precise.")

    currencies = [c for c in items["currency"].unique() if c]
    currency = currencies[0] if len(currencies) == 1 else "USD"
    if len(currencies) > 1:
        warnings.append("The file contains more than one currency; totals are shown as-is.")

    dates = items["usage_start"].dropna()
    return ParsedBill(
        items=items,
        total_cost=float(items["cost"].sum()),
        currency=currency,
        period_start=dates.min() if not dates.empty else None,
        period_end=dates.max() if not dates.empty else None,
        warnings=warnings,
    )
