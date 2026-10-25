"""Waste detection rules. Importing a rule module registers its rules."""

from rules import (always_on, data_transfer, forgotten_region, idle_ip,  # noqa: F401
                   nat_gateway, old_generation, s3_standard, snapshots)
from rules.base import RULES, BillContext, Finding, build_context, run_rules, total_saving

__all__ = ["RULES", "BillContext", "Finding", "build_context", "run_rules", "total_saving"]
