"""Plain-English explanations for grouped charges."""

import pandas as pd

from explain.ai import AISettings, explain_with_ai
from explain.templates import explain_all


def explain_groups(groups: pd.DataFrame, currency: str = "USD",
                   ai_settings: AISettings | None = None) -> tuple[dict[int, str], str]:
    """Return ({group id: explanation}, source).

    source is "ai" (every charge explained by AI), "mixed" (some fell back to
    templates) or "template" (AI off or unavailable).
    """
    explanations = explain_all(groups, currency)
    if ai_settings is None or not ai_settings.enabled or groups.empty:
        return explanations, "template"

    from_ai = explain_with_ai(groups, ai_settings)
    explanations.update(from_ai)
    if not from_ai:
        return explanations, "template"
    return explanations, "ai" if len(from_ai) == len(explanations) else "mixed"
