# Cloud-bill-explainer
The problem it solves  Cloud bills are confusing. A line on an AWS bill looks like this:  USE2-BoxUsage:t3.medium — 744 Hrs — $31.00  Most students, freelancers and small business owners don’t know what that means, so they pay it and move on. Often they’re paying for things they forgot about or don’t need.

## Status

Work in progress. Stage 1 (sample data, CSV parser, tests) is done.

## Setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m pytest
```

## Project layout (so far)

- `parsers/cur_parser.py` – reads and validates an AWS Cost and Usage Report CSV (legacy and CUR 2.0 headers), in memory, with friendly errors
- `parsers/grouping.py` – totals charges per service + usage type + region
- `parsers/names.py` – plain-English names for services, usage types and regions
- `parsers/sanitize.py` – strips account IDs, ARNs and resource IDs from text
- `sample_data/sample_aws_bill.csv` – one month of fake data (regenerate with `python sample_data/generate_sample_bill.py`)
- `tests/` – pytest tests
