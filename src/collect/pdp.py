"""S1 - AJIO PDP Q&A and reviews. The reference source, and the hardest.

Why this source is worth disproportionate build time: a question asked on a
product page is a person hesitating in public, in the present tense, before
buying. Every other source in this engine is retrospective - a review is written
by someone who already bought, and their account of why they hesitated is a
reconstruction. PDP Q&A is the only public text that is pre-purchase by
construction rather than by classification. It is therefore the reference source
for the opportunity index, and the one place where `temporal_stance` is
corroborated by the surface itself rather than inferred from wording.

If time collapses: scrape Q&A, skip PDP reviews.

Two modes:
  xhr        - replay the captured JSON endpoint. Fast. Requires a live capture.
  playwright - headless render + scroll. ~5-10x slower. Always works.

The endpoint is NOT hard-coded in this repo. AJIO's PDP is a client-rendered
app and its review payload path changes; a guessed endpoint returns 404 or, far
worse, a 200 with a different shape. Capture it live (docs/pdp_capture.md) and
write it into sources.yaml.
"""
from __future__ import annotations

import json
import random
from typing import Any, Iterator

from src.collect.base import Collector, CollectorError
from src.config import ROOT
from src.envelope import Envelope, now_ist

FRAME_LOG = ROOT / "logs" / "pdp_sampling_frame.jsonl"


class PDPCollector(Collector):
    """Base for the two PDP surfaces. Subclasses set `name` and `_parse`."""

    name = "pdp_qa"

    def __init__(self, brand: str = "ajio", cap: int | None = None) -> None:
        super().__init__(brand, cap)
        self.mode = self.cfg.get("mode", "xhr")

    # -- sampling frame -----------------------------------------------------

    def product_codes(self) -> list[dict[str, Any]]:
        """Return the stratified product sample, and record the frame.

        Do NOT scrape whatever is on the homepage. Homepage placement correlates
        with promotion, promotion correlates with price and stock behaviour, and
        stock behaviour is one of the things being measured - so a homepage
        sample would build the answer into the sampling. Same objection applies
        to a top-sorted category list: popular items have systematically
        different stock behaviour from the tail.

        The frame is an appendix table, so it is written to logs/ on every run.
        """
        sampling = self.cfg.get("sampling") or {}
        categories = sampling.get("categories")
        per_stratum = sampling.get("products_per_stratum")
        if not categories:
            raise CollectorError(
                "sources.yaml pdp_qa.sampling.categories is null - blocked on D1 "
                "(category scope) in 03_engine_spec.md.\n"
                "  Narrow the category set once, write down why, then fill it in.\n"
                "  Scraping 'whatever is on the homepage' is not a fallback; it "
                "correlates with promotion, which correlates with the stock "
                "behaviour being measured."
            )
        if not per_stratum:
            raise CollectorError(
                "sources.yaml pdp_qa.sampling.products_per_stratum is null. "
                "Derive it from the target n (config/thresholds.yaml)."
            )

        frame = self._build_frame(categories, int(per_stratum))
        FRAME_LOG.parent.mkdir(parents=True, exist_ok=True)
        with FRAME_LOG.open("a", encoding="utf-8", newline="\n") as fh:
            for row in frame:
                fh.write(json.dumps({"at": now_ist(), **row}, ensure_ascii=False) + "\n")
        return frame

    def _build_frame(self, categories: list[str], per_stratum: int) -> list[dict[str, Any]]:
        """Stratify across the rating and review-count distributions, not the top list.

        Implementation note: populating this needs a category listing crawl that
        records each product's rating and review count, then buckets them. That
        crawl is the same XHR/Playwright machinery as below and is written once
        the listing endpoint is captured alongside the review endpoint.
        """
        raise CollectorError(
            "_build_frame is not implemented yet - it needs the captured category "
            "listing endpoint (docs/pdp_capture.md step 2).\n"
            "  Interim path: hand-assemble a stratified product-code list as\n"
            "  config/pdp_products.yaml with rating_band and review_count_band per\n"
            "  entry, and point _build_frame at it. The frame is an appendix table\n"
            "  either way, so hand-assembly is acceptable provided it is recorded."
        )

    # -- D3 pre-flight ------------------------------------------------------

    def robots_preflight(self) -> None:
        """D3, settled 22 Aug 2026: if robots.txt disallows, PDP is CUT.

        The pre-commitment matters because the alternative is available and
        tempting: Playwright renders a page whether or not robots.txt permits
        fetching it, so a disallow could be quietly routed around by switching
        `mode`. Deciding in advance - and failing here rather than at the
        collector - is what makes "we honoured robots.txt" a true sentence
        instead of one that survived only because nobody checked.

        A cut is not a failure. It is recorded in docs/decisions.md and stated
        in the method line, and the engine still has Play, App Store and the
        qualitative sources.
        """
        probe = "https://www.ajio.com/p/000000000"
        if self.robots_allows(probe):
            self.log_event("robots_preflight_ok", {"probe": probe})
            return
        self.log_event("robots_preflight_disallow", {"probe": probe, "action": "PDP cut per D3"})
        raise CollectorError(
            "PDP collection is CUT: robots.txt disallows the product path, or is "
            "unreachable (which this engine treats as a disallow rather than as "
            "permission).\n"
            "  D3 pre-commits to cutting the source rather than switching to\n"
            "  Playwright, which would render a page robots.txt asked us not to\n"
            "  fetch. Deciding this in advance is what makes the ToS position real.\n"
            "  Record the cut in the method line; Play + App Store still carry the\n"
            "  differential, and Reddit/YouTube still carry mechanism."
        )

    # -- fetching -----------------------------------------------------------

    def fetch(self) -> Iterator[Envelope]:
        self.robots_preflight()
        products = self.product_codes()
        if self.mode == "xhr":
            yield from self._fetch_xhr(products)
        elif self.mode == "playwright":
            yield from self._fetch_playwright(products)
        else:
            raise CollectorError(f"unknown pdp mode {self.mode!r}; expected xhr|playwright")

    def _fetch_xhr(self, products: list[dict[str, Any]]) -> Iterator[Envelope]:
        import httpx

        endpoint = self.cfg.get("endpoint")
        if not endpoint:
            raise CollectorError(
                "sources.yaml pdp_qa.endpoint is null.\n"
                "  Capture it live: Chrome DevTools -> Network -> filter XHR -> open a\n"
                "  PDP -> find the request returning the review/Q&A payload. Note its\n"
                "  path, query params (product code, page, size) and required headers.\n"
                "  See docs/pdp_capture.md. Do not guess it from the spec - it has changed."
            )

        headers = {"User-Agent": self.user_agent, "Accept": "application/json"}
        headers.update(self.cfg.get("headers") or {})

        with httpx.Client(headers=headers, timeout=30.0, follow_redirects=True) as client:
            for product in products:
                code = product["product_code"]
                page = 0
                while True:
                    url = endpoint.format(product_code=code, page=page, size=50)
                    if not self.robots_allows(url):
                        self.log_event("robots_disallow", {"url": url})
                        break
                    try:
                        resp = client.get(url)
                        resp.raise_for_status()
                        payload = resp.json()
                    except Exception as exc:
                        self.log_event("fetch_error", {"product": code, "page": page, "error": str(exc)})
                        break

                    rows = self._extract_rows(payload)
                    if not rows:
                        break
                    for row in rows:
                        yield self._to_envelope(row, product)
                    page += 1
                    self.sleep()

    def _fetch_playwright(self, products: list[dict[str, Any]]) -> Iterator[Envelope]:
        """Fallback: headless render, scroll-to-load, parse the DOM.

        Slower but immune to endpoint drift. If the XHR capture has not worked by
        noon on the PDP day, switch here immediately rather than spending the
        afternoon on the capture - the source matters more than the method.
        """
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise CollectorError("pip install playwright && playwright install chromium") from exc

        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            ctx = browser.new_context(user_agent=self.user_agent, locale="en-IN")
            page = ctx.new_page()
            try:
                for product in products:
                    url = product.get("url") or f"https://www.ajio.com/p/{product['product_code']}"
                    if not self.robots_allows(url):
                        self.log_event("robots_disallow", {"url": url})
                        continue
                    try:
                        page.goto(url, wait_until="networkidle", timeout=45000)
                        self._scroll_to_load(page)
                        rows = self._parse_dom(page)
                    except Exception as exc:
                        self.log_event("render_error", {"product": product["product_code"], "error": str(exc)})
                        continue
                    for row in rows:
                        yield self._to_envelope(row, product)
                    self.sleep()
            finally:
                ctx.close()
                browser.close()

    @staticmethod
    def _scroll_to_load(page: Any, max_scrolls: int = 25) -> None:
        last = 0
        for _ in range(max_scrolls):
            page.mouse.wheel(0, 4000)
            page.wait_for_timeout(600 + int(random.uniform(0, 400)))
            height = page.evaluate("document.body.scrollHeight")
            if height == last:
                break
            last = height

    # -- parsing (endpoint-shape dependent) ---------------------------------

    def _extract_rows(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        """Pull the list of Q&A / review objects out of the JSON payload.

        Shape depends on the captured endpoint, so this is filled in after the
        capture. Keep it a pure function of the payload - it is the only place
        that knows AJIO's response shape, and that containment is what makes an
        endpoint change a one-function fix.
        """
        for key in ("questions", "reviews", "content", "data", "results", "items"):
            value = payload.get(key)
            if isinstance(value, list):
                return value
            if isinstance(value, dict):
                for inner in ("questions", "reviews", "content", "items"):
                    if isinstance(value.get(inner), list):
                        return value[inner]
        self.log_event("unrecognised_payload", {"keys": sorted(payload.keys())[:20]})
        return []

    def _parse_dom(self, page: Any) -> list[dict[str, Any]]:
        raise CollectorError(
            "_parse_dom needs selectors from a live PDP. Fill after capture; see "
            "docs/pdp_capture.md step 3."
        )

    def _to_envelope(self, row: dict[str, Any], product: dict[str, Any]) -> Envelope:
        import hashlib

        text = (
            row.get("questionText")
            or row.get("question")
            or row.get("reviewText")
            or row.get("comment")
            or row.get("text")
            or ""
        )
        answer = row.get("answerText") or row.get("answer")
        native_id = row.get("id") or row.get("questionId") or row.get("reviewId")
        if native_id is None:
            native_id = hashlib.sha1(
                f"{product['product_code']}|{text}".encode("utf-8")
            ).hexdigest()[:20]

        return Envelope(
            source=self.name,
            brand=self.brand,
            source_id=f"{product['product_code']}:{native_id}",
            url=product.get("url"),
            posted_at=row.get("createdAt") or row.get("date") or row.get("postedAt"),
            raw_text=text,
            rating=row.get("rating"),
            helpful_votes=row.get("helpfulCount") or row.get("likeCount"),
            meta={
                "product_code": product["product_code"],
                "category": product.get("category"),
                "rating_band": product.get("rating_band"),
                "review_count_band": product.get("review_count_band"),
                # The seller/brand answer is kept as meta, never as an utterance:
                # it is not a shopper speaking and would pollute any denominator.
                "answer_text": answer,
                "surface": self.name,
            },
        )


class PDPQACollector(PDPCollector):
    """Q&A. Higher priority than reviews - see the module docstring."""

    name = "pdp_qa"


class PDPReviewCollector(PDPCollector):
    name = "pdp_reviews"
