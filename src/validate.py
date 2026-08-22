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
import pathlib
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


def gwet_ac1(a: list[Any], b: list[Any], weights: list[float] | None = None) -> dict[str, Any]:
    """Gwet's AC1 - a chance-corrected agreement statistic robust to skewed marginals.

    Cohen's kappa assumes chance agreement can be estimated from the observed
    marginals. When one category dominates, that estimate inflates and kappa
    collapses even though the raters agree almost every time - the documented
    "high agreement but low kappa" paradox (Feinstein & Cicchetti 1990).

    This engine hits it squarely. After D7 resolves `unclear` into
    `post_purchase`, 93% of rows carry one stance: the two models then agree on
    90.5% of rows while kappa reads 0.218, because expected agreement is 87.8%.
    Reporting kappa alone there would describe the class balance, not the
    classifier.

    AC1 estimates chance agreement from how evenly the categories are spread
    rather than from how often each was used, so it does not degrade as the
    distribution skews.

    Gwet, K.L. (2008), "Computing inter-rater reliability and its variance in
    the presence of high agreement", Br. J. Math. Stat. Psychol. 61(1), 29-48.
    """
    pairs = [(x, y, (weights[i] if weights else 1.0)) for i, (x, y) in enumerate(zip(a, b))
             if x is not None and y is not None and not _isnan(x) and not _isnan(y)]
    if not pairs:
        return {"ac1": None, "n": 0}

    total = sum(w for _, _, w in pairs)
    observed = sum(w for x, y, w in pairs if x == y) / total

    categories = sorted({str(x) for x, _, _ in pairs} | {str(y) for _, y, _ in pairs})
    q = len(categories)
    if q < 2:
        return {"ac1": None, "n": len(pairs), "note": "single category; AC1 undefined"}

    chance = 0.0
    for cat in categories:
        pa = sum(w for x, _, w in pairs if str(x) == cat) / total
        pb = sum(w for _, y, w in pairs if str(y) == cat) / total
        pi = (pa + pb) / 2.0
        chance += pi * (1.0 - pi)
    chance /= (q - 1)

    if chance >= 1.0:
        return {"ac1": None, "n": len(pairs)}
    ac1 = (observed - chance) / (1.0 - chance)
    return {
        "ac1": round(ac1, 4),
        "n": len(pairs),
        "observed_agreement": round(observed, 4),
        "chance_agreement": round(chance, 4),
        "band": landis_koch_band(ac1),
    }


def agreement(a: list[Any], b: list[Any], weights: list[float] | None = None) -> dict[str, Any]:
    """Cohen's kappa and Gwet's AC1 together, flagging the paradox when present.

    Both are reported always, so nobody has to take it on trust that the
    alternative statistic was chosen after seeing which one looked better.
    """
    k = cohens_kappa(a, b, weights)
    g = gwet_ac1(a, b, weights)
    out = dict(k)
    out["ac1"] = g.get("ac1")
    out["ac1_band"] = g.get("band")
    out["chance_agreement_gwet"] = g.get("chance_agreement")
    # The paradox signature: raters agree often, kappa says otherwise, because
    # the marginals are lopsided enough to inflate expected agreement.
    obs, kap = k.get("observed_agreement"), k.get("kappa")
    out["kappa_paradox"] = bool(
        obs is not None and kap is not None and obs >= 0.80 and kap < 0.61
    )
    if out["kappa_paradox"]:
        out["paradox_note"] = (
            f"observed agreement {obs:.1%} but kappa {kap:.3f}: one category dominates, "
            "so expected agreement is inflated. Read AC1 for this field."
        )
    return out


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
              "n_overlap": int(len(overlap)), "per_field": {}, "per_field_raw": {}}
    for field in FIELDS:
        c_field = f"laneC_{field}"
        if c_field not in overlap.columns:
            continue
        result["per_field_raw"][field] = agreement(
            overlap[field].tolist(), overlap[c_field].tolist()
        )

    # Both annotators are resolved under D7 before the reported comparison.
    # Measuring a resolved reading against an unresolved one would score the
    # convention rather than the annotators. The raw pair is kept alongside: it
    # is the purer measure of agreement on the classification task itself, while
    # the resolved pair measures the field the engine actually reports.
    from src.stance import resolve_one, surfaces_implying_post_purchase

    surfaces = surfaces_implying_post_purchase()
    a_resolved = [resolve_one(s, v, surfaces)
                  for s, v in zip(overlap["source"], overlap["temporal_stance"])]
    c_resolved = [resolve_one(s, v, surfaces)
                  for s, v in zip(overlap["source"], overlap["laneC_temporal_stance"])]
    for field in FIELDS:
        if field == "temporal_stance":
            result["per_field"][field] = agreement(a_resolved, c_resolved)
        elif field in result["per_field_raw"]:
            result["per_field"][field] = result["per_field_raw"][field]
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
    # A codebook next to the sheet. Without it the labeller has to cross-
    # reference taxonomy.yaml, and a gold set labelled to a different scheme
    # fails silently: the B-sweep scores against it, every comparison misses,
    # and a batch size gets picked from noise.
    _write_codebook(tax)
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


def _write_codebook(tax: dict[str, Any]) -> None:
    lines = [
        "# Codebook for data/gold/gold_sheet_TEMPLATE.csv",
        "",
        "Fill four columns. Use these EXACT values - the taxonomy is closed, and a",
        "label outside it cannot be scored against classifier output.",
        "",
        "Validate before use:  python -m src.cli validate check-gold",
        "",
        "Worked examples on real rows: docs/labelling_guide.md",
        "",
        "## opportunity_area  (use the CODE, e.g. `OA-04`)",
        "",
        "| Code | Name | Definition |",
        "|---|---|---|",
    ]
    for a in tax["opportunity_areas"]:
        gate = "  **[gated - still label it if it fits]**" if a["detectability"] in ("weak", "none") else ""
        lines.append(f"| `{a['code']}` | {a['name']} | {a['definition']}{gate} |")
    lines += [
        "| `none` | No opportunity area | The utterance carries no friction from the list above. |",
        "",
        "`none` is a correct answer. Do not stretch an utterance to fit a code.",
        "",
        "## temporal_stance",
        "",
        "| Value | Meaning |",
        "|---|---|",
    ]
    for t in tax["temporal_stance"]:
        lines.append(f"| `{t['code']}` | {t['definition']} |")
    lines += [
        "",
        "This is the field the whole engine rests on. `unclear` is a correct answer -",
        "do not guess.",
        "",
        "## severity  (1, 2 or 3 - a number, not high/medium/low)",
        "",
        "| Level | Meaning |",
        "|---|---|",
    ]
    for sv in tax["severity"]:
        lines.append(f"| `{sv['level']}` | {sv['definition']} |")
    lines += [
        "",
        "Leave blank when opportunity_area is `none`.",
        "",
        "## hesitation_marker  (`true` or `false`)",
        "",
        "`true` only where the speaker is visibly weighing, deferring or abandoning a",
        "purchase in the text itself - not merely dissatisfied. If every row is",
        "`false`, kappa on this field is undefined, so read the text for it rather",
        "than defaulting.",
        "",
        "## When done",
        "",
        "Save as `data/gold/human_labels.csv`, then:",
        "",
        "```bash",
        "python -m src.cli validate check-gold",
        "```",
    ]
    (GOLD / "CODEBOOK.md").write_text(
        chr(10).join(lines), encoding="utf-8", newline=chr(10)
    )


def check_gold(path: Any = None) -> dict[str, Any]:
    """Validate a hand-labelled sheet against the FROZEN taxonomy before use.

    Exists because a gold set labelled to a different scheme fails silently in
    the worst possible way: the B-sweep scores classifier output against it, so
    every comparison misses, accuracy reads ~0 at every B, and the sweep picks a
    batch size from noise. Kappa then fails its gate for a reason that has
    nothing to do with the classifier.

    A gold set is the measuring stick. It has to be checked before it is used to
    measure anything.
    """
    import pandas as pd

    path = pathlib.Path(path) if path else (GOLD / "human_labels.csv")
    if not path.exists():
        raise FileNotFoundError(f"{path} not found")
    df = pd.read_csv(path)

    tax = load_taxonomy()
    valid_area = {a["code"] for a in tax["opportunity_areas"]} | {"none"}
    valid_stance = {t["code"] for t in tax["temporal_stance"]}
    # Excel writes an integer column containing blanks as floats, so a hand
    # 2 arrives as "2.0". Normalising is not a fudge - the value is unambiguous,
    # and rejecting it would send the labeller hunting a problem that is a
    # spreadsheet artefact rather than a labelling error.
    def _norm_sev(v: Any) -> str:
        t = str(v).strip()
        if t in ("", "nan", "None"):
            return ""
        try:
            return str(int(float(t)))
        except ValueError:
            return t

    valid_sev = {str(s["level"]) for s in tax["severity"]} | {""}
    valid_bool = {"true", "false", "1", "0", "yes", "no", ""}

    problems: list[str] = []
    if "utterance_id" not in df.columns:
        problems.append("missing required column utterance_id")

    def offenders(col: str, allowed: set[str]) -> list[str]:
        if col not in df.columns:
            problems.append(f"missing required column {col}")
            return []
        seen = {str(v).strip() for v in df[col].dropna()}
        return sorted(seen - allowed)

    bad_area = offenders("opportunity_area", valid_area)
    bad_stance = offenders("temporal_stance", valid_stance)
    bad_sev = (
        sorted({_norm_sev(v) for v in df["severity"].dropna()} - valid_sev)
        if "severity" in df.columns else []
    )
    bad_hes = {str(v).strip().lower() for v in df.get("hesitation_marker", pd.Series(dtype=str)).dropna()} - valid_bool

    if bad_area:
        problems.append(
            f"opportunity_area has {len(bad_area)} value(s) outside the frozen taxonomy: "
            f"{bad_area[:12]}. The taxonomy is CLOSED - a label outside it cannot be "
            "scored against classifier output."
        )
    if bad_stance:
        problems.append(
            f"temporal_stance has values outside the taxonomy: {bad_stance[:8]}. "
            "Valid: pre_purchase, at_purchase, post_purchase, unclear. This is the "
            "field the whole engine rests on."
        )
    if bad_sev:
        problems.append(f"severity must be 1, 2 or 3 - got {bad_sev[:8]}")
    if bad_hes:
        problems.append(f"hesitation_marker must be boolean-ish - got {sorted(bad_hes)[:8]}")

    # Severity must be present exactly when an area is claimed. A severity on a
    # `none` row grades a friction that was not identified; a missing one on a
    # real area silently drops that row from the severity kappa.
    if {"opportunity_area", "severity"} <= set(df.columns):
        sev_norm = df["severity"].map(_norm_sev)
        area = df["opportunity_area"].astype(str).str.strip()
        missing_sev = int(((area != "none") & (sev_norm == "")).sum())
        stray_sev = int(((area == "none") & (sev_norm != "")).sum())
        if missing_sev:
            problems.append(
                f"{missing_sev} row(s) claim an opportunity_area but leave severity blank - "
                "those rows drop out of the severity kappa"
            )
        if stray_sev:
            problems.append(
                f"{stray_sev} row(s) are opportunity_area=none but carry a severity - "
                "severity grades a friction, and none means none was identified"
            )

    # Zero variance in a field makes it useless for kappa: a constant column
    # agrees with everything by chance, so the statistic is undefined.
    for col in ("opportunity_area", "temporal_stance", "severity", "hesitation_marker"):
        if col in df.columns and df[col].dropna().nunique() <= 1:
            problems.append(
                f"{col} has a single value across every row. Kappa is undefined on a "
                "constant column - expected agreement is 1.0."
            )

    return {
        "path": str(path),
        "rows": int(len(df)),
        "valid": not problems,
        "problems": problems,
        "expected": {
            "opportunity_area": sorted(valid_area),
            "temporal_stance": sorted(valid_stance),
            "severity": [1, 2, 3],
            "hesitation_marker": ["true", "false"],
        },
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
    # Compare against the RESOLVED stance (D7). The human labelled by surface
    # convention, so comparing against the raw `unclear` would measure the
    # convention rather than the classifier.
    if "temporal_stance_resolved" in joined.columns:
        joined["temporal_stance_model"] = joined["temporal_stance_resolved"]
    weights = joined["sampling_weight"].fillna(1.0).tolist() if "sampling_weight" in joined else None

    result = {"at": now_ist(), "comparison": "human vs lane_A", "n": int(len(joined)),
              "reweighted": weights is not None, "per_field": {}}
    for field in FIELDS:
        model_col = f"{field}_model" if f"{field}_model" in joined.columns else field
        result["per_field"][field] = agreement(
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
    # Compare against the RESOLVED stance (D7). The human labelled by surface
    # convention, so comparing against the raw `unclear` would measure the
    # convention rather than the classifier.
    if "temporal_stance_resolved" in joined.columns:
        joined["temporal_stance_model"] = joined["temporal_stance_resolved"]

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
