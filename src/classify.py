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
