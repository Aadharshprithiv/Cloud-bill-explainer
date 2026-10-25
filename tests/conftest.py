import csv
import io
from pathlib import Path

import pytest

SAMPLE_PATH = Path(__file__).resolve().parent.parent / "sample_data" / "sample_aws_bill.csv"


@pytest.fixture(scope="session")
def sample_bytes() -> bytes:
    return SAMPLE_PATH.read_bytes()


def make_csv(rows: list[dict], columns: list[str] | None = None) -> bytes:
    """Build CSV bytes from a list of dicts (missing keys become empty cells)."""
    columns = columns or list(dict.fromkeys(key for row in rows for key in row))
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=columns, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)
    return out.getvalue().encode()
