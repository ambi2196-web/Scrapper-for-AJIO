"""S1 - Apple App Store reviews (country: in), via Apple's own RSS JSON feed.

Not `app-store-scraper`. That library is listed in 04 §2.2 and is broken against
urllib3 v2: it imports `urllib3.packages.six.moves`, removed in the v2 release.
Pinning urllib3 back would have fixed the import while dragging a deprecated
transport into every other collector, so the dependency is dropped instead.

Apple publishes the same data itself at

    https://itunes.apple.com/{country}/rss/customerreviews/page={n}/id={app_id}/sortby=mostrecent/json

which needs no library beyond httpx, returns 50 reviews per page with ratings,
versions and timestamps, and is a first-party endpoint rather than a scrape.

Volume is low by construction - Apple caps public pagination at ten pages, so
~500 reviews per app. The value here is not prevalence in its own right; it is a
second structurally-matched surface. If a differential shows on Play and
vanishes on the App Store, that is information about the instrument rather than
about the brands, and it is worth knowing before the number reaches a slide.

This surface also carries the FULL comparison set from 03 §2 Tier 3 - AJIO,
Myntra and Nykaa Fashion - which Play cannot, because Nykaa Fashion has no
separately installable Play listing in India (see D4).
"""
from __future__ import annotations

import html
import re
from typing import Any, Iterator

from src.collect.base import Collector, CollectorError
from src.envelope import Envelope

FEED = (
    "https://itunes.apple.com/{country}/rss/customerreviews"
    "/page={page}/id={app_id}/sortby=mostrecent/json"
)
MAX_PAGES = 10          # Apple's public ceiling; page 11+ returns no entries
_TAGS = re.compile(r"<[^>]+>")


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
                "  Resolve it via https://itunes.apple.com/search?term=<name>&country=in&entity=software"
            )
        return entry

    def fetch(self) -> Iterator[Envelope]:
        import httpx

        app = self._app()
        country = self.cfg.get("country", "in")
        headers = {"User-Agent": self.user_agent, "Accept": "application/json"}

        returned = 0
        in_window = 0
        with httpx.Client(headers=headers, timeout=30.0, follow_redirects=True) as client:
            for page in range(1, MAX_PAGES + 1):
                url = FEED.format(country=country, page=page, app_id=app["app_id"])
                try:
                    resp = client.get(url)
                    resp.raise_for_status()
                    entries = (resp.json().get("feed") or {}).get("entry") or []
                except Exception as exc:
                    self.log_event("page_error", {"page": page, "error": str(exc)})
                    break

                # Page 1 prepends an app-metadata entry that has no im:rating.
                reviews = [e for e in entries if "im:rating" in e]
                if not reviews:
                    break

                for entry in reviews:
                    returned += 1
                    env = self._to_envelope(entry, app)
                    # Apple does not guarantee strict newest-first across pages,
                    # so filter rather than break - breaking early would silently
                    # truncate the window.
                    if not self.in_window(env.posted_at):
                        continue
                    in_window += 1
                    yield env

                self.log_event("page", {"page": page, "rows": len(reviews)})
                if page < MAX_PAGES:
                    self.sleep()

        self.log_event("run", {
            "app_id": app["app_id"], "returned": returned, "in_window": in_window,
            "window_days": self.window_days,
            "note": (
                "Apple caps public pagination at ~500 reviews. If in_window is much "
                "smaller than returned, this surface does not reach back a full "
                "window and its period parity must be checked before it carries a "
                "differential."
            ),
        })

    def _to_envelope(self, entry: dict[str, Any], app: dict[str, Any]) -> Envelope:
        def label(key: str) -> str:
            node = entry.get(key)
            return (node or {}).get("label", "") if isinstance(node, dict) else ""

        title = label("title").strip()
        body = _TAGS.sub("", html.unescape(label("content"))).strip()
        # Title and body are concatenated because App Store titles carry real
        # signal ("Sizes never match") and dropping them loses utterances.
        text = f"{title}. {body}".strip(". ").strip() if title else body

        rating = label("im:rating")
        votes = label("im:voteCount")

        return Envelope(
            source=self.name,
            brand=self.brand,
            source_id=f"as:{label('id')}",
            url=f"https://apps.apple.com/{self.cfg.get('country', 'in')}/app/id{app['app_id']}",
            posted_at=label("updated") or None,
            raw_text=text,
            rating=int(rating) if rating.isdigit() else None,
            helpful_votes=int(votes) if votes.isdigit() else None,
            meta={
                "app_id": app["app_id"],
                "title": title,
                "app_version": label("im:version"),
                # author is deliberately NOT stored - not needed for any analysis
                # here, and a public repo is a poor place for it.
            },
        )
