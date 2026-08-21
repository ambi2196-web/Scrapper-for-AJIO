"""S4 - filter. The drop log is an output, not housekeeping.

"38% of Play Store reviews carry no text at all" is a real finding about the
instrument. It also pre-empts the obvious question - why is your n smaller than
the app's review count? - which is the kind of question that, unanswered, makes
an evaluator distrust every other number on the slide.

So every drop writes a line to logs/drop_log.jsonl with its reason, and the
aggregate by reason and source goes into the appendix.
"""
from __future__ import annotations

import collections
import json
import re
from typing import Any, Iterator

from src.config import ROOT, load_sources, threshold
from src.envelope import now_ist
from src.segment import read_utterances

INTERIM = ROOT / "data" / "interim"
LOGS = ROOT / "logs"

DROP_REASONS = ("no_text", "too_short", "spam", "not_shopping", "wrong_brand", "near_dup")

_STOPWORDS = {
    "a", "an", "the", "is", "was", "are", "were", "be", "been", "am", "i", "me", "my",
    "it", "its", "this", "that", "of", "to", "in", "on", "for", "and", "or", "but",
    "so", "very", "too", "so", "hai", "ka", "ki", "ke", "ko", "se", "me", "mein",
}

_URL = re.compile(r"https?://|www\.", re.IGNORECASE)

# Pure app-quality / content-free praise. These are not shopping utterances: they
# carry no friction and no decision, so keeping them inflates every denominator
# without contributing a numerator to any opportunity area.
_NOT_SHOPPING = re.compile(
    r"^\W*(nice|good|best|super|awesome|excellent|bad|worst|ok|okay|fine|great|"
    r"nyc|mast|badhiya|accha|acha|thik|theek|useless|osm|wow|thanks?|thank you)"
    r"[\s\W]*(app|application|service|shopping|experience)?[\s\W]*$",
    re.IGNORECASE,
)
_CRASH_ONLY = re.compile(
    r"^\W*(app\s+)?(is\s+)?(crash\w*|not\s+work\w*|hang\w*|slow|lag\w*|"
    r"login\s+(issue|problem)|not\s+open\w*)[\s\W]*$",
    re.IGNORECASE,
)

_BRANDS = {
    "ajio": re.compile(r"\bajio\b", re.IGNORECASE),
    "myntra": re.compile(r"\bmyntra\b", re.IGNORECASE),
    "nykaa_fashion": re.compile(r"\bnykaa\b", re.IGNORECASE),
}


def _tokens(text: str) -> list[str]:
    return [t for t in re.findall(r"[\w']+", text.lower()) if t not in _STOPWORDS]


def classify_drop(
    row: dict[str, Any],
    *,
    min_tokens: int,
    spam_repeat_limit: int,
    text_counts: collections.Counter,
    brand_from_surface: bool = True,
    near_dup_min_words: int = 10,
) -> str | None:
    """Return a drop reason, or None to keep. Order matters: most specific first."""
    text = (row.get("utterance_text") or "").strip()

    if not text:
        return "no_text"

    toks = _tokens(text)
    if len(toks) < min_tokens:
        return "too_short"

    # near_dup is checked AFTER too_short, and only above a word floor.
    #
    # The rule exists for syndication (04 §2.8) - the same complaint posted to
    # three sites would triple-count. It is not meant to catch the thousands of
    # people who independently write "good". Checked first and unfloored, it
    # fired 31,278 times on this corpus with 98.1% of hits at <=4 words, which
    # would have reported the corpus as half-duplicated when it is really half
    # one-to-four-word generic praise.
    #
    # The denominator was never affected - those rows fail too_short either way -
    # but the drop log is an appendix table, and a drop reason that tells the
    # wrong story about the instrument is a finding stated falsely.
    if len(text.split()) >= near_dup_min_words:
        if row.get("near_dup_of") and row["near_dup_of"] != f"{row['source']}:{row['source_id']}":
            return "near_dup"

    # A URL in a short review is spammy regardless of length - no floor here.
    if _URL.search(text) and len(toks) < 15:
        return "spam"

    # The repeated-text branch gets the SAME word floor as near_dup, for the same
    # reason. 04 §3 aims this rule at "promo/seller boilerplate", which is long
    # and distinctive. Unfloored, it flagged 1,671 rows of which every single one
    # was 3-8 words and none contained a URL - "best shopping app" x221 is 221
    # people independently writing a natural phrase, not a spam campaign.
    #
    # Left in place it would have put a four-figure "spam" count in the appendix
    # and implied the corpus was being manipulated.
    if len(text.split()) >= near_dup_min_words:
        if text_counts[row.get("text_sha1")] > spam_repeat_limit:
            return "spam"

    if _NOT_SHOPPING.match(text) or _CRASH_ONLY.match(text):
        return "not_shopping"

    # wrong_brand applies ONLY where the surface does not already establish the
    # brand. Under com.ril.ajio every review is about AJIO whether or not the
    # word appears — and it usually does not, because the reviewer knows which
    # app they opened. Applying a text-mention rule there would drop precisely
    # the comparison reviews ("cheaper than Myntra") that are the most
    # informative rows in the corpus.
    #
    # On Reddit and YouTube the opposite holds: a thread mentions several
    # retailers and attribution has to come from the text, so the rule earns its
    # place. Declared per source in sources.yaml rather than hard-coded here.
    if not brand_from_surface:
        review_text = row.get("reference_text") or text
        mentioned = {b for b, pat in _BRANDS.items() if pat.search(review_text)}
        own = _BRANDS.get(row["brand"])
        if mentioned and row["brand"] not in mentioned and not (own and own.search(review_text)):
            return "wrong_brand"

    return None


def run() -> dict[str, Any]:
    min_tokens = int(threshold("collection.min_tokens_after_stopwords"))
    spam_repeat_limit = int(threshold("collection.spam_repeat_source_ids"))
    near_dup_min_words = int(threshold("collection.near_dup_min_words"))

    surface_brand = {
        name: bool(c.get("brand_established_by_surface", False))
        for name, c in load_sources()["sources"].items()
    }
    rows = list(read_utterances())
    # Count distinct source_ids per text hash: boilerplate repeats across
    # reviewers, whereas a genuinely common phrase repeats within one.
    by_hash: dict[str, set[str]] = collections.defaultdict(set)
    for row in rows:
        by_hash[row.get("text_sha1")].add(row.get("source_id"))
    text_counts = collections.Counter({h: len(ids) for h, ids in by_hash.items()})

    INTERIM.mkdir(parents=True, exist_ok=True)
    LOGS.mkdir(parents=True, exist_ok=True)
    out_path = INTERIM / "filtered.jsonl"
    drop_path = LOGS / "drop_log.jsonl"

    kept = 0
    drops: collections.Counter = collections.Counter()
    drops_by_source: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    totals_by_source: collections.Counter = collections.Counter()

    with out_path.open("w", encoding="utf-8", newline="\n") as out, \
            drop_path.open("a", encoding="utf-8", newline="\n") as dlog:
        for row in rows:
            totals_by_source[row["source"]] += 1
            reason = classify_drop(
                row, min_tokens=min_tokens, spam_repeat_limit=spam_repeat_limit,
                text_counts=text_counts,
                brand_from_surface=surface_brand.get(row["source"], False),
                near_dup_min_words=near_dup_min_words,
            )
            if reason:
                drops[reason] += 1
                drops_by_source[row["source"]][reason] += 1
                dlog.write(json.dumps({
                    "at": now_ist(),
                    "utterance_id": row["utterance_id"],
                    "source": row["source"],
                    "brand": row["brand"],
                    "source_id": row["source_id"],
                    "reason": reason,
                    "text": (row.get("utterance_text") or "")[:280],
                }, ensure_ascii=False) + "\n")
                continue
            out.write(json.dumps(row, ensure_ascii=False) + "\n")
            kept += 1

    report = {
        "at": now_ist(),
        "stage": "S4",
        "utterances_in": len(rows),
        "kept": kept,
        "dropped": sum(drops.values()),
        "drop_rate": round(sum(drops.values()) / len(rows), 4) if rows else 0,
        "by_reason": dict(drops),
        "by_source": {
            s: {
                "total": totals_by_source[s],
                "dropped": dict(drops_by_source[s]),
                "drop_rate": round(sum(drops_by_source[s].values()) / totals_by_source[s], 4),
            }
            for s in totals_by_source
        },
        "note": "This table goes in the appendix. The drop profile is itself a finding about each instrument.",
    }
    with (LOGS / "s4_report.jsonl").open("a", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(report, ensure_ascii=False) + "\n")
    return report


def derive_near_dup_floor(drop_log: Any = None) -> dict[str, Any]:
    """Re-derive the near-dup word floor from an observed collision distribution.

    The floor sits where the collision count PLATEAUS: below it, identical text
    is coincidence (independent short praise); at and above it, the count stops
    falling, which is what genuine duplication looks like.
    """
    import collections as _c
    import json as _j

    path = drop_log or (LOGS / "drop_log.jsonl")
    rows = [_j.loads(l) for l in open(path, encoding="utf-8") if l.strip()]
    nd = [r for r in rows if r["reason"] == "near_dup"]
    if not nd:
        return {"floor": None, "note": "no near_dup drops logged; nothing to derive from"}

    lens = _c.Counter(len(r["text"].split()) for r in nd)
    remaining, curve = len(nd), []
    for w in range(1, 21):
        remaining -= lens.get(w, 0)
        curve.append({"min_words": w + 1, "collisions_remaining": remaining})

    floor = None
    for i in range(1, len(curve) - 1):
        if curve[i]["collisions_remaining"] == curve[i + 1]["collisions_remaining"]:
            floor = curve[i]["min_words"]
            break
    return {"floor": floor, "curve": curve,
            "rule": "first word count at which the collision count stops falling"}


def read_filtered() -> Iterator[dict[str, Any]]:
    path = INTERIM / "filtered.jsonl"
    if not path.exists():
        raise FileNotFoundError(f"{path} missing - run S4 first: python -m src.cli filter")
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                yield json.loads(line)
