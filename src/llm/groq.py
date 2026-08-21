"""Groq provider - lane C, the blind second annotator.

Groq's JSON mode (`response_format={"type": "json_object"}`) is looser than
Gemini's schema mode: it guarantees parseable JSON, not JSON of the right shape.
So validation on receipt is not optional here, and anything that fails goes to
quarantine rather than being coerced.

Blindness is the point of this lane and it is enforced at the call site, not by
convention: the prompt this module is handed must never contain lane A's labels.
A "please check this label" framing produces agreement bias, and agreement
produced by framing is not inter-annotator agreement - it would inflate kappa
while measuring nothing, which is worse than not running the lane at all.
"""
from __future__ import annotations

from typing import Any

from src.config import api_key
from src.llm.router import RetryableError

_RETRYABLE_STATUS = {429, 500, 502, 503, 504}


def _client() -> Any:
    try:
        from groq import Groq
    except ImportError as exc:
        raise RuntimeError("pip install groq") from exc
    return Groq(api_key=api_key("GROQ_API_KEY"))


def invoke(model: str, prompt: str) -> tuple[str, int, int]:
    """Return (text, prompt_tokens, completion_tokens). Raises RetryableError on 429/5xx."""
    client = _client()
    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
            response_format={"type": "json_object"},
        )
    except Exception as exc:
        status = getattr(exc, "status_code", None) or getattr(getattr(exc, "response", None), "status_code", None)
        if status in _RETRYABLE_STATUS:
            raise RetryableError(str(exc), retry_after=_retry_after(exc)) from exc
        if "rate" in str(exc).lower() or "429" in str(exc):
            raise RetryableError(str(exc), retry_after=_retry_after(exc)) from exc
        raise

    usage = resp.usage
    return (
        resp.choices[0].message.content or "",
        getattr(usage, "prompt_tokens", 0) or 0,
        getattr(usage, "completion_tokens", 0) or 0,
    )


def _retry_after(exc: BaseException) -> float | None:
    headers = getattr(getattr(exc, "response", None), "headers", None)
    if headers:
        value = headers.get("retry-after") or headers.get("Retry-After")
        if value:
            try:
                return float(value)
            except ValueError:
                return None
    return None


def make_invoker():
    def _invoke(model: str, prompt: str) -> tuple[str, int, int]:
        return invoke(model, prompt)

    return _invoke
