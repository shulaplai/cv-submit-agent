"""LLM abstraction: OpenAI-compatible client (DeepSeek primary, Qwen fallback).

Provides chat_json() which guarantees a parsed JSON object back or raises
LLMError — callers treat JSON-mode + double-parse as the contract, so the
frontend never receives malformed model output (format-drift guard).
"""
from __future__ import annotations

import json
import logging
from typing import Any

from openai import OpenAI

from ..config import settings

log = logging.getLogger(__name__)


class LLMError(RuntimeError):
    pass


def _client(base_url: str, api_key: str) -> OpenAI:
    return OpenAI(base_url=base_url, api_key=api_key, timeout=60, max_retries=2)


def _chat(messages: list[dict], model: str, base_url: str, api_key: str,
          json_mode: bool, temperature: float = 0.3) -> str:
    client = _client(base_url, api_key)
    kwargs: dict[str, Any] = {"model": model, "messages": messages, "temperature": temperature}
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}
    resp = client.chat.completions.create(**kwargs)
    content = resp.choices[0].message.content or ""
    if not content:
        raise LLMError("empty LLM response")
    return content


def chat(messages: list[dict], temperature: float = 0.3) -> str:
    """Plain text completion: primary provider, then fallback provider."""
    if settings.LLM_API_KEY:
        try:
            return _chat(messages, settings.LLM_MODEL, settings.LLM_BASE_URL,
                         settings.LLM_API_KEY, json_mode=False, temperature=temperature)
        except Exception as e:  # noqa: BLE001
            log.warning("primary LLM failed (%s); trying fallback", e)
    if settings.LLM_FALLBACK_API_KEY:
        return _chat(messages, settings.LLM_FALLBACK_MODEL, settings.LLM_FALLBACK_BASE_URL,
                     settings.LLM_FALLBACK_API_KEY, json_mode=False, temperature=temperature)
    raise LLMError("no LLM API key configured (set LLM_API_KEY or LLM_FALLBACK_API_KEY in .env)")


def chat_json(messages: list[dict], temperature: float = 0.0) -> dict:
    """JSON completion with double-parse safety. Returns a dict or raises LLMError."""
    last_err: Exception | None = None
    if settings.LLM_API_KEY:
        try:
            content = _chat(messages, settings.LLM_MODEL, settings.LLM_BASE_URL,
                            settings.LLM_API_KEY, json_mode=True, temperature=temperature)
            return _parse_json(content)
        except Exception as e:  # noqa: BLE001
            last_err = e
            log.warning("primary JSON LLM failed (%s); trying fallback", e)
    if settings.LLM_FALLBACK_API_KEY:
        try:
            content = _chat(messages, settings.LLM_FALLBACK_MODEL, settings.LLM_FALLBACK_BASE_URL,
                            settings.LLM_FALLBACK_API_KEY, json_mode=True, temperature=temperature)
            return _parse_json(content)
        except Exception as e:  # noqa: BLE001
            last_err = e
    raise LLMError(f"LLM JSON call failed: {last_err}")


def _parse_json(content: str) -> dict:
    """Parse model output as JSON, stripping markdown fences if present."""
    text = content.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()
    try:
        obj = json.loads(text)
    except json.JSONDecodeError:
        # last resort: extract first {...} block
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise LLMError(f"model returned non-JSON: {content[:300]}")
        obj = json.loads(text[start : end + 1])
    if not isinstance(obj, dict):
        raise LLMError(f"model returned non-object JSON: {content[:200]}")
    return obj
