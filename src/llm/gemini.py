"""Gemini provider. Structured output via response_schema, temperature 0.

`response_mime_type="application/json"` plus `response_schema` eliminates the
whole class of "the model wrapped it in a markdown fence" failures. On a free
tier where every retry costs quota that is worth more than it looks: a parse
failure is not just a lost batch, it is a lost slot in a 1,500-request day.
"""
from __future__ import annotations

from typing import Any

from src.config import api_key
from src.llm.router import QuotaExhausted, RetryableError

_RETRYABLE_MARKERS = (
    "429", "500", "503", "resource_exhausted", "unavailable", "deadline",
    "timeout", "timed out", "connection", "read operation",
)


# A hung request with no timeout blocks the whole run, silently and forever.
# Observed 24 Aug 2026: 175 calls averaging 7s each, then one call that never
# returned and a stage that sat there for 22 minutes producing nothing and
# printing nothing. A finite timeout turns that into a retryable error, which
# the router already knows how to handle.
REQUEST_TIMEOUT_MS = 120_000


def _client() -> Any:
    try:
        from google import genai
        from google.genai import types as _types
    except ImportError as exc:
        raise RuntimeError("pip install google-genai") from exc
    return genai.Client(
        api_key=api_key("GEMINI_API_KEY"),
        http_options=_types.HttpOptions(timeout=REQUEST_TIMEOUT_MS),
    )


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
        # A DAILY quota 429 is not retryable today. Distinguishing it from a
        # per-minute 429 matters more than it looks: treated as retryable, every
        # remaining batch burns five attempts with backoff against a wall that
        # will not move until midnight. That is how a run spends twelve hours
        # producing nothing instead of stopping cleanly and resuming tomorrow.
        if _is_daily_quota(str(exc)):
            raise QuotaExhausted(
                f"{model}: provider reports the DAILY free-tier quota is exhausted.\n"
                f"  {_quota_detail(str(exc))}\n"
                "  Not retryable today. Re-run after the provider's daily reset; the\n"
                "  run is resumable and will skip everything already labelled."
            ) from exc
        if any(marker in message for marker in _RETRYABLE_MARKERS):
            raise RetryableError(str(exc), retry_after=_retry_after(exc)) from exc
        raise

    usage = getattr(resp, "usage_metadata", None)
    ptok = getattr(usage, "prompt_token_count", 0) or 0
    ctok = getattr(usage, "candidates_token_count", 0) or 0
    return (resp.text or ""), ptok, ctok


# Google names the exhausted metric in the 429 body. A per-DAY metric cannot
# recover by waiting a minute; a per-minute one can.
_DAILY_MARKERS = (
    "free_tier_requests",       # generate_content_free_tier_requests - per day
    "per_day",
    "requests_per_day",
    "quota_metric.*day",
)


def _is_daily_quota(message: str) -> bool:
    import re

    low = message.lower()
    if "429" not in low and "resource_exhausted" not in low:
        return False
    if any(re.search(m, low) for m in _DAILY_MARKERS):
        # A per-minute metric mentioned alongside is not a daily exhaustion.
        return not re.search(r"per_minute|requests_per_minute|_per_min", low)
    return False


def _quota_detail(message: str) -> str:
    """Pull the human-readable 'Quota exceeded for metric ... limit: N' line out."""
    import re

    m = re.search(r"Quota exceeded for metric:[^\n\"']+", message)
    return m.group(0).strip() if m else message[:160]


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
