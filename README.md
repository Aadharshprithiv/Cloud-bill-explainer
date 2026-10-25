# Cloud-bill-explainer
The problem it solves  Cloud bills are confusing. A line on an AWS bill looks like this:  USE2-BoxUsage:t3.medium — 744 Hrs — $31.00  Most students, freelancers and small business owners don’t know what that means, so they pay it and move on. Often they’re paying for things they forgot about or don’t need.

## Status

Work in progress. Done: stage 1 (sample data, CSV parser, tests) and stage 2 (waste rules, tests).

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
- `rules/` – waste detection rules, one module per rule (see below)
- `tests/` – pytest tests

## Waste rules

Every saving is an **estimate** and is labelled as one.

| Rule | Triggers when | Estimated monthly saving | Confidence |
|---|---|---|---|
| Always-on server | an EC2 instance runs 700+ hours in a month | cost of hours outside weekday working hours (217 h/month kept) | medium |
| Idle public IP | usage type contains `IdleAddress` | full cost | high |
| NAT gateway | `NatGateway-Hours` present | up to the hourly NAT charge | medium |
| Snapshot buildup | EBS snapshots cost over $5/month | 50% of snapshot cost | medium |
| Forgotten region | a non-main region with under 10% of spend | that region's full cost | medium |
| Old-generation instance | t1/t2, m1/m3/m4, c1/c3/c4, r3/r4 and similar | ~10% of instance cost (after any schedule) | medium |
| Large S3 Standard | over 100 GB-month in S3 Standard | ~30% of that storage cost | low |
| High data transfer | data sent out is over 15% of the bill | not estimated | low |

The total potential saving never counts the same charge twice: a charge flagged
by several rules can save at most what it costs.

### Adding a rule

Create `rules/my_rule.py` with a function that takes a `BillContext` and returns
a list of `Finding`s, decorate it with `@rule`, and import the module in
`rules/__init__.py`. Add tests in `tests/test_rules.py`.
