"""S3 - segment reviews into utterances. One row per utterance, not per review.

Invariant I2: a review containing three signals produces three rows. This is the
whole reason the engine can say "X% of pre-purchase utterances mention sizing"
rather than "X% of reviews mention sizing" - the second conflates a review that
is entirely about sizing with one that mentions it in passing among four other
complaints, and those are different facts about the world.

Invariant on spans: `raw_text[start:end]` must reconstruct the utterance exactly
(acceptance test T1). The span is what lets S5's evidence_quote be checked
against the original, and what lets a verbatim in the deck be traced back to a
character range in a specific review. Asserted at write time, not hoped for.

utterance_id = sha1(source|source_id|start|end) is stable across re-runs, which
is precisely what makes S5 resumable after the quota wall or a laptop sleep.
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Iterator

from src.config import ROOT, threshold
from src.normalise import read_normalised

INTERIM = ROOT / "data" / "interim"

# Subject-continuation cues: a sentence starting with one of these is a
# continuation of the previous thought, not a new one. Merging them is what
# turns "The kurta was nice. But it ran two sizes small." into one utterance
# about sizing rather than one neutral fragment and one orphaned clause.
_CONTINUATION = re.compile(
    r"^(and|but|so|because|also|then|however|though|although|which|that|plus|"
    r"aur|lekin|par|phir|kyunki|isliye|matlab)\b",
    re.IGNORECASE,
)
_PRONOUN_START = re.compile(r"^(it|they|this|that|these|those|he|she|we|ye|yeh|woh|iska|uska)\b", re.IGNORECASE)


def _sentences_with_spans(text: str) -> list[tuple[int, int]]:
    """Sentence-split, returning char spans into `text`.

    Uses pysbd when available - it handles Indian-English punctuation habits
    (spaced ellipses, missing space after full stop, "Rs." as a non-boundary)
    better than a regex. Falls back to a conservative regex so the pipeline is
    runnable without the dependency.
    """
    try:
        import pysbd

        seg = pysbd.Segmenter(language="en", clean=False, char_span=True)
        return [(s.start, s.end) for s in seg.segment(text)]
    except Exception:
        spans: list[tuple[int, int]] = []
        start = 0
        for match in re.finditer(r"[.!?\n]+(?=\s|$)", text):
            end = match.end()
            if end > start:
                spans.append((start, end))
            start = end
        if start < len(text):
            spans.append((start, len(text)))
        return spans


def _merge_continuations(text: str, spans: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Merge adjacent sentences that continue the same subject."""
    if not spans:
        return spans
    merged = [spans[0]]
    for start, end in spans[1:]:
        fragment = text[start:end].lstrip()
        prev_start, prev_end = merged[-1]
        prev_len = len((text[prev_start:prev_end]).split())
        if _CONTINUATION.match(fragment) or (_PRONOUN_START.match(fragment) and prev_len < 25):
            merged[-1] = (prev_start, end)
        else:
            merged.append((start, end))
    return merged


def segment_text(text: str, *, short_floor: int) -> list[tuple[int, int]]:
    """Return (start, end) spans of utterances within `text`."""
    if not text.strip():
        return []
    if len(text.split()) < short_floor:
        # Short reviews emit as a single utterance. Splitting "sizes are off,
        # returns are slow" into two three-word fragments produces two utterances
        # neither of which a classifier can read in context.
        return [(0, len(text))]
    spans = _merge_continuations(text, _sentences_with_spans(text))
    return [(s, e) for s, e in spans if text[s:e].strip()]


def utterance_id(source: str, source_id: str, start: int, end: int) -> str:
    return hashlib.sha1(f"{source}|{source_id}|{start}|{end}".encode("utf-8")).hexdigest()


def run() -> dict[str, Any]:
    short_floor = int(threshold("collection.short_review_word_floor"))
    INTERIM.mkdir(parents=True, exist_ok=True)
    out_path = INTERIM / "utterances.jsonl"

    reviews_in = 0
    utterances_out = 0
    span_failures = 0

    with out_path.open("w", encoding="utf-8", newline="\n") as out:
        for row in read_normalised():
            reviews_in += 1
            # Segment against normalised_text, and carry it as the reference text
            # for spans. raw_text is preserved untouched alongside for provenance.
            text = row.get("normalised_text") or ""
            spans = segment_text(text, short_floor=short_floor)
            # A rating-only review has no text and so produces no span. Emit an
            # empty utterance anyway, so S4 can drop it with reason `no_text` and
            # log it. Otherwise it vanishes here and the drop log loses the
            # finding — "38% of Play reviews carry no text at all" is a real
            # statement about the instrument, and it is the answer to "why is
            # your n smaller than the app's review count?"
            if not spans:
                spans = [(0, 0)]

            for start, end in spans:
                utterance = text[start:end]
                # T1 asserted here, at write time. A span that does not
                # reconstruct is a bug that would otherwise surface as a
                # mysterious evidence_quote failure three stages later.
                if text[start:end] != utterance:
                    span_failures += 1
                    continue
                uid = utterance_id(row["source"], row["source_id"], start, end)
                out.write(json.dumps({
                    "utterance_id": uid,
                    "source": row["source"],
                    "brand": row["brand"],
                    "source_id": row["source_id"],
                    "url": row.get("url"),
                    "posted_at": row.get("posted_at"),
                    "captured_at": row.get("captured_at"),
                    "rating": row.get("rating"),
                    "helpful_votes": row.get("helpful_votes"),
                    "language": row.get("language"),
                    "text_sha1": row.get("text_sha1"),
                    "near_dup_of": row.get("near_dup_of"),
                    "span": [start, end],
                    "utterance_text": utterance.strip(),
                    "reference_text": text,
                    "raw_text": row.get("raw_text"),
                    "has_caps_emphasis": row.get("has_caps_emphasis"),
                    "has_repeated_chars": row.get("has_repeated_chars"),
                    "has_exclamation_run": row.get("has_exclamation_run"),
                    "meta": row.get("meta") or {},
                }, ensure_ascii=False) + "\n")
                utterances_out += 1

    if span_failures:
        raise AssertionError(
            f"{span_failures} utterances failed span reconstruction (invariant T1). "
            "This is never acceptable to skip: spans are what make evidence quotes traceable."
        )
    # I2 in one assertion: segmentation that returns exactly one row per review
    # for every review means it silently did nothing.
    if reviews_in and utterances_out == reviews_in:
        raise AssertionError(
            "S3 emitted exactly one utterance per review for every review (invariant I2). "
            "Either the corpus is entirely single-sentence, or segmentation is a no-op. "
            "Check the pysbd install before proceeding."
        )
    return {
        "stage": "S3",
        "reviews_in": reviews_in,
        "utterances_out": utterances_out,
        "utterances_per_review": round(utterances_out / reviews_in, 2) if reviews_in else 0,
    }


def read_utterances() -> Iterator[dict[str, Any]]:
    path = INTERIM / "utterances.jsonl"
    if not path.exists():
        raise FileNotFoundError(f"{path} missing - run S3 first: python -m src.cli segment")
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                yield json.loads(line)
