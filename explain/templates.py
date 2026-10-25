"""Template-based plain-English explanations (used when no AI is available)."""

import re

from parsers.names import split_usage_prefix

UNIT_NAMES = {
    "hrs": "hours", "hours": "hours", "hour": "hours",
    "gb-mo": "GB-months", "gb-month": "GB-months",
    "gb": "GB", "requests": "requests", "request": "requests",
    "lambda-gb-second": "GB-seconds", "gb-seconds": "GB-seconds",
    "lcu-hrs": "capacity-unit hours", "count": "", "queries": "queries",
}


def format_quantity(amount: float, unit: str) -> str:
    """744, "Hrs" -> "744 hours"; 0.25, "GB" -> "0.25 GB"."""
    unit_name = UNIT_NAMES.get((unit or "").lower(), unit or "")
    amount = round(amount, 6)  # sums of daily rows leave float noise like 99.9999999
    if amount >= 100 or float(amount).is_integer():
        number = f"{amount:,.0f}"
    elif amount >= 1:
        number = f"{amount:,.1f}"
    else:
        number = f"{amount:,.3g}"
    return f"{number} {unit_name}".strip()


def money(amount: float, currency: str = "USD") -> str:
    prefix = "$" if currency == "USD" else f"{currency} "
    sign = "-" if amount < 0 else ""
    return f"{sign}{prefix}{abs(amount):,.2f}"


# (regex on usage type without region prefix, template). Placeholders:
# {qty} quantity with unit, {cost}, {region}, {instance}, {service}, {name}
TEMPLATES = [
    (r"^BoxUsage", "A virtual server (a computer you rent in an AWS data centre){instance_part}. "
                   "It was switched on for {qty} in {region}, which cost {cost}."),
    (r"^SpotUsage", "A discounted virtual server using AWS's spare capacity{instance_part}. "
                    "It ran for {qty} in {region}, costing {cost}."),
    (r"^EBS:VolumeUsage", "A storage disk for your servers, like a hard drive. You kept {qty} of disk "
                          "space (1 GB-month is 1 GB kept for a whole month), costing {cost}."),
    (r"^EBS:Snapshot", "Backups of your server disks (snapshots), like saved photos of a disk. "
                       "You stored {qty} of them, costing {cost}."),
    (r"IdleAddress", "A public IP address (the address the internet uses to reach a server) that "
                     "wasn't attached to anything for {qty}. AWS still charges for it: {cost}."),
    (r"InUseAddress", "A public IP address (the address the internet uses to reach a server) "
                      "attached to one of your servers for {qty}, costing {cost}."),
    (r"^NatGateway-Hours", "A NAT gateway, which lets private servers reach the internet, like a shared "
                           "front door. It's charged for every hour it exists: {qty}, costing {cost}."),
    (r"^NatGateway-Bytes", "The data that passed through your NAT gateway: {qty}, costing {cost}."),
    (r"^DataTransfer-Out-Bytes", "Data sent from AWS out to the internet, for example when people "
                                 "visit your site or download files: {qty}, costing {cost}."),
    (r"^DataTransfer-In-Bytes", "Data sent into AWS from the internet: {qty}, costing {cost}."),
    (r"^DataTransfer-Regional-Bytes", "Data moved between data centres in the same region: {qty}, "
                                      "costing {cost}."),
    (r"-AWS-Out-Bytes$", "Data sent from {region} to another AWS region: {qty}, costing {cost}."),
    (r"-AWS-In-Bytes$", "Data received in {region} from another AWS region: {qty}, costing {cost}."),
    (r"^TimedStorage-ByteHrs$", "Files stored in S3 Standard, AWS's everyday file storage, like an "
                                "online drive. You stored {qty}, costing {cost}."),
    (r"^TimedStorage", "Files stored in S3 ({name}): {qty}, costing {cost}."),
    (r"^Requests-Tier1", "Requests to upload or list files in S3: {qty}, costing {cost}."),
    (r"^Requests-Tier2", "Requests to read or download files from S3: {qty}, costing {cost}."),
    (r"^Requests-", "Requests made to S3: {qty}, costing {cost}."),
    (r"^Lambda-GB-Second", "Time your Lambda functions (small pieces of code AWS runs on demand) "
                           "spent running, measured as memory x seconds: {qty}, costing {cost}."),
    (r"^Request$", "The number of times your Lambda functions (small pieces of code AWS runs on demand) "
                   "were called: {qty}, costing {cost}."),
    (r"^InstanceUsage", "A managed database server{instance_part} that ran for {qty} in {region}, "
                        "costing {cost}."),
    (r"^LoadBalancerUsage", "A load balancer, which shares visitors between your servers, "
                            "charged per hour: {qty}, costing {cost}."),
    (r"^Tax$", "Sales tax or VAT added to your bill: {cost}."),
    (r"^(Credit|Refund)$", "A credit or refund from AWS that reduces your bill: {cost}."),
]
_COMPILED = [(re.compile(pattern), text) for pattern, text in TEMPLATES]

FALLBACK = ("A charge for {name} from {service} in {region}: {qty}, costing {cost}. "
            "We don't have a plain-English description for this one yet.")


def explain_charge(group: dict, currency: str = "USD") -> str:
    """One or two plain sentences for a grouped charge (a row from group_line_items)."""
    _, core = split_usage_prefix(group["usage_type"])
    instance = group.get("instance_type") or ""
    values = {
        "qty": format_quantity(group["usage_amount"], group["unit"]),
        "cost": money(group["cost"], currency),
        "region": group["region_name"],
        "service": group["service_name"],
        "name": group["usage_name"],
        "instance_part": f" of size {instance}" if instance else "",
    }
    for pattern, text in _COMPILED:
        if pattern.search(core):
            return text.format(**values)
    return FALLBACK.format(**values)


def explain_all(groups, currency: str = "USD") -> dict[int, str]:
    return {int(g["id"]): explain_charge(g, currency) for g in groups.to_dict("records")}
