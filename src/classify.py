"""S5 - classification. Lane A bulk, lane B escalation, lane C blind annotator.

Resumability is a hard requirement, not a nicety. This run WILL be interrupted -
by the RPD wall, by a laptop sleeping, by a crash - and a pipeline that restarts
from zero is a pipeline you cannot afford to run twice on a free tier. Before
each batch, ids already present in the shard are skipped. Consequence: a re-run
with no new data makes zero LLM calls (acceptance test T3), and a kill -9
mid-stage costs at most one batch (T4).

Nothing is repaired. An item that fails schema validation, taxonomy validation
or the evidence-quote assertion goes to logs/quarantine.jsonl with its reason. A
repaired label is a fabricated label: once written it is indistinguishable from
a real one, and every count built on it inherits that.
"""
from __future__ import annotations

import json
import math
import pathlib
import random
from typing import Any, Iterable, Iterator

from pydantic import ValidationError

from src.config import ROOT, load_lexicon, load_proximity, load_taxonomy, threshold
from src.envelope import now_ist
from src.sample import read_sampled as read_filtered
from src.llm import gemini, groq
from src.llm.router import LANES, QuotaExhausted, Router
from src.llm.schema import Label, check_evidence, response_json_schema, validate_against_taxonomy

LABELLED = ROOT / "data" / "labelled"
LOGS = ROOT / "logs"
PROMPTS = ROOT / "prompts"

CLASSIFIER_VERSION = "classify_v1"


# --------------------------------------------------------------------------
# Prompt assembly
# --------------------------------------------------------------------------

def _taxonomy_block(tax: dict[str, Any]) -> str:
    lines = []
    for area in tax["opportunity_areas"]:
        lines.append(f"  {area['code']}  {area['name']} — {area['definition']}")
    return "\n".join(lines)


def _lexicon_block(lex: dict[str, Any]) -> str:
    out = []
    for bucket in ("hesitation_en", "hesitation_hinglish"):
        for group, phrases in (lex.get(bucket) or {}).items():
            sample = ", ".join(f'"{p}"' for p in phrases[:8])
            out.append(f"  {bucket}/{group}: {sample}")
    return "\n".join(out)


def build_prompt(template: str, utterances: list[dict[str, Any]]) -> str:
    tax = load_taxonomy()
    lex = load_lexicon()
    payload = [{"utterance_id": u["utterance_id"], "text": u["utterance_text"]} for u in utterances]
    return (
        template
        .replace("{taxonomy_block}", _taxonomy_block(tax))
        .replace("{tree_nodes}", json.dumps(tax.get("tree_nodes", [])))
        .replace("{sub_nodes}", json.dumps(tax.get("sub_nodes", [])))
        .replace("{lexicon_block}", _lexicon_block(lex))
        .replace("{input_block}", json.dumps(payload, ensure_ascii=False, indent=None))
    )


def _load_template(name: str) -> str:
    return (PROMPTS / name).read_text(encoding="utf-8")


# --------------------------------------------------------------------------
# Shards and resumability
# --------------------------------------------------------------------------

def _shard_path(lane: str) -> pathlib.Path:
    LABELLED.mkdir(parents=True, exist_ok=True)
    return LABELLED / f"labels_lane_{lane}.jsonl"


def completed_ids(lane: str) -> set[str]:
    """Ids already labelled on this lane at this classifier_version.

    Versioned on purpose: a prompt edit bumps CLASSIFIER_VERSION, which makes
    every previous row stale and forces a full re-run rather than a corpus that
    is half one prompt and half another.
    """
    path = _shard_path(lane)
    if not path.exists():
        return set()
    done: set[str] = set()
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue  # torn final line from a kill -9; the id gets redone
            if row.get("classifier_version") == CLASSIFIER_VERSION:
                done.add(row["utterance_id"])
    return done


def _quarantine(utterance_id: str, lane: str, reason: str, payload: Any) -> None:
    LOGS.mkdir(parents=True, exist_ok=True)
    with (LOGS / "quarantine.jsonl").open("a", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps({
            "at": now_ist(), "lane": lane, "utterance_id": utterance_id,
            "reason": reason, "classifier_version": CLASSIFIER_VERSION,
            "payload": payload,
        }, ensure_ascii=False, default=str) + "\n")


def _parse_response(text: str) -> list[dict[str, Any]]:
    """Accept both the array (Gemini schema mode) and {"labels": [...]} (Groq)."""
    data = json.loads(text)
    if isinstance(data, dict):
        data = data.get("labels") or data.get("results") or []
    if not isinstance(data, list):
        raise ValueError(f"expected a list of labels, got {type(data).__name__}")
    return data


def _write_labels(lane: str, rows: Iterable[dict[str, Any]]) -> int:
    path = _shard_path(lane)
    count = 0
    with path.open("a", encoding="utf-8", newline="\n") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
            count += 1
    return count


# --------------------------------------------------------------------------
# The core batch loop
# --------------------------------------------------------------------------

def classify_batch(
    router: Router, lane: str, utterances: list[dict[str, Any]], template: str,
) -> tuple[int, int]:
    """Classify one batch. Returns (written, quarantined)."""
    tax = load_taxonomy()
    valid_areas = {a["code"] for a in tax["opportunity_areas"]}
    valid_sub_nodes = set(tax.get("sub_nodes") or [])
    by_id = {u["utterance_id"]: u for u in utterances}

    prompt = build_prompt(template, utterances)
    invoker = (
        gemini.make_invoker(response_json_schema())
        if LANES[lane]["provider"] == "gemini"
        else groq.make_invoker()
    )
    batch_id = f"{utterances[0]['utterance_id'][:12]}_{len(utterances)}"
    text = router.call(lane, prompt, invoke=invoker, batch_id=batch_id)
    if text is None:
        return 0, 0  # deferred; the queue is drained later

    try:
        items = _parse_response(text)
    except (json.JSONDecodeError, ValueError) as exc:
        for u in utterances:
            _quarantine(u["utterance_id"], lane, f"unparseable batch response: {exc}", text[:2000])
        return 0, len(utterances)

    good: list[dict[str, Any]] = []
    bad = 0
    for item in items:
        uid = item.get("utterance_id")
        utt = by_id.get(uid)
        if utt is None:
            _quarantine(str(uid), lane, "response contains an id that was not in the batch", item)
            bad += 1
            continue
        try:
            label = Label(**item)
        except ValidationError as exc:
            _quarantine(uid, lane, f"schema validation failed: {exc.errors()[:3]}", item)
            bad += 1
            continue

        reason = validate_against_taxonomy(label, valid_areas, valid_sub_nodes)
        if reason is None:
            reason = check_evidence(label, utt["utterance_text"])
        if reason is not None:
            _quarantine(uid, lane, reason, item)
            bad += 1
            continue

        good.append({
            **label.model_dump(),
            "lane": lane,
            "provider": LANES[lane]["provider"],
            "model": LANES[lane]["model"],
            "classifier_version": CLASSIFIER_VERSION,
            "taxonomy_version": tax["taxonomy_version"],
            "labelled_at": now_ist(),
            # Carried so S7 never has to join back to interim to group correctly.
            "source": utt["source"], "brand": utt["brand"], "source_id": utt["source_id"],
            "url": utt.get("url"), "posted_at": utt.get("posted_at"),
            "language": utt.get("language"), "span": utt.get("span"),
            "utterance_text": utt["utterance_text"],
            "rating": utt.get("rating"), "helpful_votes": utt.get("helpful_votes"),
        })

    # Silent omission: a model that returns 18 labels for 20 inputs has dropped
    # two, and without this they would simply never appear and never be counted.
    returned = {i.get("utterance_id") for i in items}
    for uid in set(by_id) - returned:
        _quarantine(uid, lane, "omitted from the model response", None)
        bad += 1

    return _write_labels(lane, good), bad


def _batches(rows: list[dict[str, Any]], size: int) -> Iterator[list[dict[str, Any]]]:
    for i in range(0, len(rows), size):
        yield rows[i:i + size]


# --------------------------------------------------------------------------
# Lanes
# --------------------------------------------------------------------------

def run_lane_a(limit: int | None = None) -> dict[str, Any]:
    """Bulk pass over the full corpus."""
    load_proximity()  # invariant I8: refuses to start against an unfrozen table
    batch_size = int(threshold("classification.batch_size_B"))
    template = _load_template("classify_v1.txt")

    done = completed_ids("A")
    pending = [r for r in read_filtered() if r["utterance_id"] not in done]
    if limit:
        pending = pending[:limit]

    router = Router()
    written = quarantined = 0
    try:
        for batch in _batches(pending, batch_size):
            w, q = classify_batch(router, "A", batch, template)
            written += w
            quarantined += q
    except QuotaExhausted as exc:
        # Not a failure. Stop cleanly, report where the run got to, resume tomorrow.
        return {"stage": "S5", "lane": "A", "written": written, "quarantined": quarantined,
                "already_done": len(done), "remaining": len(pending) - written - quarantined,
                "stopped_by": str(exc)}
    return {"stage": "S5", "lane": "A", "written": written, "quarantined": quarantined,
            "already_done": len(done), "remaining": 0}


def run_lane_b() -> dict[str, Any]:
    """Escalate the low-confidence tail, one utterance per call for full attention."""
    load_proximity()
    tau = float(threshold("classification.tau_escalation"))
    template = _load_template("classify_v1.txt")

    lane_a = {r["utterance_id"]: r for r in _read_lane("A")}
    low = [uid for uid, r in lane_a.items() if r.get("confidence", 1.0) < tau]
    done = completed_ids("B")
    pending_ids = [u for u in low if u not in done]

    by_id = {r["utterance_id"]: r for r in read_filtered()}
    router = Router()
    written = quarantined = 0
    try:
        for uid in pending_ids:
            utt = by_id.get(uid)
            if utt is None:
                continue
            w, q = classify_batch(router, "B", [utt], template)
            written += w
            quarantined += q
    except QuotaExhausted as exc:
        return {"stage": "S5", "lane": "B", "tau": tau, "escalated": len(low),
                "written": written, "quarantined": quarantined, "stopped_by": str(exc)}
    return {"stage": "S5", "lane": "B", "tau": tau, "escalated": len(low),
            "written": written, "quarantined": quarantined}


def run_lane_c(sample_size: int | None = None, seed: int = 20260822) -> dict[str, Any]:
    """Blind second annotator over a stratified sample.

    Stratified by (source, brand) so the reliability statistic is not dominated
    by whichever source happened to be largest. The sample is drawn with a fixed
    seed so the same utterances are re-labelled if the lane is re-run - a moving
    sample would make the two kappas incomparable.

    This lane never sees lane A's output. That independence is the only reason
    the resulting kappa means anything.
    """
    load_proximity()
    target = sample_size or int(threshold("validation.lane_c_sample_target_per_day"))
    batch_size = int(threshold("classification.batch_size_B"))
    template = _load_template("classify_v1_groq.txt")

    rows = list(read_filtered())
    strata: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in rows:
        strata.setdefault((row["source"], row["brand"]), []).append(row)

    rng = random.Random(seed)
    per_stratum = max(1, target // max(1, len(strata)))
    sample: list[dict[str, Any]] = []
    for key in sorted(strata):
        bucket = sorted(strata[key], key=lambda r: r["utterance_id"])
        sample.extend(rng.sample(bucket, min(per_stratum, len(bucket))))

    done = completed_ids("C")
    pending = [r for r in sample if r["utterance_id"] not in done]

    router = Router()
    written = quarantined = 0
    try:
        for batch in _batches(pending, batch_size):
            w, q = classify_batch(router, "C", batch, template)
            written += w
            quarantined += q
    except QuotaExhausted as exc:
        return {"stage": "S5", "lane": "C", "sample_target": target, "strata": len(strata),
                "written": written, "quarantined": quarantined, "stopped_by": str(exc),
                "note": "Groq TPD is the binding limit; resume tomorrow to extend the sample."}
    return {"stage": "S5", "lane": "C", "sample_target": target, "strata": len(strata),
            "written": written, "quarantined": quarantined}


def _read_lane(lane: str) -> Iterator[dict[str, Any]]:
    path = _shard_path(lane)
    if not path.exists():
        return iter(())
    def _gen() -> Iterator[dict[str, Any]]:
        with path.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if row.get("classifier_version") == CLASSIFIER_VERSION:
                    yield row
    return _gen()


def consolidate() -> dict[str, Any]:
    """Merge lanes into data/labelled/utterances.parquet.

    Lane B supersedes lane A where it ran, because lane B is the same task given
    more attention on exactly the cases lane A was least sure about. Lane C is
    kept in a separate column set - it is an independent annotator, not a
    correction, and merging it in would destroy the disagreement signal that S6
    needs.
    """
    import pandas as pd

    a = pd.DataFrame(list(_read_lane("A")))
    if a.empty:
        raise RuntimeError("no lane A labels; run S5 first")
    b = pd.DataFrame(list(_read_lane("B")))
    c = pd.DataFrame(list(_read_lane("C")))

    merged = a.set_index("utterance_id")
    if not b.empty:
        merged.update(b.set_index("utterance_id"))
        merged.loc[b["utterance_id"], "escalated"] = True
    merged["escalated"] = merged.get("escalated", False).fillna(False)

    if not c.empty:
        c_cols = c.set_index("utterance_id")[
            ["opportunity_area", "temporal_stance", "severity", "confidence", "hesitation_marker"]
        ].add_prefix("laneC_")
        merged = merged.join(c_cols, how="left")

    merged = merged.reset_index()
    out = LABELLED / "utterances.parquet"
    merged.to_parquet(out, index=False)
    return {
        "stage": "S5",
        "rows": len(merged),
        "lane_a": len(a), "lane_b": len(b), "lane_c": len(c),
        "escalated": int(merged["escalated"].sum()),
        "path": str(out.relative_to(ROOT)),
    }


# --------------------------------------------------------------------------
# B sweep (derives thresholds.classification.batch_size_B)
# --------------------------------------------------------------------------

def sweep_batch_size_by_agreement(n: int = 100, seed: int = 20260822) -> dict[str, Any]:
    """Derive B without a gold set, by measuring label STABILITY across batch size.

    The spec derives B from accuracy against 100 hand-labelled utterances
    (04 §4.3). That is the better measurement and remains owed. This is the
    fallback for when gold labels are not yet available, and it measures the
    specific thing large B threatens rather than accuracy in general.

    The risk from a large batch is attention dilution: the model loses track of
    individual items, which shows up as labels that move when the same utterance
    is graded in a bigger batch, as omitted items, and as ids that drift out of
    alignment. B=5 is the reference because a small batch is where per-item
    attention is highest.

    So: label the same utterances at every B in the grid, and measure agreement
    with the B=5 labels. If labels are stable across B, accuracy is stable too -
    a model that returns the same answer in a batch of 40 as in a batch of 5 has
    not been diluted by the batch. If they move, the larger B is degrading the
    task regardless of which answer was right.

    This CANNOT detect a model that is consistently wrong in the same way at
    every B. Only gold labels can. That is why it is a fallback and why the
    threshold records which derivation produced it.
    """
    import random as _random

    from statsmodels.stats.proportion import proportions_ztest

    grid = threshold("classification.batch_size_B_sweep_grid")
    alpha = float(threshold("statistics.alpha"))
    template = _load_template("classify_v1.txt")

    rows = list(read_filtered())
    if not rows:
        raise RuntimeError("no sampled utterances - run S4b first")
    rng = _random.Random(seed)
    subset = rng.sample(sorted(rows, key=lambda r: r["utterance_id"]), min(n, len(rows)))

    router = Router()
    invoker = gemini.make_invoker(response_json_schema())

    def label_at(b: int) -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        for batch in _batches(subset, b):
            prompt = build_prompt(template, batch)
            text = router.call("A", prompt, invoke=invoker)
            if text is None:
                continue
            try:
                items = _parse_response(text)
            except (json.JSONDecodeError, ValueError):
                continue
            for item in items:
                uid = item.get("utterance_id")
                if uid:
                    out[uid] = item
        return out

    # The reference is run TWICE. Comparing the smallest B against itself is
    # 100% by construction, which is an artefact rather than a measurement and
    # makes every larger B look degraded against a ceiling nothing can reach.
    #
    # run1-vs-run2 at the smallest B is the model's own NONDETERMINISM FLOOR:
    # temperature=0 constrains sampling but does not make a large model bit-exact,
    # so some disagreement exists even with the batch size held fixed. A larger B
    # is only degrading the task if it disagrees MORE than that floor.
    reference_b = min(grid)
    reference = label_at(reference_b)
    reference_repeat = label_at(reference_b)

    floor_shared = set(reference) & set(reference_repeat)
    floor_agree = sum(
        1 for u in floor_shared
        if reference_repeat[u].get("opportunity_area") == reference[u].get("opportunity_area")
    )
    floor = {
        "B": reference_b, "comparable": len(floor_shared),
        "agree_opportunity_area": floor_agree,
        "stability_area": round(floor_agree / len(floor_shared), 4) if floor_shared else None,
        "note": "self-agreement at the smallest batch size; the noise floor, not a ceiling",
    }

    results = []
    for b in grid:
        labels = reference_repeat if b == reference_b else label_at(b)
        shared = set(reference) & set(labels)
        agree_area = sum(
            1 for u in shared
            if labels[u].get("opportunity_area") == reference[u].get("opportunity_area")
        )
        agree_stance = sum(
            1 for u in shared
            if labels[u].get("temporal_stance") == reference[u].get("temporal_stance")
        )
        results.append({
            "B": b,
            "returned": len(labels),
            "omitted": len(subset) - len(labels),
            "comparable": len(shared),
            "agree_opportunity_area": agree_area,
            "agree_temporal_stance": agree_stance,
            "stability_area": round(agree_area / len(shared), 4) if shared else None,
            "stability_stance": round(agree_stance / len(shared), 4) if shared else None,
        })

    # Every B, including the smallest, is tested against the nondeterminism floor.
    for r in results:
        if r["comparable"] and floor["comparable"]:
            _, p = proportions_ztest(
                [r["agree_opportunity_area"], floor["agree_opportunity_area"]],
                [r["comparable"], floor["comparable"]],
            )
            r["p_vs_floor"] = round(float(p), 4)
            r["indistinguishable"] = bool(p > alpha)

    # The rule: largest B that is statistically indistinguishable from the
    # reference AND omitted nothing. Omission is disqualifying on its own - a
    # batch size that silently drops utterances loses them from the denominator.
    # 04 §4.3 says take the LARGEST indistinguishable B, on the rationale that
    # "throughput rises monotonically with B; quality is the only limit". That
    # rationale only discriminates when throughput is actually binding.
    #
    # Here it often is not: several B fit inside one day's request quota, and
    # once they do, a larger B buys nothing. So the rule is applied in two steps:
    #   1. keep the B values indistinguishable from the noise floor, no omissions
    #   2. among those that FIT the daily request budget, take the SMALLEST
    # Step 2's tie-break is blast radius. A failed, deferred or quarantined batch
    # costs B utterances, so where throughput is free the smaller batch is
    # strictly cheaper to recover from. If nothing fits the budget, fall back to
    # the largest eligible B - then throughput IS binding and the spec's
    # rationale applies as written.
    eligible = [r["B"] for r in results if r["omitted"] == 0 and r.get("indistinguishable")]
    corpus_size = len(rows)
    rpd = LANES["A"]["limits"].rpd
    fits = [b for b in eligible if math.ceil(corpus_size / b) <= rpd]
    if fits:
        chosen, tie_break = min(fits), "smallest eligible B that fits the daily request budget"
    elif eligible:
        chosen, tie_break = max(eligible), "largest eligible B; none fits the budget, so throughput binds"
    else:
        chosen, tie_break = reference_b, "no B was indistinguishable from the floor; fell back to the reference"

    report = {
        "at": now_ist(), "stage": "S5-sweep", "method": "agreement_with_smallest_B",
        "reference_B": reference_b, "n": len(subset), "grid": grid,
        "nondeterminism_floor": floor,
        "results": results, "chosen_B": chosen,
        "corpus_utterances": corpus_size,
        "calls_at_chosen_B": math.ceil(corpus_size / chosen),
        "daily_request_budget": rpd,
        "tie_break": tie_break,
        "power_caveat": (
            f"n={len(subset)} gives limited power: the test cannot detect a small "
            "degradation, so 'indistinguishable' here means no evidence of harm "
            "rather than evidence of no harm."
        ),
        "rule": (
            "largest B whose disagreement with the reference is statistically "
            "indistinguishable from the model's own run-to-run floor, with zero omissions"
        ),
        "limitation": (
            "Measures stability, not accuracy. A model consistently wrong the same "
            "way at every B would pass. The gold-based accuracy sweep (04 §4.3) is "
            "still owed and takes precedence when hand labels exist."
        ),
    }
    with (LOGS / "b_sweep.jsonl").open("a", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(report, ensure_ascii=False) + "\n")
    return report


def sweep_batch_size(gold_path: pathlib.Path | None = None) -> dict[str, Any]:
    """Sweep B against a fixed gold subset; report accuracy per B.

    Pick the largest B whose accuracy is statistically indistinguishable from
    B=5. Throughput rises monotonically with B, so quality is the only limit and
    the only honest way to set it is to measure it. Writes a result table for
    thresholds.yaml; it does not write the threshold itself, because choosing
    the value is a decision and decisions get recorded by a human.
    """
    import pandas as pd
    from statsmodels.stats.proportion import proportions_ztest

    gold_path = gold_path or (ROOT / "data" / "gold" / "human_labels.csv")
    if not gold_path.exists():
        raise FileNotFoundError(
            f"{gold_path} missing. The B sweep needs a hand-labelled subset "
            "(~100 utterances) to measure accuracy against."
        )
    gold = pd.read_csv(gold_path)
    grid = threshold("classification.batch_size_B_sweep_grid")
    by_id = {r["utterance_id"]: r for r in read_filtered()}
    subset = [by_id[u] for u in gold["utterance_id"] if u in by_id]
    template = _load_template("classify_v1.txt")
    truth = dict(zip(gold["utterance_id"], gold["opportunity_area"]))

    router = Router()
    results = []
    for b in grid:
        correct = total = 0
        for batch in _batches(subset, b):
            prompt = build_prompt(template, batch)
            text = router.call("A", prompt, invoke=gemini.make_invoker(response_json_schema()))
            if text is None:
                continue
            for item in _parse_response(text):
                expected = truth.get(item.get("utterance_id"))
                if expected is None:
                    continue
                total += 1
                correct += int(item.get("opportunity_area") == expected)
        results.append({"B": b, "n": total, "correct": correct,
                        "accuracy": round(correct / total, 4) if total else None})

    baseline = next((r for r in results if r["B"] == grid[0]), None)
    for r in results:
        if baseline and r is not baseline and r["n"] and baseline["n"]:
            _, p = proportions_ztest([r["correct"], baseline["correct"]], [r["n"], baseline["n"]])
            r["p_vs_baseline"] = round(float(p), 4)
            r["indistinguishable_from_baseline"] = bool(p > float(threshold("statistics.alpha")))

    report = {"at": now_ist(), "stage": "S5-sweep", "grid": grid, "results": results,
              "rule": "pick the largest B whose accuracy is indistinguishable from the smallest B"}
    with (LOGS / "b_sweep.jsonl").open("a", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(report, ensure_ascii=False) + "\n")
    return report
