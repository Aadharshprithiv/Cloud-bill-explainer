import json
from types import SimpleNamespace

import anthropic
import httpx2
import pytest

from explain import explain_groups
from explain.ai import (BATCH_SIZE, SYSTEM_PROMPT, AISettings, build_payload, explain_with_ai,
                        parse_response, validate)
from parsers.cur_parser import parse_cur
from parsers.grouping import group_line_items
from tests.conftest import make_csv

SETTINGS = AISettings(api_key="test-key", model="claude-opus-5-5")


class FakeClient:
    """Stands in for anthropic.Anthropic; records each request."""

    def __init__(self, reply=None, error=None, stop_reason="end_turn"):
        self.calls = []
        self._reply = reply or self.good_reply
        self._error = error
        self._stop_reason = stop_reason
        self.messages = SimpleNamespace(create=self._create)
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    @staticmethod
    def good_reply(batch):
        return json.dumps([{"id": item["id"], "explanation": f"Plain words for item {item['id']}, "
                                                             f"costing ${item['cost']:.2f}."}
                           for item in batch])

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        if self._error:
            raise self._error
        batch = json.loads(kwargs["messages"][0]["content"])
        return SimpleNamespace(stop_reason=self._stop_reason,
                               content=[SimpleNamespace(type="text", text=self._reply(batch))])


@pytest.fixture(scope="module")
def groups(sample_bytes):
    return group_line_items(parse_cur(sample_bytes).items)


def many_groups(n):
    rows = [{"lineItem/ProductCode": "AmazonEC2", "lineItem/UsageType": f"BoxUsage:t3.size{i}",
             "lineItem/UnblendedCost": str(i + 1)} for i in range(n)]
    return group_line_items(parse_cur(make_csv(rows)).items)


# --- What gets sent --------------------------------------------------------------

def test_system_prompt_matches_spec():
    assert SYSTEM_PROMPT.startswith("You explain cloud bill charges to people with no cloud experience.")
    assert "never invent figures" in SYSTEM_PROMPT
    assert SYSTEM_PROMPT.endswith('Return JSON: [{"id": ..., "explanation": ...}]')


def test_payload_has_only_grouped_fields(groups):
    payload = build_payload(groups)
    assert len(payload) == len(groups)
    assert set(payload[0]) == {"id", "service", "usage_type", "region", "quantity", "unit", "cost"}
    first = payload[0]
    assert first == {"id": 1, "service": "AmazonEC2", "usage_type": "BoxUsage:m5.large",
                     "region": "N. Virginia (USA) (us-east-1)", "quantity": 744.0, "unit": "Hrs",
                     "cost": 71.42}


def test_payload_contains_no_identifiers(groups, sample_bytes):
    text = json.dumps(build_payload(groups))
    assert "123456789012" not in text
    assert "i-0a1b2c3d" not in text
    assert "arn:aws" not in text
    assert "my-photo-backups" not in text  # bucket names (resource IDs) are not sent


def test_payload_scrubs_ids_hidden_in_fields():
    rows = [{"lineItem/ProductCode": "AmazonEC2", "lineItem/UsageType": "Odd-123456789012-i-0abc12345678def00",
             "lineItem/UnblendedCost": "1"}]
    text = json.dumps(build_payload(group_line_items(parse_cur(make_csv(rows)).items)))
    assert "123456789012" not in text and "i-0abc12345678def00" not in text


def test_request_shape(groups):
    client = FakeClient()
    explain_with_ai(groups, SETTINGS, client)
    [call] = client.calls
    assert call["model"] == "claude-opus-5-5"
    assert call["system"] == SYSTEM_PROMPT
    assert call["output_config"] == {"effort": "low"}
    assert call["fallbacks"] == "default"
    assert call["betas"] == ["server-side-fallback-2026-07-01"]


def test_request_for_other_models_skips_fallback_and_optional_effort(groups):
    client = FakeClient()
    explain_with_ai(groups, AISettings(api_key="k", model="claude-haiku-4-5", effort=""), client)
    [call] = client.calls
    assert call["model"] == "claude-haiku-4-5"
    assert "fallbacks" not in call and "betas" not in call and "output_config" not in call


def test_items_are_sent_in_batches():
    groups = many_groups(45)
    client = FakeClient()
    result = explain_with_ai(groups, SETTINGS, client)
    sizes = sorted(len(json.loads(c["messages"][0]["content"])) for c in client.calls)
    assert sizes == [5, BATCH_SIZE, BATCH_SIZE]
    assert len(result) == 45


# --- Using the reply ---------------------------------------------------------------

def test_good_reply_is_used(groups):
    explanations = explain_with_ai(groups, SETTINGS, FakeClient())
    assert explanations[1] == "Plain words for item 1, costing $71.42."
    assert len(explanations) == len(groups)


def test_parse_response_tolerates_code_fences():
    assert parse_response('```json\n[{"id": 1, "explanation": "x"}]\n```') == [{"id": 1, "explanation": "x"}]
    with pytest.raises(ValueError):
        parse_response("Sorry, I can't help with that.")


def test_validate_rejects_bad_items():
    batch = [{"id": 1, "cost": 3.72}, {"id": 2, "cost": 0.5}]
    results = [
        {"id": 1, "explanation": "An unused address, costing $3.72."},
        {"id": 2, "explanation": "This saves you $40 a month."},   # invented figure
        {"id": 99, "explanation": "Not in this batch."},
        {"id": "x", "explanation": "Bad id."},
        {"id": 1, "explanation": ""},
        "not a dict",
    ]
    assert validate(results, batch) == {1: "An unused address, costing $3.72."}


def test_validate_rejects_overlong_explanations():
    assert validate([{"id": 1, "explanation": "word " * 200}], [{"id": 1, "cost": 1}]) == {}


# --- Falling back to templates -----------------------------------------------------

def test_explain_groups_without_key_uses_templates(groups):
    explanations, source = explain_groups(groups, ai_settings=AISettings(api_key=""))
    assert source == "template"
    assert "virtual server" in explanations[1]


def test_explain_groups_all_ai(groups, monkeypatch):
    monkeypatch.setattr("explain.explain_with_ai",
                        lambda g, s: explain_with_ai(g, s, FakeClient()))
    explanations, source = explain_groups(groups, ai_settings=SETTINGS)
    assert source == "ai"
    assert explanations[1].startswith("Plain words for item 1")


def test_partial_reply_mixes_in_templates(groups, monkeypatch):
    def half(batch):
        return json.dumps([{"id": i["id"], "explanation": "AI words."} for i in batch[::2]])
    monkeypatch.setattr("explain.explain_with_ai",
                        lambda g, s: explain_with_ai(g, s, FakeClient(reply=half)))
    explanations, source = explain_groups(groups, ai_settings=SETTINGS)
    assert source == "mixed"
    assert explanations[1] == "AI words."
    assert "AI words." not in explanations[2]
    assert all(explanations.values())


@pytest.mark.parametrize("client", [
    FakeClient(reply=lambda batch: "not json at all"),
    FakeClient(stop_reason="refusal"),
    FakeClient(stop_reason="max_tokens"),
    FakeClient(error=anthropic.APIConnectionError(request=httpx2.Request("POST", "https://api.anthropic.com"))),
    FakeClient(error=anthropic.RateLimitError(
        "rate limited", response=httpx2.Response(429, request=httpx2.Request("POST", "https://api.anthropic.com")),
        body=None)),
], ids=["bad-json", "refusal", "max-tokens", "connection-error", "rate-limit"])
def test_failures_fall_back_to_templates(groups, monkeypatch, client):
    monkeypatch.setattr("explain.explain_with_ai", lambda g, s: explain_with_ai(g, s, client))
    explanations, source = explain_groups(groups, ai_settings=SETTINGS)
    assert source == "template"
    assert "virtual server" in explanations[1]
    assert len(explanations) == len(groups)


def test_failure_logs_contain_no_bill_data(groups, caplog):
    client = FakeClient(reply=lambda batch: "garbage BoxUsage:m5.large 71.42")
    explain_with_ai(groups, SETTINGS, client)
    assert "BoxUsage" not in caplog.text and "71.42" not in caplog.text
