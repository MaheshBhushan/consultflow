import json
from decimal import Decimal

import httpx
import pytest

from app import llm


def test_offline_provider_is_deterministic_and_extracts_entities():
    prompt = (
        "Invoice INV-1 for $1,200.50 is due on 2026-12-01. "
        "Contact billing@acme.example about the invoice. The weather was nice today."
    )
    a = llm.OfflineProvider().complete("offline-extractive-v1", "sys", prompt)
    b = llm.OfflineProvider().complete("offline-extractive-v1", "sys", prompt)
    assert a == b
    assert "Amounts: $1,200.50" in a.text
    assert "Dates: 2026-12-01" in a.text
    assert "Emails: billing@acme.example" in a.text
    assert a.input_tokens == llm.count_tokens("sys") + llm.count_tokens(prompt)
    assert a.output_tokens == llm.count_tokens(a.text)


def test_cost_uses_pricing_table():
    # claude-haiku-4-5: $1 / 1M input, $5 / 1M output
    assert llm.cost_usd("claude-haiku-4-5", 1000, 200) == Decimal("0.002000")
    assert llm.cost_usd("gpt-4o-mini", 10_000, 1_000) == Decimal("0.002100")
    assert llm.cost_usd("offline-extractive-v1", 10**6, 10**6) == Decimal("0")


def test_every_offered_model_is_priced():
    for models in llm.PROVIDER_MODELS.values():
        for m in models:
            assert m in llm.PRICING


def test_anthropic_adapter_parses_usage():
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert request.headers["x-api-key"] == "k"
        assert body["system"] == "be brief"
        return httpx.Response(
            200,
            json={
                "content": [{"type": "text", "text": "ok"}],
                "usage": {"input_tokens": 12, "output_tokens": 3},
            },
        )

    provider = llm.AnthropicProvider("k", httpx.Client(transport=httpx.MockTransport(handler)))
    assert provider.complete("claude-haiku-4-5", "be brief", "hi") == llm.LLMResult("ok", 12, 3)


def test_openai_adapter_parses_usage_and_errors():
    def ok(request):
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "yo"}}],
                "usage": {"prompt_tokens": 7, "completion_tokens": 2},
            },
        )

    provider = llm.OpenAIProvider("k", httpx.Client(transport=httpx.MockTransport(ok)))
    assert provider.complete("gpt-4o-mini", "", "hi") == llm.LLMResult("yo", 7, 2)

    bad = llm.OpenAIProvider(
        "k", httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(401, text="no")))
    )
    with pytest.raises(llm.LLMError, match="401"):
        bad.complete("gpt-4o-mini", "", "hi")


def test_get_provider_requires_key(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(llm.LLMError, match="OPENAI_API_KEY"):
        llm.get_provider("openai")
    assert llm.available_providers()["openai"]["available"] is False
    assert llm.available_providers()["offline"]["available"] is True
