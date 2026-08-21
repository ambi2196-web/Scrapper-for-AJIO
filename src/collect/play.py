"""S1 - Google Play reviews via google-play-scraper (unofficial, no API key).

Fetches both lang='en' and lang='hi'. The two calls return a partly different
slice of the same review pool, so the union is larger than either; dedupe on
source_id afterwards handles the overlap. The Hindi slice matters beyond volume:
it is where the Hinglish sits, and Hinglish is where the hesitation markers are
least likely to have been smoothed out by a user writing "properly" in English.
"""
from __future__ import annotations

from typing import Any, Iterator

from src.collect.base import Collector, CollectorError
from src.envelope import Envelope


class PlayCollector(Collector):
    name = "play"

    def _package_id(self) -> str:
        pkg = (self.cfg.get("brands") or {}).get(self.brand)
        if not pkg:
            raise CollectorError(
                f"no Play package id for brand {self.brand!r} in sources.yaml. "
                "Verify it on the live Play listing - a wrong id returns [] silently."
            )
        return pkg

    def fetch(self) -> Iterator[Envelope]:
        try:
            from google_play_scraper import Sort, reviews
        except ImportError as exc:
            raise CollectorError("pip install google-play-scraper") from exc

        pkg = self._package_id()
        sort_name = self.cfg.get("sort", "NEWEST")
        sort = getattr(Sort, sort_name)
        seen: set[str] = set()

        for lang in self.cfg.get("langs", ["en"]):
            token = None
            pages = 0
            per_lang = 0
            while True:
                try:
                    batch, token = reviews(
                        pkg,
                        lang=lang,
                        country=self.cfg.get("country", "in"),
                        sort=sort,
                        count=200,
                        continuation_token=token,
                    )
                except Exception as exc:
                    self.log_event("page_error", {"lang": lang, "page": pages, "error": str(exc)})
                    break

                if not batch:
                    break

                for row in batch:
                    rid = row.get("reviewId")
                    if not rid or rid in seen:
                        continue
                    seen.add(rid)
                    per_lang += 1
                    yield self._to_envelope(row, pkg, lang)

                pages += 1
                self.log_event("page", {"lang": lang, "page": pages, "rows": len(batch)})
                if token is None:
                    break
                # Half the cap per language, so neither language starves the other
                # and the en/hi mix stays comparable across brands.
                if per_lang >= self.cap:
                    break
                self.sleep()

            self.log_event("lang_complete", {"lang": lang, "collected": per_lang, "pages": pages})

    def _to_envelope(self, row: dict[str, Any], pkg: str, lang: str) -> Envelope:
        posted = row.get("at")
        return Envelope(
            source=self.name,
            brand=self.brand,
            source_id=str(row["reviewId"]),
            url=f"https://play.google.com/store/apps/details?id={pkg}&reviewId={row['reviewId']}",
            posted_at=posted.isoformat() if hasattr(posted, "isoformat") else posted,
            raw_text=row.get("content") or "",
            rating=row.get("score"),
            helpful_votes=row.get("thumbsUpCount"),
            meta={
                "package": pkg,
                "query_lang": lang,
                "app_version": row.get("reviewCreatedVersion"),
                "reply": row.get("replyContent"),
                # user_name is deliberately NOT stored. It is not needed for any
                # analysis in the engine, and a public repo is a poor place for it.
            },
        )
