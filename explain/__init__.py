"""Plain-English explanations for grouped charges."""

import pandas as pd

from explain.templates import explain_all


def explain_groups(groups: pd.DataFrame, currency: str = "USD") -> tuple[dict[int, str], str]:
    """Return ({group id: explanation}, source) where source is "template" or "ai"."""
    return explain_all(groups, currency), "template"
