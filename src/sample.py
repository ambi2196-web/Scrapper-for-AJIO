"""S4b - random downsample within the collection window, to bound the LLM budget.

Why this stage exists. B10 replaced equal per-brand counts with a common time
window, because equal counts across brands whose review velocity differs ~100x
produces wildly unequal periods. That fixes comparability but leaves volume
unbounded: a 90-day window yields roughly 11k AJIO reviews, ~50k Myntra and ~330
Urbanic, which after segmentation exceeds a day of Gemini's free-tier quota
several times over.

So: collect the whole window, then sample within it.

Two properties this must have, and both are easy to get wrong:

1. **The sample is RANDOM, never the newest n.** Taking the newest n would
   silently reintroduce exactly the unequal windows B10 exists to eliminate -
   Myntra's newest 6,000 is a week, AJIO's is a month. Random sampling within
   the window preserves the period.

2. **Sampling does not bias the proportion.** A uniform random sample within a
   cell leaves every area's share unchanged in expectation, and the Wilson
   interval widens correctly to reflect the smaller n. What is lost is
   precision, which is visible, rather than validity, which would not be.

The realised sample fraction is recorded per cell so the appendix can state it.
"""
from __future__ import annotations

import collections
import json
import random
from typing import Any, Iterator

from src.config import ROOT, load_sources
from src.envelope import now_ist
from src.filter import read_filtered

INTERIM = ROOT / "data" / "interim"
LOGS = ROOT / "logs"

# Fixed so a re-run reproduces the same sample. A moving sample would make two
# runs of S7 disagree for no reason anyone could reconstruct.
SEED = 20260822


def run(target_per_cell: int | None = None, seed: int = SEED) -> dict[str, Any]:
    """Downsample filtered utterances to `target_per_cell` per (source, brand)."""
    cfg = load_sources()
    target = target_per_cell or cfg.get("classification_target_per_cell")
    if not target:
        raise RuntimeError(
            "no classification_target_per_cell in sources.yaml and no --target given. "
            "The budget has to be an explicit decision, not a default."
        )

    rows = list(read_filtered())
    by_cell: dict[tuple[str, str], list[dict[str, Any]]] = collections.defaultdict(list)
    for row in rows:
        by_cell[(row["source"], row["brand"])].append(row)

    rng = random.Random(seed)
    kept: list[dict[str, Any]] = []
    report_cells = []

    for cell in sorted(by_cell):
        bucket = by_cell[cell]
        # Sort by utterance_id first: dict/file order is not guaranteed stable
        # across runs, and a seeded sample over an unstable ordering is not
        # actually reproducible.
        bucket.sort(key=lambda r: r["utterance_id"])
        n_available = len(bucket)
        n_take = min(target, n_available)
        chosen = rng.sample(bucket, n_take) if n_take < n_available else list(bucket)
        kept.extend(chosen)
        report_cells.append({
            "source": cell[0], "brand": cell[1],
            "available": n_available, "sampled": n_take,
            "fraction": round(n_take / n_available, 4) if n_available else None,
            "censused": n_take == n_available,
        })

    out_path = INTERIM / "sampled.jsonl"
    with out_path.open("w", encoding="utf-8", newline="\n") as fh:
        for row in kept:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")

    report = {
        "at": now_ist(), "stage": "S4b", "seed": seed,
        "target_per_cell": target,
        "utterances_in": len(rows), "utterances_out": len(kept),
        "cells": report_cells,
        "note": (
            "Random sample WITHIN the collection window, never the newest n - "
            "taking the newest n would reintroduce the unequal windows B10 removes. "
            "A uniform sample leaves each area's share unchanged in expectation; "
            "the Wilson interval widens to reflect the smaller n."
        ),
    }
    LOGS.mkdir(parents=True, exist_ok=True)
    with (LOGS / "s4b_report.jsonl").open("a", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(report, ensure_ascii=False) + "\n")
    return report


def read_sampled() -> Iterator[dict[str, Any]]:
    """Sampled utterances, falling back to the full filtered set if S4b never ran.

    The fallback is deliberate: skipping the downsample is a legitimate choice
    on a small corpus, and it should not require a different command downstream.
    """
    path = INTERIM / "sampled.jsonl"
    if not path.exists():
        yield from read_filtered()
        return
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                yield json.loads(line)
