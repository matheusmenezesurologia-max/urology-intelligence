"""Unified LLM client wrapper for OpenAI and Anthropic.

Routing is by model id: ``gpt-*`` / ``o1-*`` go to OpenAI, ``claude-*`` go to
Anthropic. A ``complete_json`` helper enforces and parses JSON output so agents
get a validated ``dict`` back.
"""
from __future__ import annotations

import json
import re
from functools import lru_cache
from typing import Any

from app.config import settings
from app.utils.logger import get_logger
from app.utils.metrics import LLM_TOKENS
from app.utils.retry import llm_retry

log = get_logger("llm")


class LLMError(RuntimeError):
    pass


@lru_cache(maxsize=1)
def _openai_client():  # pragma: no cover - requires live OpenAI client
    from openai import OpenAI

    if not settings.openai_api_key:
        raise LLMError("OPENAI_API_KEY is not configured")
    return OpenAI(api_key=settings.openai_api_key, timeout=settings.http_timeout_seconds)


@lru_cache(maxsize=1)
def _anthropic_client():  # pragma: no cover - requires live Anthropic client
    from anthropic import Anthropic

    if not settings.anthropic_api_key:
        raise LLMError("ANTHROPIC_API_KEY is not configured")
    return Anthropic(api_key=settings.anthropic_api_key, timeout=settings.http_timeout_seconds)


def _provider_for(model: str) -> str:
    m = model.lower()
    if m.startswith(("gpt", "o1", "o3", "chatgpt", "text-embedding")):
        return "openai"
    if m.startswith("claude"):
        return "anthropic"
    # Default to Anthropic for unknown ids (analysis-heavy workloads).
    return "anthropic"


@llm_retry
def _openai_complete(model: str, system: str, user: str, temperature: float, max_tokens: int, json_mode: bool) -> str:  # pragma: no cover - network
    client = _openai_client()
    kwargs: dict[str, Any] = {
        "model": model,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    }
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}
    resp = client.chat.completions.create(**kwargs)
    if resp.usage:
        LLM_TOKENS.labels(provider="openai", kind="input").inc(resp.usage.prompt_tokens)
        LLM_TOKENS.labels(provider="openai", kind="output").inc(resp.usage.completion_tokens)
    return resp.choices[0].message.content or ""


@llm_retry
def _anthropic_complete(model: str, system: str, user: str, temperature: float, max_tokens: int, json_mode: bool) -> str:  # pragma: no cover - network
    client = _anthropic_client()
    if json_mode:
        system = f"{system}\n\nYou MUST respond with a single valid JSON object and nothing else."
    # Current Claude models (4.6+/5.x) use adaptive thinking and reject a non-default
    # `temperature`, so we omit it. We also give generous headroom on max_tokens so the
    # (billed-only-if-used) thinking tokens plus the actual answer both fit — a small
    # cap like 500 would otherwise be consumed by thinking and truncate the output.
    # Stream the response: large inputs / outputs / thinking otherwise hit the API's
    # non-streaming request timeout (Anthropic's documented guidance).
    with client.messages.stream(
        model=model,
        system=system,
        max_tokens=max(max_tokens, 8192),
        messages=[{"role": "user", "content": user}],
    ) as stream:
        resp = stream.get_final_message()
    if resp.usage:
        LLM_TOKENS.labels(provider="anthropic", kind="input").inc(resp.usage.input_tokens)
        LLM_TOKENS.labels(provider="anthropic", kind="output").inc(resp.usage.output_tokens)
    return "".join(block.text for block in resp.content if getattr(block, "type", None) == "text")


def complete(
    system: str,
    user: str,
    *,
    model: str | None = None,
    temperature: float = 0.2,
    max_tokens: int = 2048,
    json_mode: bool = False,
) -> str:
    """Return the model's text completion for a system+user prompt."""
    model = model or settings.analysis_model
    provider = _provider_for(model)
    log.debug("llm_call", provider=provider, model=model, json_mode=json_mode)
    if provider == "openai":
        return _openai_complete(model, system, user, temperature, max_tokens, json_mode)
    return _anthropic_complete(model, system, user, temperature, max_tokens, json_mode)


def _extract_json(text: str) -> str:
    """Pull the first balanced JSON object/array out of a text blob."""
    text = text.strip()
    # Strip markdown fences if present.
    fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, flags=re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    if text.startswith("{") or text.startswith("["):
        return text
    match = re.search(r"(\{.*\}|\[.*\])", text, flags=re.DOTALL)
    if match:
        return match.group(1)
    raise LLMError(f"No JSON object found in LLM response: {text[:200]!r}")


def complete_json(
    system: str,
    user: str,
    *,
    model: str | None = None,
    temperature: float = 0.1,
    max_tokens: int = 2048,
) -> dict[str, Any]:
    """Return a parsed JSON object from the model. Raises ``LLMError`` on failure."""
    raw = complete(
        system,
        user,
        model=model,
        temperature=temperature,
        max_tokens=max_tokens,
        json_mode=True,
    )
    try:
        return json.loads(_extract_json(raw))
    except (json.JSONDecodeError, LLMError) as exc:
        log.error("llm_json_parse_failed", error=str(exc), raw=raw[:500])
        raise LLMError(f"Failed to parse JSON from LLM: {exc}") from exc
