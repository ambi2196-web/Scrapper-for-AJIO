"""S1 - complaint aggregators: PissedConsumer, Trustpilot, MouthShut, Reviews.io.

These are severity and verbatim sources. Their selection bias is not a caveat
you can size and then correct for - it is structural. People arrive at a
complaint site because they have a complaint, so the base rate of complaint on a
complaint site is definitionally near 1, and any proportion computed from that
denominator is a statement about the site's function rather than about the
brand. All four are denominator_eligible: false in sources.yaml, and S7 refuses
to aggregate them (acceptance test T9).

What they are genuinely good for: the severity-3 tail. The utterances where
someone says they will never use the service again are rare in app-store reviews
and common here, and severity-3 verbatims are what make an opportunity area
legible on a slide.

Syndication is a real hazard: the same complaint is often posted to three sites.
Dedupe at S1 catches identical source_ids only, so the near-duplicate pass at S2
on sha1(normalised_text) is what actually prevents triple-counting.
"""
from __future__ import annotations

import hashlib
from typing import Any, Iterator
from urllib.parse import urljoin

from src.collect.base import Collector, CollectorError
from src.envelope import Envelope

# Per-site selectors. Kept as data rather than code so that a site redesign is a
# config edit rather than a rewrite, and so what was scraped stays inspectable.
SITE_RULES: dict[str, dict[str, str]] = {
    "pissedconsumer": {
        "list": "div.review-item, article.review",
        "text": ".review-text, .description",
        "title": ".review-title, h2",
        "date": "time",
        "rating": ".rating-value",
        "next": "a.next, a[rel=next]",
    },
    "trustpilot": {
        "list": "article[data-service-review-card-paper]",
        "text": "p[data-service-review-text-typography]",
        "title": "h2[data-service-review-title-typography]",
        "date": "time",
        "rating": "div[data-service-review-rating] img",
        "next": "a[name=pagination-button-next]",
    },
    "mouthshut": {
        "list": "div.review-article, li.review",
        "text": "div.more, .review-text",
        "title": "strong.reviewdata-title, h2",
        "date": "div.review-date, time",
        "rating": "span.rating",
        "next": "a.next",
    },
}


class ComplaintsCollector(Collector):
    name = "complaints"

    def __init__(self, brand: str = "ajio", cap: int | None = None, site: str | None = None) -> None:
        super().__init__(brand, cap)
        self.site_filter = site

    def _sites(self) -> list[dict[str, Any]]:
        sites = [s for s in (self.cfg.get("sites") or []) if s.get("base")]
        if self.site_filter:
            sites = [s for s in sites if s["name"] == self.site_filter]
        if not sites:
            raise CollectorError("no complaint sites with a base URL configured")
        return sites

    def fetch(self) -> Iterator[Envelope]:
        try:
            import httpx
            from selectolax.parser import HTMLParser
        except ImportError as exc:
            raise CollectorError("pip install httpx selectolax") from exc

        headers = {"User-Agent": self.user_agent, "Accept-Language": "en-IN,en;q=0.9"}
        with httpx.Client(headers=headers, timeout=30.0, follow_redirects=True) as client:
            for site in self._sites():
                rules = SITE_RULES.get(site["name"])
                if not rules:
                    self.log_event("no_rules", {"site": site["name"]})
                    continue
                yield from self._crawl(client, HTMLParser, site, rules)

    def _crawl(self, client: Any, HTMLParser: Any, site: dict[str, Any], rules: dict[str, str]) -> Iterator[Envelope]:
        url = site["base"]
        pages = 0
        while url and pages < 100:
            if not self.robots_allows(url):
                self.log_event("robots_disallow", {"url": url})
                return
            try:
                resp = client.get(url)
                resp.raise_for_status()
            except Exception as exc:
                self.log_event("fetch_error", {"url": url, "error": str(exc)})
                return

            tree = HTMLParser(resp.text)
            nodes = tree.css(rules["list"])
            if not nodes:
                self.log_event("no_reviews_matched", {"url": url, "selector": rules["list"]})
                return

            for node in nodes:
                env = self._node_to_envelope(node, rules, site, url)
                if env is not None:
                    yield env

            nxt = tree.css_first(rules["next"])
            href = nxt.attributes.get("href") if nxt else None
            url = urljoin(url, href) if href else None
            pages += 1
            self.sleep()

    def _node_to_envelope(self, node: Any, rules: dict[str, str], site: dict[str, Any], page_url: str) -> Envelope | None:
        def text_of(selector: str) -> str:
            el = node.css_first(selector)
            return el.text(strip=True) if el else ""

        body = text_of(rules["text"])
        title = text_of(rules["title"])
        if not body and not title:
            return None
        combined = f"{title}. {body}".strip(". ").strip() if title else body

        # These sites rarely expose a stable id in the listing, so hash the
        # content. Stability across re-runs is what makes T3 pass.
        source_id = "cs:" + hashlib.sha1(
            f"{site['name']}|{combined}".encode("utf-8")
        ).hexdigest()[:20]

        return Envelope(
            source=self.name,
            brand=self.brand,
            source_id=source_id,
            url=page_url,
            posted_at=text_of(rules["date"]) or None,
            raw_text=combined,
            rating=None,
            helpful_votes=None,
            meta={"site": site["name"], "title": title, "rating_text": text_of(rules["rating"])},
        )
