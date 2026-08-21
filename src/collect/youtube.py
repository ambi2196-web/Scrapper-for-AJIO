"""S1 - YouTube comments via Data API v3.

Quota shape drives the design. commentThreads.list costs 1 unit and returns up
to 100 comments, so the 10,000 unit/day free quota is ~1M comments of headroom -
quota is not the constraint. search.list costs 100 units, so ~20 searches is the
whole discovery budget for a day. Spend searches once, write the resulting video
ids into config/youtube_videos.yaml, and pull comments from that fixed list
thereafter. The video list is an appendix table for the same reason the PDP
sampling frame is: which videos you chose determines what you find, so it has to
be inspectable.

denominator_eligible: false. A haul video's comment section is an audience, not
a sample.
"""
from __future__ import annotations

from typing import Any, Iterator

import yaml

from src.collect.base import Collector, CollectorError
from src.config import ROOT, api_key
from src.envelope import Envelope

SEARCH_COST = 100
COMMENTS_COST = 1


class YouTubeCollector(Collector):
    name = "youtube"

    def __init__(self, brand: str, cap: int | None = None) -> None:
        super().__init__(brand, cap)
        self.units_used = 0

    def _client(self) -> Any:
        try:
            from googleapiclient.discovery import build
        except ImportError as exc:
            raise CollectorError("pip install google-api-python-client") from exc
        return build("youtube", "v3", developerKey=api_key("YOUTUBE_API_KEY"), cache_discovery=False)

    def _spend(self, units: int) -> None:
        cap = int(self.cfg.get("daily_quota_units", 10000))
        if self.units_used + units > cap:
            raise CollectorError(
                f"YouTube quota exhausted: {self.units_used}/{cap} units used. "
                "Quota resets at midnight Pacific. Work from the fixed video id list "
                "(config/youtube_videos.yaml) rather than re-searching."
            )
        self.units_used += units

    # -- video selection ----------------------------------------------------

    def video_ids(self) -> list[dict[str, Any]]:
        """Prefer the committed list; fall back to a metered search that writes it."""
        path = ROOT / self.cfg.get("video_id_list", "config/youtube_videos.yaml")
        if path.exists():
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            videos = [v for v in (data.get("videos") or []) if v.get("brand") == self.brand]
            if videos:
                self.log_event("using_committed_video_list", {"count": len(videos), "path": str(path)})
                return videos
        return self._discover(path)

    def _discover(self, path: Any) -> list[dict[str, Any]]:
        yt = self._client()
        queries = [
            f"{self.brand} haul",
            f"{self.brand} review",
            f"{self.brand} quality worth it",
            f"{self.brand} return refund experience",
            f"{self.brand} vs myntra",
        ]
        max_calls = int(self.cfg.get("max_search_calls", 20))
        found: list[dict[str, Any]] = []

        for query in queries[:max_calls]:
            self._spend(SEARCH_COST)
            try:
                resp = yt.search().list(
                    q=query, part="id,snippet", type="video", maxResults=25,
                    relevanceLanguage="en", regionCode="IN",
                ).execute()
            except Exception as exc:
                self.log_event("search_error", {"query": query, "error": str(exc)})
                continue
            for item in resp.get("items", []):
                found.append({
                    "video_id": item["id"]["videoId"],
                    "title": item["snippet"]["title"],
                    "channel": item["snippet"]["channelTitle"],
                    "published_at": item["snippet"]["publishedAt"],
                    "brand": self.brand,
                    "discovered_by_query": query,
                })
            self.sleep()

        # Persist so the 100-unit searches are never repeated, and so the
        # selection is inspectable as an appendix table.
        existing = {}
        if path.exists():
            existing = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        merged = {v["video_id"]: v for v in (existing.get("videos") or [])}
        merged.update({v["video_id"]: v for v in found})
        path.write_text(
            yaml.safe_dump({"videos": list(merged.values())}, sort_keys=False, allow_unicode=True),
            encoding="utf-8",
        )
        self.log_event("discovery_complete", {"videos": len(found), "units_used": self.units_used})
        return found

    # -- comments -----------------------------------------------------------

    def fetch(self) -> Iterator[Envelope]:
        yt = self._client()
        for video in self.video_ids():
            token = None
            while True:
                self._spend(COMMENTS_COST)
                try:
                    resp = yt.commentThreads().list(
                        part="snippet,replies", videoId=video["video_id"],
                        maxResults=100, textFormat="plainText", pageToken=token,
                    ).execute()
                except Exception as exc:
                    # Comments disabled on a video is routine, not an error worth stopping for.
                    self.log_event("comments_error", {"video": video["video_id"], "error": str(exc)})
                    break

                for thread in resp.get("items", []):
                    yield from self._thread_envelopes(thread, video)

                token = resp.get("nextPageToken")
                if not token:
                    break
                self.sleep()

    def _thread_envelopes(self, thread: dict[str, Any], video: dict[str, Any]) -> Iterator[Envelope]:
        top = thread["snippet"]["topLevelComment"]
        yield self._to_envelope(top, video, kind="top_level")
        for reply in (thread.get("replies") or {}).get("comments", []):
            yield self._to_envelope(reply, video, kind="reply")

    def _to_envelope(self, comment: dict[str, Any], video: dict[str, Any], kind: str) -> Envelope:
        snip = comment["snippet"]
        return Envelope(
            source=self.name,
            brand=self.brand,
            source_id=comment["id"],
            url=f"https://www.youtube.com/watch?v={video['video_id']}&lc={comment['id']}",
            posted_at=snip.get("publishedAt"),
            raw_text=snip.get("textOriginal") or snip.get("textDisplay") or "",
            rating=None,
            helpful_votes=snip.get("likeCount"),
            meta={
                "video_id": video["video_id"],
                "video_title": video.get("title"),
                "channel": video.get("channel"),
                "comment_kind": kind,
            },
        )
