"""S7 - quantify. Wilson intervals, and the two gates that keep numbers honest.

Gate 1 (invariant I4, test T8): the detectability gate is applied HERE, before
aggregation - not filtered out in the deck. An area graded weak or none carries
oi = null and a gate_reason. The distinction matters: a number computed and then
hidden can be un-hidden by anyone who opens the CSV, and at 2 a.m. on submission
eve someone will. A number never computed cannot leak.

Gate 2 (invariant I5, test T7): denominators never mix sources and never mix
temporal_stance. Grouping is (source, brand, temporal_stance, opportunity_area)
and a call that omits either key raises. "12% of utterances mention sizing" is
not a fact unless you say 12% of which surface, and at which point in the
purchase - a pre-purchase sizing question and a post-purchase sizing complaint
are different phenomena that happen to share a word.

Gate 3 (test T9): complaint sites are denominator_eligible: false in
sources.yaml and are refused here. Their selection bias is structural rather
than sizeable.

Point estimates never leave this stage unaccompanied by a Wilson interval.
"""
from __future__ import annotations

import json
from typing import Any

from src.config import ROOT, denominator_eligible_sources, detectability_map, load_proximity, threshold
from src.envelope import now_ist

LABELLED = ROOT / "data" / "labelled"
OUT = ROOT / "data" / "out"
LOGS = ROOT / "logs"

REQUIRED_GROUP_KEYS = {"source", "temporal_stance"}
GROUP_KEYS = ["source", "brand", "temporal_stance", "opportunity_area"]


class DenominatorError(RuntimeError):
    """Raised when a query would mix sources or stances, or use an ineligible source."""


def wilson(successes: int, n: int, alpha: float) -> tuple[float | None, float | None, float | None]:
    """Wilson score interval. Preferred over Wald at small n and extreme p."""
    if n == 0:
        return None, None, None
    from statsmodels.stats.proportion import proportion_confint

    lo, hi = proportion_confint(successes, n, alpha=alpha, method="wilson")
    return successes / n, float(lo), float(hi)


def _load() -> Any:
    import pandas as pd

    path = LABELLED / "utterances.parquet"
    if not path.exists():
        raise FileNotFoundError(f"{path} missing - run S5 then `consolidate`")
    return pd.read_parquet(path)


def aggregate(group_keys: list[str] | None = None) -> Any:
    """Aggregate to proportions with Wilson intervals, both gates applied.

    `group_keys` is a parameter only so that the guard below can catch a caller
    who tries to drop one. It is not a knob.
    """
    import pandas as pd

    keys = group_keys or GROUP_KEYS
    missing = REQUIRED_GROUP_KEYS - set(keys)
    if missing:
        raise DenominatorError(
            f"grouping omits {sorted(missing)} (invariant I5).\n"
            "  A denominator that mixes sources compares an app-store audience with a\n"
            "  product-page audience; one that mixes temporal_stance compares people\n"
            "  who have not bought with people who have. Neither is a rate."
        )

    alpha = float(threshold("statistics.alpha"))
    eligible = denominator_eligible_sources()
    grades = detectability_map()
    proximity = load_proximity()["weights"]

    df = _load()

    ineligible_present = sorted(set(df["source"]) - eligible)
    if ineligible_present:
        # Not dropped silently: the row count is a real finding about severity
        # coverage, so it is logged before removal.
        _log("s7_excluded_sources", {
            "at": now_ist(), "excluded": ineligible_present,
            "rows_excluded": int(df[~df["source"].isin(eligible)].shape[0]),
            "reason": "denominator_eligible: false in sources.yaml - severity and verbatim sources only",
        })
        df = df[df["source"].isin(eligible)]

    # Denominator: all utterances in the (source, brand, stance) cell.
    denom = (df.groupby(["source", "brand", "temporal_stance"])
               .size().rename("n_cell").reset_index())
    numer = df.groupby(keys).size().rename("n_area").reset_index()
    merged = numer.merge(denom, on=["source", "brand", "temporal_stance"], how="left")

    stats = merged.apply(
        lambda r: wilson(int(r["n_area"]), int(r["n_cell"]), alpha), axis=1, result_type="expand"
    )
    merged[["proportion", "ci_low", "ci_high"]] = stats

    merged["detectability"] = merged["opportunity_area"].map(grades)
    merged["proximity_weight"] = merged["opportunity_area"].map(
        lambda a: (proximity.get(a) or {}).get("value")
    )

    # Severity mean per cell, used by the index.
    sev = (df.groupby(keys)["severity"].mean().rename("severity_mean").reset_index())
    merged = merged.merge(sev, on=keys, how="left")

    merged["oi"] = merged.apply(_opportunity_index, axis=1)
    merged["gate_reason"] = merged.apply(_gate_reason, axis=1)

    min_cell = threshold("statistics.min_cell_n_for_reporting", required=False)
    if min_cell:
        merged["below_min_cell"] = merged["n_cell"] < int(min_cell)
        merged.loc[merged["below_min_cell"], ["proportion", "ci_low", "ci_high"]] = None
        merged.loc[merged["below_min_cell"], "gate_reason"] = (
            f"denominator below min_cell_n_for_reporting={min_cell}; count reported, rate suppressed"
        )

    OUT.mkdir(parents=True, exist_ok=True)
    merged.to_parquet(OUT / "aggregates.parquet", index=False)
    return merged


def _opportunity_index(row: Any) -> float | None:
    """oi = proportion x severity x wishlist_proximity. Null when any input is gated.

    Returning null rather than a computed-then-hidden number is invariant I4:
    the gate is a property of the data, not of the rendering.
    """
    if row.get("detectability") in ("weak", "none"):
        return None
    if row.get("proportion") is None or row.get("proximity_weight") is None:
        return None
    if row.get("severity_mean") is None or row["severity_mean"] != row["severity_mean"]:
        return None
    return round(float(row["proportion"]) * float(row["severity_mean"]) * float(row["proximity_weight"]), 6)


def _gate_reason(row: Any) -> str | None:
    grade = row.get("detectability")
    if grade in ("weak", "none"):
        return "not adjudicable from public text"
    if grade is None:
        return "opportunity_area not present in the frozen taxonomy detectability table"
    if row.get("proximity_weight") is None:
        return "no wishlist_proximity weight for this area (config/proximity.yaml)"
    return None


def _log(name: str, payload: dict[str, Any]) -> None:
    LOGS.mkdir(parents=True, exist_ok=True)
    with (LOGS / f"{name}.jsonl").open("a", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")


def derive_min_cell(smallest_claimed_diff: float, expected_p: float = 0.1) -> dict[str, Any]:
    """Smallest denominator whose Wilson interval is narrower than the claim."""
    import math

    z = 1.959963985
    target = smallest_claimed_diff / 2.0
    n = math.ceil((z ** 2) * expected_p * (1 - expected_p) / (target ** 2))
    return {"min_cell_n": int(n), "inputs": {"smallest_claimed_differential": smallest_claimed_diff,
                                             "expected_proportion": expected_p},
            "write_to": "config/thresholds.yaml statistics.min_cell_n_for_reporting"}
