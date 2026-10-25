"""Remove account IDs and resource identifiers from text.

Used on anything that leaves the server (for example, text sent to the AI).
"""

import re

_PATTERNS = [
    # ARNs: arn:aws:ec2:us-east-1:123456789012:instance/i-0abc...
    (re.compile(r"\barn:aws[\w-]*:[^\s,;\"']+"), "[resource]"),
    # 12-digit AWS account IDs
    (re.compile(r"(?<!\d)\d{12}(?!\d)"), "[account]"),
    # EC2-style resource IDs: i-0abc, vol-, snap-, eipalloc-, nat-, eni-, sg-, ami-, ...
    (re.compile(r"\b(?:i|vol|snap|eipalloc|eipassoc|nat|eni|sg|ami|subnet|vpc|igw|rtb|acl|"
                r"vpce|tgw|lt|fs|db|elb|lb|r|sir|fleet|h|cr)-[0-9a-f]{8,17}\b"), "[resource]"),
]


def scrub_text(text: str) -> str:
    """Replace account IDs, ARNs and resource IDs with placeholders."""
    if not text:
        return text
    for pattern, replacement in _PATTERNS:
        text = pattern.sub(replacement, text)
    return text
