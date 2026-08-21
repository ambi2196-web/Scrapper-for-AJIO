"""S1 - Reddit via PRAW. Mechanism and verbatims only; never a denominator.

Reddit is the only source in the engine carrying real closure and intent
language - people narrate the whole decision, including the part where they
didn't buy. That makes it the best place to *discover* a mechanism and the worst
place to *size* one: the population is small, self-selected, skewed metro and
English-fluent, and a rate computed from it would describe Reddit rather than
AJIO's customers. sources.yaml marks it denominator_eligible: false and S7
enforces that in code, so the temptation cannot be acted on later.

Comments matter more than submissions here. A submission is usually a question;
the reasoning lives in the replies.
"""
from __future__ import annotations

import datetime as _dt
from typing import Any, Iterator

from src.collect.base import Collector, CollectorError
from src.config import api_key
from src.envelope import Envelope


class RedditCollector(Collector):
    name = "reddit"

    def _client(self) -> Any:
        try:
            import praw
        except ImportError as exc:
            raise CollectorError("pip install praw") from exc
        return praw.Reddit(
            client_id=api_key("REDDIT_CLIENT_ID"),
            client_secret=api_key("REDDIT_CLIENT_SECRET"),
            user_agent=api_key("REDDIT_USER_AGENT"),
            check_for_async=False,
        )

    def fetch(self) -> Iterator[Envelope]:
        reddit = self._client()
        query = self.cfg.get("search_all_query", "ajio")
        subs = list(self.cfg.get("subreddits") or [])
        seen: set[str] = set()

        # Named subreddits first - higher precision - then an r/all sweep for reach.
        for sub_name in subs + ["all"]:
            try:
                subreddit = reddit.subreddit(sub_name)
                results = subreddit.search(query, sort="new", time_filter="year", limit=250)
                for submission in results:
                    yield from self._walk_submission(submission, sub_name, seen)
                    self.sleep()
            except Exception as exc:
                self.log_event("subreddit_error", {"subreddit": sub_name, "error": str(exc)})
                continue

    def _walk_submission(self, submission: Any, sub_name: str, seen: set[str]) -> Iterator[Envelope]:
        if submission.id not in seen:
            seen.add(submission.id)
            body = f"{submission.title}\n\n{submission.selftext or ''}".strip()
            yield self._to_envelope(
                source_id=f"t3_{submission.id}",
                text=body,
                created=submission.created_utc,
                url=f"https://reddit.com{submission.permalink}",
                score=submission.score,
                meta={"kind": "submission", "subreddit": str(submission.subreddit), "queried_in": sub_name},
            )

        if not self.cfg.get("collect_comments", True):
            return

        try:
            submission.comments.replace_more(limit=0)  # skip "load more" stubs; they cost calls
            for comment in submission.comments.list():
                if comment.id in seen or not getattr(comment, "body", None):
                    continue
                if comment.body in ("[deleted]", "[removed]"):
                    continue
                seen.add(comment.id)
                yield self._to_envelope(
                    source_id=f"t1_{comment.id}",
                    text=comment.body,
                    created=comment.created_utc,
                    url=f"https://reddit.com{comment.permalink}",
                    score=comment.score,
                    meta={
                        "kind": "comment",
                        "subreddit": str(submission.subreddit),
                        "queried_in": sub_name,
                        "parent_submission": submission.id,
                    },
                )
        except Exception as exc:
            self.log_event("comment_error", {"submission": submission.id, "error": str(exc)})

    def _to_envelope(
        self, *, source_id: str, text: str, created: float, url: str, score: int, meta: dict[str, Any]
    ) -> Envelope:
        return Envelope(
            source=self.name,
            brand=self.brand,
            source_id=source_id,
            url=url,
            posted_at=_dt.datetime.fromtimestamp(created, tz=_dt.timezone.utc).isoformat(),
            raw_text=text,
            rating=None,
            helpful_votes=score,
            meta=meta,
        )
