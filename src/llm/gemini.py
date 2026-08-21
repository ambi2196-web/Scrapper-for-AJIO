"""Gemini provider. Structured output via response_schema, temperature 0.

`response_mime_type="application/json"` plus `response_schema` eliminates the
whole class of "the model wrapped it in a markdown fence" failures. On a free
tier where every retry costs quota that is worth more than it looks: a parse
failure is not just a lost batch, it is a lost slot in a 1,500-request day.
"""
from __future__ import annotations

from typing import Any

from src.config import api_key
from src.llm.router import RetryableError

_RETRYABLE_MARKERS = ("429", "500", "503", "resource_exhausted", "unavailable", "deadline")


def _client() -> Any:
    try:
        from google import genai
    except ImportError as exc:
        raise RuntimeError("pip install google-genai") from exc
    return genai.Client(api_key=api_key("GEMINI_API_KEY"))


def invoke(model: str, prompt: str, *, response_schema: dict[str, Any] | None = None) -> tuple[str, int, int]:
    """Return (text, prompt_tokens, completion_tokens). Raises RetryableError on 429/5xx."""
    from google.genai import types

    client = _client()
    config = types.GenerateContentConfig(
        temperature=0,  # a classifier with temperature is not reproducible
        response_mime_type="application/json",
        response_schema=response_schema,
    )
    try:
        resp = client.models.generate_content(model=model, contents=prompt, config=config)
    except Exception as exc:
        message = str(exc).lower()
        if any(marker in message for marker in _RETRYABLE_MARKERS):
            raise RetryableError(str(exc), retry_after=_retry_after(exc)) from exc
        raise

    usage = getattr(resp, "usage_metadata", None)
    ptok = getattr(usage, "prompt_token_count", 0) or 0
    ctok = getattr(usage, "candidates_token_count", 0) or 0
    return (resp.text or ""), ptok, ctok


def _retry_after(exc: BaseException) -> float | None:
    """Pull retryDelay out of a Gemini RESOURCE_EXHAUSTED payload when present."""
    import re

    match = re.search(r"retryDelay['\"]?\s*[:=]\s*['\"]?(\d+(?:\.\d+)?)s", str(exc))
    return float(match.group(1)) if match else None


def make_invoker(response_schema: dict[str, Any] | None = None):
    """Bind a schema, returning the (model, prompt) -> (text, ptok, ctok) callable
    the Router expects."""
    def _invoke(model: str, prompt: str) -> tuple[str, int, int]:
        return invoke(model, prompt, response_schema=response_schema)

    return _invoke
