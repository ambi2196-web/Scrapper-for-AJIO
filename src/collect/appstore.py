"""S1 - Apple App Store reviews (country: in).

Low volume by construction - Apple caps public review pagination hard. The value
here is not prevalence in its own right; it is a second structurally-matched
surface. If a differential shows on Play and vanishes on the App Store, that is
information about the instrument rather than about the brands, and it is worth
knowing before the number reaches a slide.
"""
from __future__ import annotations

from typing import Any, Iterator

from src.collect.base import Collector, CollectorError
from src.envelope import Envelope


class AppStoreCollector(Collector):
    name = "appstore"

    def _app(self) -> dict[str, Any]:
        entry = (self.cfg.get("brands") or {}).get(self.brand)
        if not entry or not entry.get("app_id"):
            raise CollectorError(
                f"no verified App Store app_id for brand {self.brand!r}.\n"
                "  sources.yaml ships null for unverified ids on purpose, so this\n"
                "  stops rather than collecting an empty slice that looks like a\n"
                "  real finding of 'no reviews'.\n"
                "  Find the id in the App Store URL: /app/<name>/id<APP_ID>"
            )
        return entry

    def fetch(self) -> Iterator[Envelope]:
        try:
            from app_store_scraper import AppStore
        except ImportError as exc:
            raise CollectorError("pip install app-store-scraper") from exc

        app = self._app()
        scraper = AppStore(
            country=self.cfg.get("country", "in"),
            app_name=app["app_name"],
            app_id=app["app_id"],
        )
        # how_many is a ceiling, not a promise; Apple returns what it returns.
        scraper.review(how_many=self.cap, sleep=self.defaults.get("request_delay_seconds", 2))

        for row in scraper.reviews:
            yield self._to_envelope(row, app)

        self.log_event("run", {"app_id": app["app_id"], "returned": len(scraper.reviews)})

    def _to_envelope(self, row: dict[str, Any], app: dict[str, Any]) -> Envelope:
        posted = row.get("date")
        # app-store-scraper exposes no stable review id, so derive a deterministic
        # one. It must be stable across re-runs or dedupe and T3 both break.
        import hashlib

        raw = f"{app['app_id']}|{posted}|{row.get('title','')}|{row.get('review','')}"
        source_id = "as:" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:20]

        title = (row.get("title") or "").strip()
        body = (row.get("review") or "").strip()
        # Title and body are concatenated because App Store titles carry real
        # signal ("Sizes never match") and dropping them loses utterances.
        text = f"{title}. {body}".strip(". ").strip() if title else body

        return Envelope(
            source=self.name,
            brand=self.brand,
            source_id=source_id,
            url=f"https://apps.apple.com/in/app/{app['app_name']}/id{app['app_id']}",
            posted_at=posted.isoformat() if hasattr(posted, "isoformat") else posted,
            raw_text=text,
            rating=row.get("rating"),
            helpful_votes=None,
            meta={"app_id": app["app_id"], "title": title, "developer_response": row.get("developerResponse")},
        )
