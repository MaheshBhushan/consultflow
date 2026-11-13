"""Provider adapter for LLM calls.

Every provider returns an `LLMResult` with token usage; cost is computed from the
`PRICING` table (USD per 1M tokens) so cost logging is identical across providers.
The `offline` provider is deterministic and needs no network; tests and CI use it.
"""

from __future__ import annotations

import os
import re
from collections import Counter
from dataclasses import dataclass
from decimal import Decimal

import httpx

# USD per 1M tokens (input, output). Edit when list prices change.
PRICING: dict[str, tuple[Decimal, Decimal]] = {
    "offline-extractive-v1": (Decimal("0"), Decimal("0")),
    "claude-haiku-4-5": (Decimal("1.00"), Decimal("5.00")),
    "claude-sonnet-4-5": (Decimal("3.00"), Decimal("15.00")),
    "gpt-4o-mini": (Decimal("0.15"), Decimal("0.60")),
    "gpt-4.1-mini": (Decimal("0.40"), Decimal("1.60")),
}

PROVIDER_MODELS: dict[str, list[str]] = {
    "offline": ["offline-extractive-v1"],
    "anthropic": ["claude-haiku-4-5", "claude-sonnet-4-5"],
    "openai": ["gpt-4o-mini", "gpt-4.1-mini"],
}

PROVIDER_KEYS = {"anthropic": "ANTHROPIC_API_KEY", "openai": "OPENAI_API_KEY"}


class LLMError(RuntimeError):
    pass


@dataclass(frozen=True)
class LLMResult:
    text: str
    input_tokens: int
    output_tokens: int


def cost_usd(model: str, input_tokens: int, output_tokens: int) -> Decimal:
    price_in, price_out = PRICING[model]
    total = (price_in * input_tokens + price_out * output_tokens) / Decimal(1_000_000)
    return total.quantize(Decimal("0.000001"))


def available_providers() -> dict[str, dict]:
    """Providers and whether they can be used in this process (key present)."""
    out = {}
    for name, models in PROVIDER_MODELS.items():
        key = PROVIDER_KEYS.get(name)
        out[name] = {"models": models, "available": key is None or bool(os.environ.get(key))}
    return out


_TOKEN_RE = re.compile(r"\w+|[^\w\s]")
_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+|\n+")
_STOPWORDS = set(
    "a an and are as at be by for from has have i in is it of on or our please that the this "
    "to was we were will with you your".split()
)
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_AMOUNT_RE = re.compile(r"(?:[$€£]\s?\d[\d,]*(?:\.\d+)?|\b\d[\d,]*(?:\.\d+)?\s?(?:USD|EUR|GBP)\b)")
_DATE_RE = re.compile(
    r"\b(?:\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/\d{2,4}|"
    r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.? \d{1,2},? \d{4})\b"
)


def count_tokens(text: str) -> int:
    """Approximate token count (words and punctuation). Used only by the offline provider."""
    return len(_TOKEN_RE.findall(text))


class OfflineProvider:
    """Deterministic extractive model: ranks sentences by word frequency and lists
    e-mail addresses, monetary amounts and dates found in the prompt."""

    name = "offline"

    def complete(self, model: str, system: str, prompt: str) -> LLMResult:
        sentences = [s.strip() for s in _SENTENCE_RE.split(prompt) if len(s.strip()) > 20]
        words = [w.lower() for w in re.findall(r"[A-Za-z]{3,}", prompt)]
        freq = Counter(w for w in words if w not in _STOPWORDS)

        def score(s: str) -> float:
            toks = [w.lower() for w in re.findall(r"[A-Za-z]{3,}", s)]
            return sum(freq[t] for t in toks) / (len(toks) or 1)

        ranked = sorted(range(len(sentences)), key=lambda i: (-score(sentences[i]), i))[:3]
        summary = [sentences[i] for i in sorted(ranked)]
        lines = ["Key points:"] + [f"- {s}" for s in summary]
        for label, rx in (("Emails", _EMAIL_RE), ("Amounts", _AMOUNT_RE), ("Dates", _DATE_RE)):
            found = list(dict.fromkeys(m.strip() for m in rx.findall(prompt)))
            if found:
                lines.append(f"{label}: {', '.join(found)}")
        text = "\n".join(lines)
        return LLMResult(text, count_tokens(system) + count_tokens(prompt), count_tokens(text))


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, api_key: str, client: httpx.Client | None = None):
        self.api_key = api_key
        self.client = client or httpx.Client(timeout=60)

    def complete(self, model: str, system: str, prompt: str) -> LLMResult:
        body: dict = {
            "model": model,
            "max_tokens": 1024,
            "messages": [{"role": "user", "content": prompt}],
        }
        if system:
            body["system"] = system
        resp = self.client.post(
            "https://api.anthropic.com/v1/messages",
            json=body,
            headers={"x-api-key": self.api_key, "anthropic-version": "2023-06-01"},
        )
        if resp.status_code != 200:
            raise LLMError(f"anthropic HTTP {resp.status_code}: {resp.text[:300]}")
        data = resp.json()
        text = "".join(b.get("text", "") for b in data["content"] if b.get("type") == "text")
        usage = data["usage"]
        return LLMResult(text, usage["input_tokens"], usage["output_tokens"])


class OpenAIProvider:
    name = "openai"

    def __init__(self, api_key: str, client: httpx.Client | None = None):
        self.api_key = api_key
        self.client = client or httpx.Client(timeout=60)

    def complete(self, model: str, system: str, prompt: str) -> LLMResult:
        messages = ([{"role": "system", "content": system}] if system else []) + [
            {"role": "user", "content": prompt}
        ]
        resp = self.client.post(
            "https://api.openai.com/v1/chat/completions",
            json={"model": model, "messages": messages},
            headers={"Authorization": f"Bearer {self.api_key}"},
        )
        if resp.status_code != 200:
            raise LLMError(f"openai HTTP {resp.status_code}: {resp.text[:300]}")
        data = resp.json()
        usage = data["usage"]
        return LLMResult(
            data["choices"][0]["message"]["content"] or "",
            usage["prompt_tokens"],
            usage["completion_tokens"],
        )


def get_provider(name: str):
    if name == "offline":
        return OfflineProvider()
    if name not in PROVIDER_KEYS:
        raise LLMError(f"unknown provider {name!r}")
    key = os.environ.get(PROVIDER_KEYS[name])
    if not key:
        raise LLMError(f"provider {name!r} needs {PROVIDER_KEYS[name]} in the environment")
    return AnthropicProvider(key) if name == "anthropic" else OpenAIProvider(key)
