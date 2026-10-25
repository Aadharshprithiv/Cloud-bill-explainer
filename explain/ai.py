"""Plain-English explanations from the Anthropic API.

Only grouped totals are sent (service, usage type, region, quantity, unit,
cost), with any account or resource IDs scrubbed. Anything that fails or looks
wrong falls back to the template explanation, so the page always renders.
"""

import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

import anthropic
import pandas as pd

from parsers.sanitize import scrub_text

log = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "You explain cloud bill charges to people with no cloud experience. For "
    "each item (service, usage type, region, quantity, unit, cost), write 1-2 "
    "short sentences: what the thing is, using an everyday comparison if it "
    "helps, and why it was charged. Use simple English with no jargon; if a "
    "technical term is unavoidable, explain it in brackets. Only use the numbers "
    "provided and never invent figures. If you don't recognise an item, say so "
    "plainly instead of guessing. Return JSON: [{\"id\": ..., \"explanation\": ...}]"
)

BATCH_SIZE = 20
MAX_PARALLEL_BATCHES = 4
MAX_EXPLANATION_CHARS = 600
# Models that accept the server-side refusal fallback ("default" form).
FALLBACK_MODELS = {"claude-fable-5-1", "claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5"}


@dataclass
class AISettings:
    api_key: str
    model: str = "claude-opus-5-5"
    effort: str = "low"  # explaining short items doesn't need deep reasoning
    timeout: float = 45.0

    @property
    def enabled(self) -> bool:
        return bool(self.api_key and self.model)


def build_payload(groups: pd.DataFrame) -> list[dict]:
    """The only bill data that leaves the server: grouped totals, IDs scrubbed."""
    return [{
        "id": int(g["id"]),
        "service": scrub_text(g["product_code"] or g["service_name"]),
        "usage_type": scrub_text(g["usage_type"]),
        "region": scrub_text(f'{g["region_name"]} ({g["region"]})'),
        "quantity": round(float(g["usage_amount"]), 4),
        "unit": scrub_text(g["unit"]),
        "cost": round(float(g["cost"]), 2),
    } for g in groups.to_dict("records")]


def parse_response(text: str) -> list[dict]:
    """Pull the JSON array out of the reply (tolerates code fences or extra prose)."""
    start, end = text.find("["), text.rfind("]")
    if start == -1 or end <= start:
        raise ValueError("no JSON array in response")
    data = json.loads(text[start:end + 1])
    if not isinstance(data, list):
        raise ValueError("response is not a list")
    return data


_DOLLAR_RE = re.compile(r"\$\s?(\d[\d,]*(?:\.\d+)?)")


def uses_only_given_costs(explanation: str, item: dict) -> bool:
    """Reject explanations that mention a dollar figure we didn't provide."""
    for match in _DOLLAR_RE.finditer(explanation):
        amount = float(match.group(1).replace(",", ""))
        if abs(amount - abs(item["cost"])) > 0.01:
            return False
    return True


def validate(results: list, batch: list[dict]) -> dict[int, str]:
    """Keep only well-formed explanations for ids in this batch."""
    by_id = {item["id"]: item for item in batch}
    valid = {}
    for result in results:
        if not isinstance(result, dict):
            continue
        try:
            item_id = int(result.get("id"))
        except (TypeError, ValueError):
            continue
        explanation = result.get("explanation")
        if item_id not in by_id or not isinstance(explanation, str):
            continue
        explanation = " ".join(explanation.split())
        if not explanation or len(explanation) > MAX_EXPLANATION_CHARS:
            continue
        if not uses_only_given_costs(explanation, by_id[item_id]):
            continue
        valid[item_id] = explanation
    return valid


def _request_kwargs(settings: AISettings, batch: list[dict]) -> dict:
    kwargs = {
        "model": settings.model,
        "max_tokens": 16000,
        "system": SYSTEM_PROMPT,
        "messages": [{"role": "user", "content": json.dumps(batch)}],
    }
    if settings.effort:
        kwargs["output_config"] = {"effort": settings.effort}
    if settings.model in FALLBACK_MODELS:
        # If a safety classifier declines, the API retries on a fallback model in the same call.
        kwargs["betas"] = ["server-side-fallback-2026-07-01"]
        kwargs["fallbacks"] = "default"
    return kwargs


def explain_batch(client: anthropic.Anthropic, settings: AISettings, batch: list[dict]) -> dict[int, str]:
    """Explanations for one batch; empty dict on any failure (caller falls back)."""
    kwargs = _request_kwargs(settings, batch)
    try:
        if "betas" in kwargs:
            response = client.beta.messages.create(**kwargs)
        else:
            response = client.messages.create(**kwargs)
    except anthropic.APIStatusError as exc:
        # Log the status only - never the request or response, which hold bill data.
        log.warning("AI explanation request failed with HTTP %s", exc.status_code)
        return {}
    except anthropic.APIConnectionError:
        log.warning("AI explanation request could not connect")
        return {}
    except anthropic.APIError as exc:
        log.warning("AI explanation request failed: %s", type(exc).__name__)
        return {}

    if response.stop_reason not in ("end_turn", "stop_sequence"):
        log.warning("AI explanation stopped early: %s", response.stop_reason)
        return {}
    text = "".join(block.text for block in response.content if block.type == "text")
    try:
        return validate(parse_response(text), batch)
    except (ValueError, json.JSONDecodeError):
        log.warning("AI explanation response was not valid JSON")
        return {}


def explain_with_ai(groups: pd.DataFrame, settings: AISettings,
                    client: anthropic.Anthropic | None = None) -> dict[int, str]:
    """Explanations keyed by group id; ids missing from the result need a fallback."""
    payload = build_payload(groups)
    if not payload:
        return {}
    client = client or anthropic.Anthropic(api_key=settings.api_key, timeout=settings.timeout, max_retries=1)
    batches = [payload[i:i + BATCH_SIZE] for i in range(0, len(payload), BATCH_SIZE)]
    explanations: dict[int, str] = {}
    with ThreadPoolExecutor(max_workers=min(MAX_PARALLEL_BATCHES, len(batches))) as pool:
        for result in pool.map(lambda batch: explain_batch(client, settings, batch), batches):
            explanations.update(result)
    return explanations
