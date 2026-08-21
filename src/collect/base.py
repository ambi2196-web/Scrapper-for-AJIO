"""Shared collector machinery: politeness, robots, caps, and the run report.

Every collector subclasses `Collector` and implements `fetch()`, yielding
Envelopes. Everything else - rate limiting, dedupe, the equal-cap rule, the
manifest - is handled here so that a new source is a small file rather than a
new opportunity to break an invariant.
"""
from __future__ import annotations

import abc
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

    def __init__(self, brand: str, cap: int | None = None) -> None:
        if not self.name:
            raise CollectorError("collector subclass must set `name`")
        self.brand = brand
        cfg = load_sources()
        self.defaults = cfg.get("defaults", {})
        self.cfg = cfg["sources"].get(self.name)
        if self.cfg is None:
            raise CollectorError(f"no config block for source {self.name!r} in sources.yaml")
        # Equal caps across brands are load-bearing for the differential (04 §2.1).
        self.cap = cap if cap is not None else cfg["brand_cap_per_source"]
        self._robots: dict[str, urllib.robotparser.RobotFileParser] = {}

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
        with RawWriter(self.name, self.brand) as writer:
            for env in self.fetch():
                writer.write(env)
                emitted += 1
                if emitted >= self.cap:
                    self.log_event("cap_reached", {"cap": self.cap})
                    break
            stats = writer.stats
        stats["emitted_from_source"] = emitted
        stats["cap"] = self.cap
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


def check_equal_caps(stats_by_brand: dict[str, dict[str, Any]], tolerance: float) -> list[str]:
    """Acceptance test T10: per-brand collected counts within tolerance of each other.

    An unequal cap contaminates the differential because the two proportions
    would be measured on differently-deep slices of the review timeline - the
    older slice carries a different app version, a different pricing regime and
    a different set of complaints.
    """
    counts = {b: s.get("total_on_disk", 0) for b, s in stats_by_brand.items()}
    if len(counts) < 2:
        return []
    lo, hi = min(counts.values()), max(counts.values())
    if hi == 0:
        return ["all brands collected zero rows"]
    if (hi - lo) / hi > tolerance:
        return [
            f"per-brand counts differ by more than {tolerance:.0%}: {counts}. "
            "Comparing proportions across unequal depths is not a defensible differential; "
            "truncate the deeper brands to the shallowest count before S7."
        ]
    return []
