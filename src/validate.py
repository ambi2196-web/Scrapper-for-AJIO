"""S6 - validation. The stage it will be tempting to skip. Build it now.

Two independent reliability numbers, which is a materially stronger claim than
one:

  1. Model-vs-model kappa (lane A vs lane C). Free, automatic, and reportable on
     its own. It is meaningful only because the lanes are different model
     families from different vendors - a model checking itself would agree with
     itself and measure nothing.
  2. Human-vs-lane-A kappa on a stratified hand-labelled sample, over-sampled in
     the disagreement stratum and reweighted to the population.

Kappa is reported PER FIELD. A blended kappa hides the case where
temporal_stance - the field the entire engine rests on - is the weak one, and
that is precisely the failure worth knowing about.

The gate: below Landis & Koch's substantial-agreement band, the numbers do not
go on a slide. If a field misses, fix the prompt, bump classifier_version, and
re-run the FULL corpus - not the sample. A corpus that is half one prompt and
half another has no single classifier_version and no defensible count.

If the schedule collapses, cut sources, not this stage. A validated engine on
three sources beats an unvalidated one on nine, and only the first survives
questioning.
"""
from __future__ import annotations

import json
import math
import random
from typing import Any

from src.config import ROOT, load_taxonomy, threshold
from src.envelope import now_ist

LABELLED = ROOT / "data" / "labelled"
GOLD = ROOT / "data" / "gold"
LOGS = ROOT / "logs"

FIELDS = ("opportunity_area", "temporal_stance", "severity")


def _load_labels() -> Any:
    import pandas as pd

    path = LABELLED / "utterances.parquet"
    if not path.exists():
        raise FileNotFoundError(f"{path} missing - run S5 and `consolidate` first")
    return pd.read_parquet(path)


def cohens_kappa(a: list[Any], b: list[Any], weights: list[float] | None = None) -> dict[str, Any]:
    """Weighted Cohen's kappa. Weights reweight an over-sampled stratum to the population.

    Without reweighting, a kappa computed over a disagreement-oversampled sample
    UNDERSTATES agreement - the wrong direction of error to publish, because it
    makes the engine look less reliable than it is and invites the reader to
    discount every number rather than only the weak ones.
    """
    pairs = [(x, y, (weights[i] if weights else 1.0)) for i, (x, y) in enumerate(zip(a, b))
             if x is not None and y is not None and not _isnan(x) and not _isnan(y)]
    if not pairs:
        return {"kappa": None, "n": 0, "note": "no comparable pairs"}

    total = sum(w for _, _, w in pairs)
    observed = sum(w for x, y, w in pairs if x == y) / total

    categories = sorted({str(x) for x, _, _ in pairs} | {str(y) for _, y, _ in pairs})
    expected = 0.0
    for cat in categories:
        pa = sum(w for x, _, w in pairs if str(x) == cat) / total
        pb = sum(w for _, y, w in pairs if str(y) == cat) / total
        expected += pa * pb

    if expected >= 1.0:
        return {"kappa": None, "n": len(pairs), "note": "expected agreement is 1.0; kappa undefined"}

    kappa = (observed - expected) / (1 - expected)
    # Standard error under the null, adequate for reporting a CI on kappa.
    n_eff = total
    se = math.sqrt(observed * (1 - observed) / (n_eff * (1 - expected) ** 2)) if n_eff else None
    return {
        "kappa": round(kappa, 4),
        "n": len(pairs),
        "observed_agreement": round(observed, 4),
        "expected_agreement": round(expected, 4),
        "se": round(se, 4) if se else None,
        "ci95": [round(kappa - 1.96 * se, 4), round(kappa + 1.96 * se, 4)] if se else None,
        "band": landis_koch_band(kappa),
    }


def _isnan(v: Any) -> bool:
    return isinstance(v, float) and math.isnan(v)


def landis_koch_band(kappa: float) -> str:
    """Landis & Koch (1977) Biometrics 33(1):159-174."""
    if kappa < 0.00:
        return "poor"
    if kappa < 0.21:
        return "slight"
    if kappa < 0.41:
        return "fair"
    if kappa < 0.61:
        return "moderate"
    if kappa < 0.81:
        return "substantial"
    return "almost perfect"


# --------------------------------------------------------------------------
# 1. Model vs model
# --------------------------------------------------------------------------

def model_vs_model() -> dict[str, Any]:
    df = _load_labels()
    if "laneC_opportunity_area" not in df.columns:
        raise RuntimeError("lane C labels absent - run `classify lane-c` before S6")
    overlap = df[df["laneC_opportunity_area"].notna()]

    result = {"at": now_ist(), "comparison": "lane_A_gemini vs lane_C_groq",
              "n_overlap": int(len(overlap)), "per_field": {}}
    for field in FIELDS:
        c_field = f"laneC_{field}"
        if c_field not in overlap.columns:
            continue
        result["per_field"][field] = cohens_kappa(
            overlap[field].tolist(), overlap[c_field].tolist()
        )
    result["note"] = (
        "Two vendors' models as independent annotators. A second pass by the same "
        "model would agree with itself, which is not evidence."
    )
    _log("s6_model_vs_model", result)
    return result


def disagreement_stratum() -> Any:
    """Utterances where lanes A and C disagree - where a human label is most informative."""
    df = _load_labels()
    overlap = df[df["laneC_opportunity_area"].notna()].copy()
    overlap["disagrees"] = (
        (overlap["opportunity_area"] != overlap["laneC_opportunity_area"])
        | (overlap["temporal_stance"] != overlap["laneC_temporal_stance"])
    )
    return overlap


# --------------------------------------------------------------------------
# 2. Sample size, derived
# --------------------------------------------------------------------------

def derive_sample_size(smallest_reportable_p: float, smallest_claimed_diff: float) -> dict[str, Any]:
    """n such that the Wilson half-width on the smallest reported area is narrower
    than the smallest differential to be claimed.

    Three lines of arithmetic, and they go in the appendix. The alternative -
    picking 100 because it is a round number - is exactly the failure mode I6
    exists to prevent.
    """
    z = 1.959963985
    p = smallest_reportable_p
    target = smallest_claimed_diff / 2.0
    # Wilson half-width shrinks ~ z*sqrt(p(1-p)/n); solve for n at the target width.
    n = math.ceil((z ** 2) * p * (1 - p) / (target ** 2))
    return {
        "n_required": int(n),
        "inputs": {"smallest_reportable_proportion": p, "smallest_claimed_differential": smallest_claimed_diff},
        "rule": "Wilson half-width on the smallest reported area < half the smallest claimed differential",
        "write_to": "config/thresholds.yaml validation.human_sample_size_n",
    }


def build_gold_sheet(n: int = 100, seed: int = 20260822) -> dict[str, Any]:
    """Blind hand-labelling sheet for the B-sweep, drawn BEFORE any classification.

    Distinct from `build_labelling_sheet`, which stratifies on model
    disagreement and therefore cannot exist until lanes A and C have run. The
    B-sweep needs gold labels *first* (04 §4.3: sweep B against a fixed
    100-utterance gold subset), so this samples from the S4b output directly.

    Stratified by (source, brand) so the sweep is not dominated by whichever
    cell happens to be largest, and seeded so the same 100 utterances are used
    on every re-run - a moving gold set would make two sweeps incomparable.

    Blind by construction: the sheet carries the text and nothing else. There is
    no model label to see yet, which is the one advantage of labelling at this
    point in the pipeline.
    """
    import pandas as pd

    from src.sample import read_sampled

    rows = list(read_sampled())
    if not rows:
        raise RuntimeError("no sampled utterances - run S4b first")

    strata: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in rows:
        strata.setdefault((row["source"], row["brand"]), []).append(row)

    rng = random.Random(seed)
    per = max(1, n // max(1, len(strata)))
    picked: list[dict[str, Any]] = []
    for key in sorted(strata):
        bucket = sorted(strata[key], key=lambda r: r["utterance_id"])
        picked.extend(rng.sample(bucket, min(per, len(bucket))))

    # Top up from the pool at large if rounding left the sheet short.
    if len(picked) < n:
        chosen = {r["utterance_id"] for r in picked}
        rest = sorted((r for r in rows if r["utterance_id"] not in chosen),
                      key=lambda r: r["utterance_id"])
        picked.extend(rng.sample(rest, min(n - len(picked), len(rest))))

    sheet = pd.DataFrame([{
        "utterance_id": r["utterance_id"],
        "source": r["source"], "brand": r["brand"],
        "language": r.get("language"), "url": r.get("url"),
        "utterance_text": r["utterance_text"],
        "opportunity_area": "", "temporal_stance": "", "severity": "",
        "hesitation_marker": "", "labeller_note": "",
    } for r in picked])

    GOLD.mkdir(parents=True, exist_ok=True)
    path = GOLD / "gold_sheet_TEMPLATE.csv"
    sheet.to_csv(path, index=False, encoding="utf-8-sig")

    tax = load_taxonomy()
    return {
        "path": str(path.relative_to(ROOT)),
        "n": len(sheet),
        "strata": {f"{s}/{b}": len(v) for (s, b), v in sorted(strata.items())},
        "seed": seed,
        "valid_opportunity_area": [a["code"] for a in tax["opportunity_areas"]] + ["none"],
        "valid_temporal_stance": [t["code"] for t in tax["temporal_stance"]],
        "valid_severity": [s["level"] for s in tax["severity"]],
        "next": (
            "Fill the blank columns, save as data/gold/human_labels.csv, then run "
            "`python -m src.cli classify sweep-b` to derive batch_size_B."
        ),
    }


def build_labelling_sheet(n: int, oversample_factor: float = 3.0, seed: int = 20260823) -> dict[str, Any]:
    """Write a BLIND labelling sheet: raw text and span, nothing else.

    If the labeller can see the model's guess they are not a second annotator,
    they are an approver, and approval rates are not kappa. The model's labels
    are deliberately absent from this file.
    """
    import pandas as pd

    overlap = disagreement_stratum()
    agree = overlap[~overlap["disagrees"]]
    disagree = overlap[overlap["disagrees"]]

    # Over-sample the disagreement stratum; sampling weights are recorded so the
    # kappa can be reweighted back to the population.
    n_disagree = min(len(disagree), int(n * oversample_factor / (1 + oversample_factor)))
    n_agree = min(len(agree), n - n_disagree)

    sample = pd.concat([
        disagree.sample(n=n_disagree, random_state=seed) if n_disagree else disagree.head(0),
        agree.sample(n=n_agree, random_state=seed) if n_agree else agree.head(0),
    ])

    p_disagree = len(disagree) / len(overlap) if len(overlap) else 0
    sample["stratum"] = sample["disagrees"].map({True: "disagreement", False: "agreement"})
    sample["sampling_weight"] = sample["disagrees"].map({
        True: (p_disagree / (n_disagree / len(sample))) if n_disagree else 0,
        False: ((1 - p_disagree) / (n_agree / len(sample))) if n_agree else 0,
    })

    GOLD.mkdir(parents=True, exist_ok=True)
    sheet = sample[["utterance_id", "source", "brand", "url", "span", "utterance_text",
                    "stratum", "sampling_weight"]].copy()
    for field in FIELDS:
        sheet[field] = ""          # the human fills these
    sheet["labeller_note"] = ""
    path = GOLD / "human_labels_TEMPLATE.csv"
    sheet.to_csv(path, index=False)

    return {"path": str(path.relative_to(ROOT)), "n": len(sheet),
            "n_disagreement_stratum": int(n_disagree), "n_agreement_stratum": int(n_agree),
            "population_disagreement_rate": round(p_disagree, 4),
            "note": "Fill the blank label columns, save as human_labels.csv, then run S6 human-kappa."}


# --------------------------------------------------------------------------
# 3. Human vs lane A, and the gate
# --------------------------------------------------------------------------

def human_vs_model() -> dict[str, Any]:
    import pandas as pd

    gold_path = GOLD / "human_labels.csv"
    if not gold_path.exists():
        raise FileNotFoundError(
            f"{gold_path} missing. Generate the blind sheet with "
            "`python -m src.cli labelling-sheet`, hand-label it, save as human_labels.csv."
        )
    gold = pd.read_csv(gold_path)
    df = _load_labels().set_index("utterance_id")

    joined = gold.join(df, on="utterance_id", rsuffix="_model")
    weights = joined["sampling_weight"].fillna(1.0).tolist() if "sampling_weight" in joined else None

    result = {"at": now_ist(), "comparison": "human vs lane_A", "n": int(len(joined)),
              "reweighted": weights is not None, "per_field": {}}
    for field in FIELDS:
        model_col = f"{field}_model" if f"{field}_model" in joined.columns else field
        result["per_field"][field] = cohens_kappa(
            joined[field].tolist(), joined[model_col].tolist(), weights
        )
    result.update(apply_gate(result["per_field"]))
    _log("s6_human_vs_model", result)
    return result


def apply_gate(per_field: dict[str, dict[str, Any]]) -> dict[str, Any]:
    floor = float(threshold("validation.kappa_acceptance_floor"))
    failing = [
        f for f, r in per_field.items()
        if r.get("kappa") is None or r["kappa"] < floor
    ]
    return {
        "kappa_floor": floor,
        "floor_source": "Landis & Koch (1977) - lower bound of the substantial-agreement band",
        "failing_fields": failing,
        "gate": "PASS" if not failing else "FAIL",
        "gate_action": (
            "Numbers may go on a slide."
            if not failing else
            f"Fields {failing} are below the floor. Fix the prompt, bump classifier_version, "
            "re-run the FULL corpus. Do not report these fields until they pass."
        ),
    }


def _log(name: str, payload: dict[str, Any]) -> None:
    LOGS.mkdir(parents=True, exist_ok=True)
    with (LOGS / f"{name}.jsonl").open("a", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")


def sweep_tau() -> dict[str, Any]:
    """Derive the escalation floor: accuracy by self-reported confidence bucket.

    Set tau at the bucket where accuracy first drops below the corpus mean. This
    is what makes tau a measurement rather than 0.7 chosen because it looks
    reasonable.
    """
    import pandas as pd

    gold_path = GOLD / "human_labels.csv"
    if not gold_path.exists():
        raise FileNotFoundError(f"{gold_path} missing - tau is derived against the gold set")
    gold = pd.read_csv(gold_path)
    df = _load_labels().set_index("utterance_id")
    joined = gold.join(df, on="utterance_id", rsuffix="_model")

    joined["correct"] = joined["opportunity_area"] == joined.get(
        "opportunity_area_model", joined["opportunity_area"]
    )
    joined["bucket"] = (joined["confidence"] * 10).round() / 10
    corpus_mean = float(joined["correct"].mean())

    table = (joined.groupby("bucket")["correct"]
             .agg(["mean", "count"]).reset_index()
             .rename(columns={"mean": "accuracy", "count": "n"})
             .sort_values("bucket"))

    below = table[table["accuracy"] < corpus_mean]
    tau = float(below["bucket"].max()) if not below.empty else None

    result = {"at": now_ist(), "corpus_mean_accuracy": round(corpus_mean, 4),
              "buckets": table.to_dict("records"), "tau_suggested": tau,
              "rule": "highest confidence bucket whose accuracy is still below the corpus mean",
              "write_to": "config/thresholds.yaml classification.tau_escalation"}
    _log("s6_tau_sweep", result)
    return result
