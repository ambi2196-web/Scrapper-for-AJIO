"""Shared collector machinery: politeness, robots, caps, and the run report.

Every collector subclasses `Collector` and implements `fetch()`, yielding
Envelopes. Everything else - rate limiting, dedupe, the equal-cap rule, the
manifest - is handled here so that a new source is a small file rather than a
new opportunity to break an invariant.
"""
from __future__ import annotations

import abc
import datetime as _dt
import json
import random
import time
import urllib.parse
import urllib.robotparser
from typing import Any, Iterator

from src.config import ROOT, load_sources
from src.envelope import Envelope, RawWriter, now_ist

LOGS = ROOT / "logs"


class CollectorError(RuntimeError):
    pass


class Collector(abc.ABC):
    """One source. Emits envelopes; never classifies, never edits text."""

    name: str = ""

    def __init__(self, brand: str, cap: int | None = None, window_days: int | None = None) -> None:
        if not self.name:
            raise CollectorError("collector subclass must set `name`")
        self.brand = brand
        cfg = load_sources()
        self.defaults = cfg.get("defaults", {})
        self.cfg = cfg["sources"].get(self.name)
        if self.cfg is None:
            raise CollectorError(f"no config block for source {self.name!r} in sources.yaml")
        # `cap` is a safety ceiling on scrape time, NOT the sampling rule. The
        # sampling rule is the common window below - see sources.yaml for why
        # equal counts is the wrong instrument for the spec's own goal.
        self.cap = cap if cap is not None else cfg["brand_cap_per_source"]
        self.window_days = (
            window_days if window_days is not None else cfg.get("collection_window_days")
        )
        self._robots: dict[str, urllib.robotparser.RobotFileParser] = {}

    @property
    def window_start(self) -> _dt.datetime | None:
        """Inclusive lower bound on posted_at. None means no window (collect all)."""
        if not self.window_days:
            return None
        return _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(days=int(self.window_days))

    def in_window(self, posted_at: Any) -> bool:
        """True if this item falls inside the collection window.

        An item with no date is KEPT: dropping undated items would bias the
        corpus toward whichever source happens to expose timestamps, and the
        posted_at null rate is separately reported at S2 and gates trend claims.
        """
        start = self.window_start
        if start is None or posted_at is None:
            return True
        if isinstance(posted_at, str):
            try:
                posted_at = _dt.datetime.fromisoformat(posted_at.replace("Z", "+00:00"))
            except ValueError:
                return True
        if posted_at.tzinfo is None:
            posted_at = posted_at.replace(tzinfo=_dt.timezone.utc)
        return posted_at >= start

    # -- politeness ---------------------------------------------------------

    def sleep(self) -> None:
        """Jittered delay between pages. Hammering an unofficial scraper costs a day."""
        base = float(self.defaults.get("request_delay_seconds", 2.0))
        jitter = float(self.defaults.get("jitter_seconds", 1.0))
        time.sleep(base + random.uniform(0, jitter))

    @property
    def user_agent(self) -> str:
        return self.defaults.get("user_agent", "ajio-engine/0.1")

    def robots_allows(self, url: str) -> bool:
        """Check robots.txt for a URL, caching per origin.

        A disallow is honoured, not logged and ignored. The D3 decision is
        recorded in docs/decisions.md; this is its enforcement.
        """
        if not self.defaults.get("respect_robots_txt", True):
            return True
        parts = urllib.parse.urlparse(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        rp = self._robots.get(origin)
        if rp is None:
            rp = urllib.robotparser.RobotFileParser()
            rp.set_url(f"{origin}/robots.txt")
            try:
                rp.read()
            except Exception:
                # An unreachable robots.txt is not permission. Treat as disallow
                # and say so in the run report rather than proceeding quietly.
                self.log_event("robots_unreachable", {"origin": origin})
                rp = None  # type: ignore[assignment]
                self._robots[origin] = _DenyAll()  # type: ignore[assignment]
                return False
            self._robots[origin] = rp
        return self._robots[origin].can_fetch(self.user_agent, url)

    # -- logging ------------------------------------------------------------

    def log_event(self, kind: str, payload: dict[str, Any]) -> None:
        LOGS.mkdir(parents=True, exist_ok=True)
        record = {"at": now_ist(), "source": self.name, "brand": self.brand, "event": kind}
        record.update(payload)
        with (LOGS / "collect_log.jsonl").open("a", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")

    # -- the work -----------------------------------------------------------

    @abc.abstractmethod
    def fetch(self) -> Iterator[Envelope]:
        """Yield envelopes. Stop when the source exhausts; the cap is applied by run()."""

    def run(self) -> dict[str, Any]:
        emitted = 0
        newest: str | None = None
        oldest: str | None = None
        with RawWriter(self.name, self.brand) as writer:
            for env in self.fetch():
                writer.write(env)
                emitted += 1
                if env.posted_at:
                    stamp = str(env.posted_at)
                    if newest is None or stamp > newest:
                        newest = stamp
                    if oldest is None or stamp < oldest:
                        oldest = stamp
                if emitted >= self.cap:
                    # A ceiling hit means the window was NOT fully collected, so
                    # this brand's period is truncated relative to the others.
                    # Loud, because it silently breaks window parity.
                    self.log_event("safety_ceiling_hit", {
                        "cap": self.cap,
                        "warning": "window not fully collected; period parity is broken for this brand",
                    })
                    break
            stats = writer.stats
        stats["emitted_from_source"] = emitted
        stats["safety_ceiling"] = self.cap
        stats["window_days"] = self.window_days
        stats["observed_newest"] = newest
        stats["observed_oldest"] = oldest
        stats["observed_span_days"] = _span_days(newest, oldest)
        # A wrong package id / app id fails SILENTLY by returning an empty list
        # rather than raising. This is the assert that turns that into a stop.
        if emitted == 0:
            raise CollectorError(
                f"{self.name}/{self.brand} returned zero rows.\n"
                "  This is almost always a wrong id in config/sources.yaml, which\n"
                "  fails silently by returning [] rather than by raising.\n"
                "  Verify the id on the live listing before re-running."
            )
        self.log_event("run_complete", stats)
        return stats


class _DenyAll:
    def can_fetch(self, *_: object) -> bool:
        return False


def _span_days(newest: str | None, oldest: str | None) -> float | None:
    if not newest or not oldest:
        return None
    try:
        a = _dt.datetime.fromisoformat(str(newest).replace("Z", "+00:00"))
        b = _dt.datetime.fromisoformat(str(oldest).replace("Z", "+00:00"))
    except ValueError:
        return None
    return round(abs((a - b).total_seconds()) / 86400.0, 2)


def check_window_parity(
    stats_by_brand: dict[str, dict[str, Any]], tolerance_days: float
) -> list[str]:
    """T10, restated: per-brand observed PERIODS must agree, not per-brand counts.

    This is the check that 04 §2.1 was reaching for. Equal counts across brands
    with different review velocities produces unequal periods - measured at 40
    days for AJIO, 7 for Myntra and 1,094 for Urbanic at the same 4,000-row cap -
    which is exactly the contamination the original rule meant to prevent.

    Unequal n across brands is fine and is NOT flagged here: a proportion's
    denominator is its own n, and both the Wilson interval and the
    two-proportion z-test handle unequal n natively.
    """
    spans = {b: s.get("observed_span_days") for b, s in stats_by_brand.items()
             if s.get("observed_span_days") is not None}
    if len(spans) < 2:
        return []
    lo, hi = min(spans.values()), max(spans.values())
    problems: list[str] = []
    if (hi - lo) > tolerance_days:
        problems.append(
            f"per-brand observed windows differ by {hi - lo:.1f} days "
            f"(tolerance {tolerance_days}): {spans}. Proportions measured over "
            "different periods are not comparable - the longer slice spans "
            "different app versions, pricing regimes and seasons."
        )
    for brand, stats in stats_by_brand.items():
        if stats.get("safety_ceiling_hit"):
            problems.append(
                f"{brand} hit the safety ceiling, so its window is truncated and "
                "period parity is broken. Raise brand_cap_per_source or shorten the window."
            )
    return problems
