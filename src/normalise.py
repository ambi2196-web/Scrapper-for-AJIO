"""S2 - normalise. Unicode, language, near-duplicate detection, null-rate report.

Three things this stage must NOT do, each because it destroys signal the engine
depends on:

1. No translation (invariant I7). Translating Hinglish to English destroys the
   hesitation markers - "lu ya nahi" and "should I buy it" are not the same
   utterance, and the first is a much stronger deferral signal because nobody
   writes it while confident.
2. No lowercasing of stored text. ALL CAPS is a severity signal. Matchers
   lowercase a copy.
3. No stripping of repeated characters. "sooooo bad" collapses to "sooo bad" -
   capped, so the text is comparable, but the emphasis survives.
"""
from __future__ import annotations

import collections
import hashlib
import json
import re
import unicodedata
from typing import Any, Iterable, Iterator

from src.config import ROOT, load_lexicon
from src.envelope import now_ist, read_raw

INTERIM = ROOT / "data" / "interim"
LOGS = ROOT / "logs"

_ZERO_WIDTH = dict.fromkeys(map(ord, "​‌‍⁠﻿"), None)
_CONTROL = {c: None for c in range(0x20) if c not in (0x09, 0x0A, 0x0D)}


def normalise_text(text: str, *, collapse_to: int = 3, min_run: int = 4) -> str:
    """NFC, strip zero-width and control chars, cap runs of identical chars."""
    text = unicodedata.normalize("NFC", text or "")
    text = text.translate(_ZERO_WIDTH).translate(_CONTROL)
    # Cap runs at `collapse_to` rather than deleting them: emphasis is kept but
    # "soooo" and "sooooooo" become the same string, so they dedupe correctly.
    pattern = r"(.)\1{" + str(min_run - 1) + r",}"
    text = re.sub(pattern, lambda m: m.group(1) * collapse_to, text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def text_hash(text: str) -> str:
    """Hash for the near-duplicate pass. Case- and space-insensitive on a COPY."""
    canon = re.sub(r"[^\w\s]", "", text.lower())
    canon = re.sub(r"\s+", " ", canon).strip()
    return hashlib.sha1(canon.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------
# Language
# --------------------------------------------------------------------------

_DEVANAGARI = re.compile(r"[ऀ-ॿ]")
_WORD = re.compile(r"[a-z]+")


def detect_language(text: str, hindi_words: set[str]) -> str:
    """Four-way label: en | hi | hinglish | other.

    Hinglish is Latin script plus Hindi function-word hits. A word-list rule is
    used rather than a model because it is auditable: when an evaluator asks how
    Hinglish was identified, a list in config/lexicon.yaml is an answer and a
    model's internal state is not.
    """
    if not text.strip():
        return "other"
    if _DEVANAGARI.search(text):
        return "hi"
    tokens = _WORD.findall(text.lower())
    if not tokens:
        return "other"
    hits = sum(1 for t in tokens if t in hindi_words)
    # Two hits, or one in a short utterance, is enough: Hindi function words are
    # not English words, so a hit is close to unambiguous.
    if hits >= 2 or (hits == 1 and len(tokens) <= 8):
        return "hinglish"
    return "en"


def emphasis_flags(text: str, lex: dict[str, Any]) -> dict[str, bool]:
    cfg = lex.get("emphasis_markers", {})
    min_len = int(cfg.get("all_caps_min_word_len", 4))
    excl = int(cfg.get("exclamation_run_min", 2))
    caps_words = [w for w in re.findall(r"\b[A-Z]{%d,}\b" % min_len, text)]
    return {
        "has_caps_emphasis": len(caps_words) > 0,
        "has_repeated_chars": bool(re.search(r"(.)\1{2,}", text)),
        "has_exclamation_run": bool(re.search(r"!{%d,}" % excl, text)),
    }


# --------------------------------------------------------------------------
# Stage entrypoint
# --------------------------------------------------------------------------

def run(source: str | None = None, brand: str | None = None) -> dict[str, Any]:
    """Normalise all raw envelopes into data/interim/normalised.jsonl."""
    lex = load_lexicon()
    hindi_words = set(lex.get("hindi_function_words_latin") or [])
    emph = lex.get("emphasis_markers", {})

    INTERIM.mkdir(parents=True, exist_ok=True)
    out_path = INTERIM / "normalised.jsonl"

    seen_hashes: dict[str, str] = {}
    null_posted = collections.Counter()
    total_by_source = collections.Counter()
    lang_counts: collections.Counter = collections.Counter()
    near_dups = 0
    written = 0

    with out_path.open("w", encoding="utf-8", newline="\n") as out:
        for row in read_raw(source, brand):
            src = row["source"]
            total_by_source[src] += 1
            if not row.get("posted_at"):
                null_posted[src] += 1

            clean = normalise_text(
                row.get("raw_text") or "",
                collapse_to=int(emph.get("repeated_char_collapse_to", 3)),
                min_run=int(emph.get("repeated_char_min_run", 4)),
            )
            digest = text_hash(clean)
            first_seen = seen_hashes.get(digest)
            if first_seen is None:
                seen_hashes[digest] = f"{src}:{row['source_id']}"
            else:
                near_dups += 1

            lang = detect_language(clean, hindi_words)
            lang_counts[lang] += 1

            record = dict(row)
            record["normalised_text"] = clean
            record["text_sha1"] = digest
            record["near_dup_of"] = first_seen
            record["language"] = lang
            record.update(emphasis_flags(row.get("raw_text") or "", lex))
            out.write(json.dumps(record, ensure_ascii=False) + "\n")
            written += 1

    report = {
        "at": now_ist(),
        "stage": "S2",
        "rows_in": sum(total_by_source.values()),
        "rows_out": written,
        "near_duplicates_flagged": near_dups,
        "language_distribution": dict(lang_counts),
        "posted_at_null_rate_by_source": {
            s: round(null_posted[s] / total_by_source[s], 4) for s in total_by_source
        },
        "note": (
            "posted_at null rates feed thresholds.collection.posted_at_null_rate_trend_cutoff. "
            "A source above the cutoff may not carry a trend claim. Derive the cutoff from "
            "these observed rates - do not pick a round number."
        ),
    }
    LOGS.mkdir(parents=True, exist_ok=True)
    with (LOGS / "s2_report.jsonl").open("a", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(report, ensure_ascii=False) + "\n")
    return report


def read_normalised() -> Iterator[dict[str, Any]]:
    path = INTERIM / "normalised.jsonl"
    if not path.exists():
        raise FileNotFoundError(f"{path} missing - run S2 first: python -m src.cli normalise")
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                yield json.loads(line)
