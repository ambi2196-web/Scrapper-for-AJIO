"""Provider routing, rate limiting, retry and the budget ledger.

This module is where free-tier builds fail, so it is built first and everything
downstream depends on it.

The core design decision: limits are enforced BEFORE sending, not in reaction to
429s, and the reservation counts EXPECTED OUTPUT as well as the prompt. On
Groq's 8,000 tokens-per-minute budget a B=20 batch measures ~10,300 tokens -
over the whole minute in one call - because gpt-oss-120b reasons before
answering and its output exceeds its input. Reserving only the prompt would wave
that call through and collect the 429 afterwards, when the minute is already
gone.

The second design decision: the daily counters are persistent. A limiter that
resets when the process restarts will burn a 1,500-request daily quota by
mid-afternoon across four crashed runs and leave nothing for the evening, which
is exactly when the bulk classification pass wants to be running. Daily state is
replayed from logs/llm_ledger.jsonl on boot.

Routing (see 04 §4.2), re-verified against the live API 22 Aug 2026:
  Lane A - Gemini 3.5 Flash-Lite - bulk classification, full corpus
  Lane B - Gemini 3.5 Flash      - escalation for confidence < tau, B=1
  Lane C - Groq gpt-oss-120b     - blind second annotator, stratified sample

The 2.5 models the spec names are gone (404, "no longer available to new
users"), so lanes A and B moved a generation.

Lane C is not a cheaper copy of lane A. It is a different model family from a
different vendor, because a second pass by the same model measures nothing - a
model agrees with itself, and self-agreement is not evidence.
"""
from __future__ import annotations

import argparse
import dataclasses
import datetime as _dt
import json
import random
import threading
import time
from typing import Any, Callable

from src.config import ROOT
from src.envelope import now_ist

LEDGER = ROOT / "logs" / "llm_ledger.jsonl"
DEFERRED = ROOT / "logs" / "deferred"


@dataclasses.dataclass(frozen=True)
class Limits:
    """Published free-tier ceilings. RE-VERIFY BEFORE EACH RUN - these move."""

    rpm: int
    rpd: int
    tpm: int
    tpd: int | None = None
    # Hours offset from UTC at which the PROVIDER's daily counter resets.
    # Google resets at midnight US/Pacific; anchoring to local midnight instead
    # makes the limiter believe a fresh day has started up to 12.5 hours early,
    # which is how a resume runs straight into a wall it thinks is gone.
    reset_utc_offset_hours: float = -7.0


# Re-verified against the live API on 22 Aug 2026, as 04 §4.1 instructs.
#
# TWO THINGS CHANGED since the spec was written, both caught by a smoke test
# rather than by the bulk run failing halfway:
#
# 1. The models are gone. Both `gemini-2.5-flash-lite` and `gemini-2.5-flash`
#    return 404 "no longer available to new users". They still appear in
#    models.list(), so listing them is not proof they can be called - only a
#    real call is. Lanes A and B move to the 3.5 generation, which keeps the
#    escalation meaningful: lane B is a more capable model in the SAME family,
#    which is what the spec intends by escalation.
#
# 2. Google no longer publishes per-model free-tier limits in the docs - they
#    defer to an authenticated AI Studio dashboard. The first values here were
#    guessed conservatively at rpd=1000 and were WRONG BY 2x: the provider's own
#    429 names the real figure, "limit: 500, model: gemini-3.5-flash-lite", and
#    the run hit it after ~8,700 utterances.
#
#    Corrected to the measured 500. The lesson is in the correction: a guess in
#    the generous direction is not conservative, it just moves the failure to
#    the middle of a long run. Where a provider states a limit in an error, that
#    statement is the citation.
#
# Groq's limits ARE authoritative - read from x-ratelimit-* response headers on
# 22 Aug 2026: 1,000 requests/day, 8,000 tokens/minute. Both match the spec.
#
# Models are PINNED, not aliased. `gemini-flash-latest` also works but could
# shift under a running job, and a classifier whose model changes mid-corpus has
# no single classifier_version and no defensible count.
LANES: dict[str, dict[str, Any]] = {
    "A": {
        "provider": "gemini",
        "model": "gemini-3.5-flash-lite",
        "role": "bulk classification (S5 pass 1, full corpus)",
        # rpd from the provider's own 429 on 22 Aug 2026: "limit: 500".
        "limits": Limits(rpm=15, rpd=500, tpm=250_000),
        # Measured 22 Aug 2026: 425 output tokens against 1,572 input.
        "output_ratio": 0.4,
    },
    "B": {
        "provider": "gemini",
        "model": "gemini-3.5-flash",
        "role": "escalation for confidence < tau, single-utterance calls",
        # Unverified - lane B has not yet hit its wall. Kept low deliberately:
        # under-claiming costs throughput, over-claiming costs a run.
        "limits": Limits(rpm=10, rpd=100, tpm=250_000),
        "output_ratio": 0.6,
    },
    "C": {
        "provider": "groq",
        "model": "openai/gpt-oss-120b",
        "role": "blind second annotator (independent, stratified sample)",
        # TPD is the binding limit, and it is FAR tighter than the spec assumed.
        # 04 §4.1 estimated ~1,300 utterances/day. Measured 22 Aug 2026, this
        # model emits ~396 output tokens PER UTTERANCE, so at B=10 a call costs
        # ~5,860 tokens for 10 utterances and the 200k/day cap allows ~341.
        # Roughly a quarter of the spec's figure, which is why lane C is a small
        # stratified sample rather than a second full pass.
        "limits": Limits(rpm=30, rpd=1000, tpm=8_000, tpd=200_000, reset_utc_offset_hours=0.0),
        # Measured 22 Aug 2026: 1,556 output tokens against 1,397 input - this
        # model reasons before answering, so output EXCEEDS input. Reserving only
        # the prompt would let a batch through that blows the 8K minute.
        "output_ratio": 2.5,
    },
}


class QuotaExhausted(RuntimeError):
    """Raised when a daily ceiling is hit. Not retryable today."""


class TokenBucket:
    """Per-minute bucket for requests and tokens. Blocks rather than 429s."""

    def __init__(self, rpm: int, tpm: int) -> None:
        self.rpm = rpm
        self.tpm = tpm
        self._requests: list[float] = []
        self._tokens: list[tuple[float, int]] = []
        self._lock = threading.Lock()

    def _prune(self, now: float) -> None:
        cutoff = now - 60.0
        self._requests = [t for t in self._requests if t > cutoff]
        self._tokens = [(t, n) for t, n in self._tokens if t > cutoff]

    def acquire(self, est_tokens: int) -> float:
        """Block until both budgets allow the call. Returns seconds waited."""
        waited = 0.0
        while True:
            with self._lock:
                now = time.monotonic()
                self._prune(now)
                used_tokens = sum(n for _, n in self._tokens)
                req_ok = len(self._requests) < self.rpm
                tok_ok = used_tokens + est_tokens <= self.tpm
                if req_ok and tok_ok:
                    self._requests.append(now)
                    self._tokens.append((now, est_tokens))
                    return waited
                # Sleep until the oldest entry in the binding window expires.
                oldest = min(
                    ([self._requests[0]] if not req_ok and self._requests else [])
                    + ([self._tokens[0][0]] if not tok_ok and self._tokens else [])
                )
                sleep_for = max(0.1, 60.0 - (now - oldest) + 0.05)
            time.sleep(sleep_for)
            waited += sleep_for


class DailyCounter:
    """RPD/TPD counters replayed from the ledger, so a restart does not reset them."""

    def __init__(self) -> None:
        self.requests: dict[tuple[str, str], int] = {}
        self.tokens: dict[tuple[str, str], int] = {}
        self._replay()

    @staticmethod
    def _window_start(offset_hours: float) -> _dt.datetime:
        """Start of the provider's current quota day, as an absolute instant."""
        tz = _dt.timezone(_dt.timedelta(hours=offset_hours))
        now_local = _dt.datetime.now(tz)
        return now_local.replace(hour=0, minute=0, second=0, microsecond=0)

    def _replay(self) -> None:
        if not LEDGER.exists():
            return
        # One window per lane, because providers reset on different clocks.
        windows = {
            (cfg["provider"], cfg["model"]): self._window_start(cfg["limits"].reset_utc_offset_hours)
            for cfg in LANES.values()
        }
        with LEDGER.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                # A rejected call does not consume provider quota. Counting 429s
                # as spend made twelve hours of failed retries look like 12 hours
                # of consumption and hid how much quota was actually left.
                if entry.get("outcome") != "ok":
                    continue
                key = (entry.get("provider"), entry.get("model"))
                start = windows.get(key)
                if start is None:
                    continue
                try:
                    when = _dt.datetime.fromisoformat(str(entry.get("at")))
                except ValueError:
                    continue
                if when.tzinfo is None:
                    when = when.replace(tzinfo=_dt.timezone.utc)
                if when < start:
                    continue
                self.requests[key] = self.requests.get(key, 0) + 1
                total = int(entry.get("prompt_tokens") or 0) + int(entry.get("completion_tokens") or 0)
                self.tokens[key] = self.tokens.get(key, 0) + total

    def check(self, provider: str, model: str, limits: Limits, est_tokens: int) -> None:
        key = (provider, model)
        if self.requests.get(key, 0) >= limits.rpd:
            resets_at = self._window_start(limits.reset_utc_offset_hours) + _dt.timedelta(days=1)
            local = resets_at.astimezone()
            raise QuotaExhausted(
                f"{provider}/{model}: daily request cap reached "
                f"({self.requests.get(key, 0)}/{limits.rpd}). "
                f"Provider quota resets {resets_at.isoformat(timespec='minutes')} "
                f"(= {local.strftime('%Y-%m-%d %H:%M %Z')} local)."
            )
        if limits.tpd is not None and self.tokens.get(key, 0) + est_tokens > limits.tpd:
            raise QuotaExhausted(
                f"{provider}/{model}: daily token cap would be exceeded "
                f"({self.tokens.get(key, 0)} + {est_tokens} > {limits.tpd}). "
                "This is the binding limit on Groq's free tier - it is why lane C "
                "is a sample rather than a full second pass."
            )

    def record(self, provider: str, model: str, prompt_tokens: int, completion_tokens: int) -> None:
        key = (provider, model)
        self.requests[key] = self.requests.get(key, 0) + 1
        self.tokens[key] = self.tokens.get(key, 0) + prompt_tokens + completion_tokens

    def status(self) -> list[dict[str, Any]]:
        rows = []
        for lane, cfg in LANES.items():
            key = (cfg["provider"], cfg["model"])
            lim: Limits = cfg["limits"]
            used_r = self.requests.get(key, 0)
            used_t = self.tokens.get(key, 0)
            rows.append({
                "lane": lane,
                "provider": cfg["provider"],
                "model": cfg["model"],
                "role": cfg["role"],
                "requests_used": used_r,
                "requests_cap": lim.rpd,
                "requests_left": max(0, lim.rpd - used_r),
                "tokens_used": used_t,
                "tokens_cap": lim.tpd,
                "tokens_left": (max(0, lim.tpd - used_t) if lim.tpd else None),
                "cost_usd": 0.0,
            })
        return rows


def estimate_tokens(text: str, lane: str | None = None) -> int:
    """Conservative pre-send estimate, INCLUDING expected output.

    Counting only the prompt is the mistake that makes an 8,000 TPM budget
    unusable. Measured 22 Aug 2026, Groq's gpt-oss-120b returned 1,556 output
    tokens for a 3-utterance batch - it reasons verbosely, so output dominates
    and can exceed input several times over. A limiter that reserves only the
    prompt lets an oversized call through, the real usage blows the minute, and
    the 429 arrives after the damage rather than before it. On an 8K/minute
    budget that costs the whole minute.

    So the reservation is prompt + prompt x output_ratio, where the ratio is
    per-lane and measured rather than assumed.
    """
    prompt_tokens = int(len(text) / 3.2) + 32
    ratio = LANES.get(lane, {}).get("output_ratio", 1.0) if lane else 1.0
    return prompt_tokens + int(prompt_tokens * ratio)


class Router:
    """Single entry point for every LLM call in the pipeline."""

    def __init__(self, max_attempts: int = 5) -> None:
        self.max_attempts = max_attempts
        self.daily = DailyCounter()
        self._buckets: dict[str, TokenBucket] = {
            lane: TokenBucket(cfg["limits"].rpm, cfg["limits"].tpm)
            for lane, cfg in LANES.items()
        }
        LEDGER.parent.mkdir(parents=True, exist_ok=True)
        DEFERRED.mkdir(parents=True, exist_ok=True)

    def call(
        self,
        lane: str,
        prompt: str,
        *,
        invoke: Callable[[str, str], tuple[str, int, int]],
        batch_id: str | None = None,
    ) -> str | None:
        """Run one call on `lane`. Returns the raw response text, or None if deferred.

        `invoke(model, prompt) -> (text, prompt_tokens, completion_tokens)` is
        supplied by the provider module, so this stays provider-agnostic and
        testable without network access.
        """
        cfg = LANES[lane]
        provider, model, limits = cfg["provider"], cfg["model"], cfg["limits"]
        est = estimate_tokens(prompt, lane)

        self.daily.check(provider, model, limits, est)
        waited = self._buckets[lane].acquire(est)

        last_error: str | None = None
        for attempt in range(1, self.max_attempts + 1):
            started = time.monotonic()
            try:
                text, ptok, ctok = invoke(model, prompt)
            except Exception as exc:  # provider modules raise RetryableError for 429/5xx
                last_error = f"{type(exc).__name__}: {exc}"
                latency = time.monotonic() - started
                self._log(lane, provider, model, 0, 0, latency, attempt, "error", last_error, waited)
                if not _is_retryable(exc) or attempt == self.max_attempts:
                    break
                time.sleep(_backoff(attempt, exc))
                continue

            latency = time.monotonic() - started
            self.daily.record(provider, model, ptok, ctok)
            self._log(lane, provider, model, ptok, ctok, latency, attempt, "ok", None, waited)
            return text

        # Park rather than stall. One bad batch must never hold up the run - the
        # deferred queue is drained at the end, when quota shape is known.
        if batch_id:
            self._defer(lane, batch_id, prompt, last_error)
        return None

    def _defer(self, lane: str, batch_id: str, prompt: str, error: str | None) -> None:
        path = DEFERRED / f"{lane}_{batch_id}.json"
        path.write_text(
            json.dumps({"lane": lane, "batch_id": batch_id, "prompt": prompt,
                        "error": error, "deferred_at": now_ist()}, ensure_ascii=False),
            encoding="utf-8",
        )

    def _log(
        self, lane: str, provider: str, model: str, ptok: int, ctok: int,
        latency: float, attempt: int, outcome: str, error: str | None, waited: float,
    ) -> None:
        with LEDGER.open("a", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps({
                "at": now_ist(), "lane": lane, "provider": provider, "model": model,
                "prompt_tokens": ptok, "completion_tokens": ctok,
                "latency_s": round(latency, 3), "rate_limit_wait_s": round(waited, 3),
                "attempt": attempt, "outcome": outcome, "error": error,
                "cost_usd": 0.0,
            }, ensure_ascii=False) + "\n")


class RetryableError(RuntimeError):
    """429/500/503. Carries Retry-After when the provider supplied one."""

    def __init__(self, message: str, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


def _is_retryable(exc: BaseException) -> bool:
    return isinstance(exc, RetryableError)


def _backoff(attempt: int, exc: BaseException) -> float:
    """Exponential backoff with FULL jitter, honouring Retry-After when present.

    Full jitter rather than equal jitter because several batches can retry
    together after a shared 429; without full randomisation they resynchronise
    and hit the next window as a thundering herd.
    """
    if isinstance(exc, RetryableError) and exc.retry_after:
        return float(exc.retry_after) + random.uniform(0, 1.0)
    return random.uniform(0, min(60.0, 2.0 ** attempt))


def print_status() -> None:
    rows = DailyCounter().status()
    print(f"LLM quota status  ({_dt.datetime.now().strftime('%Y-%m-%d %H:%M')} local)")
    print("-" * 92)
    for r in rows:
        tok = (f"{r['tokens_used']:,}/{r['tokens_cap']:,}" if r["tokens_cap"] else f"{r['tokens_used']:,}/-")
        print(f"  lane {r['lane']}  {r['provider']:<7} {r['model']:<24} "
              f"req {r['requests_used']:>5}/{r['requests_cap']:<5} "
              f"tok {tok:>17}")
        print(f"           {r['role']}")
    print("-" * 92)
    print("  total spend: $0.00 (free tiers only)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="LLM router")
    parser.add_argument("--status", action="store_true", help="print quota consumed/remaining today")
    args = parser.parse_args()
    if args.status:
        print_status()
    else:
        parser.print_help()
