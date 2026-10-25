# Cloud Bill Explainer

Cloud bills are confusing. A line on an AWS bill looks like this:

    USE2-BoxUsage:t3.medium — 744 Hrs — $31.00

Most students, freelancers and small business owners don't know what that means,
so they pay it and move on. Often they're paying for things they forgot about or
don't need.

Cloud Bill Explainer takes your AWS bill as a CSV file, explains every charge in
plain English, and flags likely waste with an **estimated** monthly saving and
simple steps to fix it.

## Features

- Upload an AWS Cost and Usage Report (CUR) CSV, or try the built-in sample bill
- Summary: total cost, estimated potential monthly saving, number of issues
- Donut chart of cost by service
- "Top savings" cards, biggest first, each with confidence level and fix steps
- Every charge explained in plain English; expand a row to see the original line
- Print-friendly "Download report" (use your browser's *Save as PDF*)
- AI explanations via the Anthropic API, with template explanations when no API key is set
- No database, no accounts; files are processed in memory and never stored or logged

## Setup

Requires Python 3.10+.

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

## Environment variables

| Variable | Required | Default | What it does |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | No | *(unset)* | Turns on AI explanations. Without it the app uses built-in template explanations. |
| `ANTHROPIC_MODEL` | No | `claude-opus-5-5` | Claude model used for explanations. |
| `ANTHROPIC_EFFORT` | No | `low` | Reasoning effort (`low`/`medium`/`high`). Set it to an empty string for models that don't support effort. |
| `FLASK_DEBUG` | No | *(unset)* | Set to `1` for auto-reload while developing. Never use it in production. |

```bash
export ANTHROPIC_API_KEY=sk-ant-...      # optional
export ANTHROPIC_MODEL=claude-opus-5-5   # optional
```

## Run

```bash
flask --app app run          # then open http://127.0.0.1:5000
```

Click **Try with sample bill** to see a full report without uploading anything.
Pages load Tailwind CSS and Chart.js from public CDNs, so the browser needs internet access.

## Tests

```bash
python -m pytest
```

Tests cover the parser, every waste rule, the app routes, and the AI explanations
(with a fake API client, so no key or network is needed). GitHub Actions runs them
on every pull request and push to `main` (Python 3.10, 3.12 and 3.13), see
`.github/workflows/tests.yml`.

## Getting your bill as a CSV

1. In the AWS console, open **Billing and Cost Management**.
2. Go to **Data Exports** (older accounts: **Cost and Usage Reports**) and create an export in CSV format.
3. AWS writes the report to an S3 bucket within about a day. Download the file for one month.
4. If it ends in `.gz` or `.zip`, unzip it and upload the `.csv`.

Both the legacy CUR headers (`lineItem/UnblendedCost`) and CUR 2.0 headers
(`line_item_unblended_cost`) are supported. Missing optional columns are handled
with a warning. Files that aren't a CUR get a friendly error explaining what to upload.

## How it works

1. **Parse and validate** the CSV in memory (`parsers/cur_parser.py`).
2. **Group** line items by service + usage type + region (`parsers/grouping.py`). Resource IDs are reduced to a count.
3. **Translate** technical names into plain names (`parsers/names.py`), e.g. `BoxUsage` → "virtual server (EC2)", `ap-southeast-2` → "Sydney".
4. **Detect waste** with the rules in `rules/`.
5. **Explain** each grouped charge (`explain/`): with AI if configured, otherwise from templates.

### AI explanations

- Only grouped totals are sent: service, usage type, region, quantity, unit and cost.
  Account IDs, ARNs and resource IDs are stripped first (`parsers/sanitize.py`); descriptions
  and resource names are never sent.
- Items are sent in batches of 20 (run in parallel) with a fixed system prompt (`explain/ai.py`).
- Replies are checked: each must be valid JSON, match an item we sent, and mention no dollar
  figure other than that item's own cost. Anything that fails, times out or is refused falls back
  to the template explanation for that charge, so the page always renders.
- On supported models, the request opts into the API's server-side refusal fallback
  (`fallbacks: "default"`), so a declined batch is retried on another model in the same call.
- Logs record only error types and status codes, never bill contents.

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
by several rules can save at most what it costs. Bills covering several months are
converted to monthly figures.

### Adding a rule

Create `rules/my_rule.py` with a function that takes a `BillContext` and returns
a list of `Finding`s, decorate it with `@rule`, and import the module in
`rules/__init__.py`. Add tests in `tests/test_rules.py`.

## Privacy and security

- CSV files only, up to 20 MB.
- Uploads are kept in memory (Werkzeug's temp-file spooling is disabled) and discarded after the response.
- No uploaded data is stored or logged.
- The app never asks for AWS credentials and never connects to an AWS account.

## Project layout

```
app.py                Flask routes: upload, results, sample bill, privacy page
analysis.py           runs the pipeline and shapes results for the page
parsers/              CSV parsing, grouping, plain-name mapping, ID scrubbing
rules/                waste detection rules, one module per rule
explain/              AI explanations (ai.py) and template fallback (templates.py)
templates/            Jinja pages (Tailwind CSS via CDN, blue theme)
static/               upload drag-and-drop, Chart.js donut, print styles
sample_data/          fake one-month bill + generator script
tests/                pytest suite
```

## Out of scope (for now)

Azure, Google Cloud, connecting live AWS accounts, user accounts, payments.
