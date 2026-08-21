"""S8 - compare. AJIO against the pooled competitor proportion.

Two rules that are enforced in code rather than remembered:

1. AJIO is EXCLUDED from the pool it is compared against. Including it dilutes
   the difference toward zero by exactly the amount AJIO contributes, which
   makes a real gap look smaller and an absent gap look like nothing - so the
   error is invisible in both directions.

2. Matched sources only. Play-vs-Play, PDP-QA-vs-PDP-QA. There is no defensible
   Play-vs-PDP comparison: an app-store review is written by someone who already
   transacted and is now rating an app, and a PDP question is written by someone
   deciding whether to. A difference between them measures the surfaces, not the
   brands. `structurally_matched_group` in sources.yaml declares which surfaces
   are comparable and this module refuses anything else.

The emitted record keeps ratio, p, n_ajio and n_pool together, deliberately. A
ratio without its denominators is not a claim - "AJIO is 2.3x worse" reads very
differently once you can see it rests on 11 utterances against 9.
"""
from __future__ import annotations

import json
from typing import Any

from src.config import ROOT, load_sources, threshold
from src.envelope import now_ist

OUT = ROOT / "data" / "out"
LOGS = ROOT / "logs"

FOCAL_BRAND = "ajio"


class ComparisonError(RuntimeError):
    """Raised on an unmatched-source or malformed comparison."""


def _matched_group(source: str) -> str:
    cfg = load_sources()["sources"].get(source) or {}
    group = cfg.get("structurally_matched_group")
    if not group:
        raise ComparisonError(f"source {source!r} declares no structurally_matched_group")
    return group


def two_proportion(
    successes_a: int, n_a: int, successes_b: int, n_b: int
) -> dict[str, Any]:
    from statsmodels.stats.proportion import proportions_ztest

    if n_a == 0 or n_b == 0:
        return {"z": None, "p": None, "note": "empty denominator; no test performed"}
    z, p = proportions_ztest([successes_a, successes_b], [n_a, n_b])
    return {"z": round(float(z), 4), "p": round(float(p), 6)}


def run(aggregates: Any | None = None) -> Any:
    """AJIO vs pooled competitors, per (source, stance, opportunity_area)."""
    import pandas as pd

    if aggregates is None:
        path = OUT / "aggregates.parquet"
        if not path.exists():
            raise FileNotFoundError(f"{path} missing - run S7 first")
        aggregates = pd.read_parquet(path)

    alpha = float(threshold("statistics.alpha"))
    rows: list[dict[str, Any]] = []

    keys = ["source", "temporal_stance", "opportunity_area"]
    for (source, stance, area), block in aggregates.groupby(keys):
        group = _matched_group(source)

        focal = block[block["brand"] == FOCAL_BRAND]
        pool = block[block["brand"] != FOCAL_BRAND]     # AJIO excluded from its own pool
        if focal.empty or pool.empty:
            continue

        n_ajio = int(focal["n_cell"].iloc[0])
        x_ajio = int(focal["n_area"].iloc[0])
        n_pool = int(pool["n_cell"].sum())
        x_pool = int(pool["n_area"].sum())

        p_ajio = x_ajio / n_ajio if n_ajio else None
        p_pool = x_pool / n_pool if n_pool else None
        gated = focal["gate_reason"].iloc[0]

        test = two_proportion(x_ajio, n_ajio, x_pool, n_pool)
        rows.append({
            "source": source,
            "structurally_matched_group": group,
            "temporal_stance": stance,
            "opportunity_area": area,
            "detectability": focal["detectability"].iloc[0],
            "gate_reason": gated,
            # Ratio is null when the area is gated: a gated area gets no number
            # anywhere, and a ratio is a number.
            "n_ajio": n_ajio, "x_ajio": x_ajio, "p_ajio": p_ajio,
            "n_pool": n_pool, "x_pool": x_pool, "p_pool": p_pool,
            "pool_brands": sorted(pool["brand"].unique().tolist()),
            "ratio": (round(p_ajio / p_pool, 4) if (gated is None and p_ajio and p_pool) else None),
            "difference": (round(p_ajio - p_pool, 4) if (gated is None and p_ajio is not None and p_pool is not None) else None),
            "z": test.get("z"),
            "p": test.get("p"),
            "significant_at_alpha": (test["p"] < alpha) if (gated is None and test.get("p") is not None) else None,
            "alpha": alpha,
        })

    result = pd.DataFrame(rows)
    OUT.mkdir(parents=True, exist_ok=True)
    result.to_parquet(OUT / "comparisons.parquet", index=False)
    _log({"at": now_ist(), "stage": "S8", "comparisons": len(result),
          "focal_brand": FOCAL_BRAND, "alpha": alpha})
    return result


def assert_matched(source_a: str, source_b: str) -> None:
    """Raise if two sources are not structurally comparable. Called by any cross-source query."""
    ga, gb = _matched_group(source_a), _matched_group(source_b)
    if ga != gb:
        raise ComparisonError(
            f"refusing to compare {source_a!r} ({ga}) with {source_b!r} ({gb}).\n"
            "  These surfaces are populated by different people at different points in\n"
            "  the purchase, so a difference between them measures the surface rather\n"
            "  than the brand. Compare like with like or not at all."
        )


def _log(payload: dict[str, Any]) -> None:
    LOGS.mkdir(parents=True, exist_ok=True)
    with (LOGS / "s8_report.jsonl").open("a", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")
