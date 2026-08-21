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


def observed_windows(labelled: Any | None = None) -> dict[tuple[str, str], float]:
    """Observed posted_at span in days, per (source, brand).

    Computed from the data rather than read from a log, so it stays true after a
    partial re-collection.
    """
    import pandas as pd

    if labelled is None:
        path = ROOT / "data" / "labelled" / "utterances.parquet"
        if not path.exists():
            return {}
        labelled = pd.read_parquet(path)

    if "posted_at" not in labelled.columns:
        return {}
    df = labelled.dropna(subset=["posted_at"]).copy()
    if df.empty:
        return {}
    df["_ts"] = pd.to_datetime(df["posted_at"], errors="coerce", utc=True)
    df = df.dropna(subset=["_ts"])
    spans = df.groupby(["source", "brand"])["_ts"].agg(["min", "max"])
    return {
        (src, brand): round((row["max"] - row["min"]).total_seconds() / 86400.0, 2)
        for (src, brand), row in spans.iterrows()
    }


def window_compliant_brands(
    source: str, windows: dict[tuple[str, str], float], tolerance_days: float
) -> tuple[set[str], dict[str, str]]:
    """Brands on `source` whose observed period matches the focal brand's.

    A brand whose window is materially shorter cannot enter a differential, and
    the reason is structural rather than fixable: Apple caps public review
    pagination at ~500 rows, so a high-velocity app like Myntra reaches back only
    ~3 days on the App Store while AJIO reaches ~88. Pooling them would compare
    three days of one brand against three months of another.

    Excluded brands keep their utterances - they remain available for verbatims
    and severity. They are only barred from the pooled proportion.
    """
    on_source = {b: d for (s, b), d in windows.items() if s == source}
    focal = on_source.get(FOCAL_BRAND)
    if focal is None:
        return set(on_source), {}
    compliant: set[str] = set()
    excluded: dict[str, str] = {}
    for brand, span in on_source.items():
        if abs(span - focal) <= tolerance_days:
            compliant.add(brand)
        else:
            excluded[brand] = (
                f"observed window {span:.1f}d vs focal {focal:.1f}d "
                f"(tolerance {tolerance_days}d) - excluded from the pool; "
                "retained for verbatims and severity"
            )
    return compliant, excluded


def run(aggregates: Any | None = None) -> Any:
    """AJIO vs pooled competitors, per (source, stance, opportunity_area)."""
    import pandas as pd

    if aggregates is None:
        path = OUT / "aggregates.parquet"
        if not path.exists():
            raise FileNotFoundError(f"{path} missing - run S7 first")
        aggregates = pd.read_parquet(path)

    alpha = float(threshold("statistics.alpha"))
    cfg = load_sources()
    tolerance = float(cfg.get("window_parity_tolerance_days", 3))
    windows = observed_windows()
    compliance: dict[str, tuple[set[str], dict[str, str]]] = {}
    for source in sorted({s for s, _ in windows}):
        compliance[source] = window_compliant_brands(source, windows, tolerance)
    for source, (_, excluded) in compliance.items():
        if excluded:
            _log({"at": now_ist(), "stage": "S8", "source": source,
                  "excluded_from_pool": excluded,
                  "reason": "window parity - see B10 in docs/decisions.md"})

    rows: list[dict[str, Any]] = []

    keys = ["source", "temporal_stance", "opportunity_area"]
    for (source, stance, area), block in aggregates.groupby(keys):
        group = _matched_group(source)

        focal = block[block["brand"] == FOCAL_BRAND]
        pool = block[block["brand"] != FOCAL_BRAND]     # AJIO excluded from its own pool

        # Window parity: a brand whose observed period does not match the focal
        # brand's cannot enter the pooled proportion (B10). Its rows stay in the
        # corpus for verbatims; they just do not form a denominator here.
        compliant, excluded_reasons = compliance.get(source, (None, {}))
        if compliant is not None:
            pool = pool[pool["brand"].isin(compliant - {FOCAL_BRAND})]

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
            "pool_excluded_for_window": sorted(excluded_reasons) if excluded_reasons else [],
            "focal_window_days": windows.get((source, FOCAL_BRAND)),
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
