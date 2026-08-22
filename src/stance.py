"""Surface-based resolution of `temporal_stance`.

D7, settled 23 Aug 2026. On a surface where the speaker has demonstrably
transacted - an app-store review can only be written by someone who installed
and used the app - an utterance that does not state its own stance is read as
`post_purchase` rather than left `unclear`.

WHY THIS EXISTS. The S6 gate failed on `temporal_stance` at kappa 0.157, and
the confusion matrix showed the entire disagreement was one cell: 24 of 69 rows
where the human said `post_purchase` and the model said `unclear`. Agreement on
`pre_purchase` was 3/3. The human was reading the surface; the prompt tells the
model to read only the text and never to guess. Both defensible, mutually
incompatible, and no amount of prompt tuning resolves a definitional
disagreement - so it is settled as a stated convention instead.

WHAT IT DOES NOT DO. It never overrides an explicit label. `pre_purchase` and
`at_purchase` survive untouched, which matters because those 453 pre-purchase
utterances are the entire basis of the opportunity index and are exactly the
rows a blanket surface rule would have destroyed. Only `unclear` moves.

WHAT IT COSTS. It asserts something the text does not say for 41% of the corpus.
That is a real modelling assumption and it is why the raw label is kept beside
the resolved one: `temporal_stance` is what the classifier said,
`temporal_stance_resolved` is what the engine reports, and any figure can be
recomputed on either. A convention you can switch off is a convention you can
defend.
"""
from __future__ import annotations

from typing import Any

from src.config import load_sources

UNSTATED = "unclear"
DEFAULT_WHEN_SURFACE_IMPLIES = "post_purchase"


def surfaces_implying_post_purchase() -> set[str]:
    return {
        name for name, cfg in load_sources()["sources"].items()
        if cfg.get("surface_implies_post_purchase")
    }


def resolve_one(source: str, stance: Any, surfaces: set[str] | None = None) -> Any:
    """Resolved stance for a single row. Explicit labels pass through unchanged."""
    surfaces = surfaces if surfaces is not None else surfaces_implying_post_purchase()
    if stance == UNSTATED and source in surfaces:
        return DEFAULT_WHEN_SURFACE_IMPLIES
    return stance


def add_resolved_column(df: Any) -> Any:
    """Add `temporal_stance_resolved` alongside the raw `temporal_stance`.

    Both columns are kept deliberately. Overwriting the raw label would make the
    convention invisible and irreversible; keeping both means the appendix can
    state exactly how many rows the rule moved, and a sceptical reader can ask
    for the numbers without it.
    """
    surfaces = surfaces_implying_post_purchase()
    df = df.copy()
    df["temporal_stance_resolved"] = [
        resolve_one(src, st, surfaces)
        for src, st in zip(df["source"], df["temporal_stance"])
    ]
    df["stance_resolved_by_surface"] = (
        df["temporal_stance_resolved"] != df["temporal_stance"]
    )
    return df


def resolution_report(df: Any) -> dict[str, Any]:
    """How many rows the convention moved, per source. Appendix material."""
    resolved = add_resolved_column(df) if "temporal_stance_resolved" not in df.columns else df
    moved = resolved[resolved["stance_resolved_by_surface"]]
    return {
        "rows": int(len(resolved)),
        "moved_by_surface_rule": int(len(moved)),
        "moved_share": round(len(moved) / len(resolved), 4) if len(resolved) else 0.0,
        "by_source": {
            src: int((moved["source"] == src).sum())
            for src in sorted(resolved["source"].unique())
        },
        "raw_distribution": {
            k: int(v) for k, v in resolved["temporal_stance"].value_counts().items()
        },
        "resolved_distribution": {
            k: int(v) for k, v in resolved["temporal_stance_resolved"].value_counts().items()
        },
        "note": (
            "Only `unclear` moves, and only on surfaces where the speaker has "
            "demonstrably transacted. Explicit pre_purchase and at_purchase labels "
            "are never overridden - those are the rows the opportunity index rests on."
        ),
    }
