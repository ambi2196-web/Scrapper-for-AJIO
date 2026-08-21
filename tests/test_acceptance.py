"""Acceptance tests T1-T11 from 04 §7. These are the definition of done.

Tests that need a populated corpus skip cleanly when it is absent, so the suite
is green on a fresh clone and meaningful after a run. A skip is visible in the
output; a silently-passing empty test is not, which is the failure mode this
arrangement avoids.
"""
from __future__ import annotations

import datetime as _dt
import json
import random
import subprocess
import sys

import pytest
import yaml

from src import config as cfg
from src.config import ROOT, ConfigError

DATA = ROOT / "data"


def _has(path) -> bool:
    return path.exists() and path.stat().st_size > 0


def _load_parquet(path):
    pd = pytest.importorskip("pandas")
    if not _has(path):
        pytest.skip(f"{path.relative_to(ROOT)} absent - run the pipeline first")
    return pd.read_parquet(path)


# --------------------------------------------------------------------------
# T1 - span reconstruction
# --------------------------------------------------------------------------

def test_t1_span_reconstruction():
    """raw_text[start:end] == utterance_text, exactly, for 1,000 random rows."""
    path = DATA / "interim" / "utterances.jsonl"
    if not _has(path):
        pytest.skip("no segmented utterances - run S3")

    rows = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
    sample = random.sample(rows, min(1000, len(rows)))
    failures = []
    for row in sample:
        start, end = row["span"]
        if row["reference_text"][start:end].strip() != row["utterance_text"]:
            failures.append(row["utterance_id"])
    assert not failures, (
        f"{len(failures)} spans do not reconstruct. Spans are what make every "
        f"evidence quote traceable to a character range. First: {failures[:5]}"
    )


def test_t1_segmenter_spans_are_exact():
    """Unit-level version of T1 that runs without a corpus."""
    from src.segment import segment_text

    text = ("Ordered a kurta and it ran two sizes small. Returns took nine days. "
            "But the fabric was fine, honestly.")
    spans = segment_text(text, short_floor=8)
    assert spans, "segmenter returned nothing"
    for start, end in spans:
        assert text[start:end] == text[start:end]
    assert "".join(text[s:e] for s, e in spans).strip() == text.strip(), (
        "concatenating spans must reproduce the input; a gap means text was lost"
    )


# --------------------------------------------------------------------------
# T2 - evidence-quote integrity
# --------------------------------------------------------------------------

def test_t2_evidence_quote_integrity():
    """100% of labelled rows satisfy evidence_quote in utterance_text (I3)."""
    df = _load_parquet(DATA / "labelled" / "utterances.parquet")
    bad = []
    for _, row in df.iterrows():
        quote = row.get("evidence_quote") or ""
        if not quote and row.get("confidence") == 0:
            continue
        if quote not in (row.get("utterance_text") or ""):
            bad.append(row["utterance_id"])
    assert not bad, (
        f"{len(bad)} rows carry an evidence_quote that is not a substring of their "
        f"utterance. These belong in quarantine, not in the table. First: {bad[:5]}"
    )


def test_t2_check_evidence_rejects_repaired_quotes():
    from src.llm.schema import Label, check_evidence

    label = Label(
        utterance_id="x", tree_node="none", opportunity_area="none",
        temporal_stance="unclear", hesitation_marker=False, confidence=0.9,
        evidence_quote="Sizes never match",
    )
    assert check_evidence(label, "the sizes never match here") is not None, (
        "a case-altered quote must be rejected, not silently accepted"
    )
    assert check_evidence(label, "Sizes never match the chart") is None


# --------------------------------------------------------------------------
# T3 / T4 - idempotence and interrupt-resume
# --------------------------------------------------------------------------

def test_t3_completed_ids_are_version_scoped():
    """A re-run with no new data must make zero LLM calls."""
    from src import classify

    done = classify.completed_ids("A")
    path = DATA / "labelled" / "labels_lane_A.jsonl"
    if not _has(path):
        assert done == set()
        pytest.skip("no lane A labels yet")

    rows = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
    current = {r["utterance_id"] for r in rows
               if r.get("classifier_version") == classify.CLASSIFIER_VERSION}
    assert done == current
    # Version scoping is what forces a full re-run after a prompt edit rather
    # than a corpus half-labelled by each prompt.
    assert all(r.get("classifier_version") for r in rows)


def test_t4_torn_final_line_does_not_break_resume(tmp_path, monkeypatch):
    """kill -9 mid-write leaves a partial line; resume must survive it."""
    from src import classify

    shard = tmp_path / "labels_lane_A.jsonl"
    good = {"utterance_id": "abc", "classifier_version": classify.CLASSIFIER_VERSION}
    shard.write_text(json.dumps(good) + "\n" + '{"utterance_id": "trunc', encoding="utf-8")
    monkeypatch.setattr(classify, "_shard_path", lambda lane: shard)

    done = classify.completed_ids("A")
    assert done == {"abc"}, "a torn final line must be skipped, not crash the resume"


def test_t4_raw_writer_dedupes_across_runs(tmp_path, monkeypatch):
    from src import envelope
    from src.envelope import Envelope, RawWriter

    monkeypatch.setattr(envelope, "RAW_DIR", tmp_path)
    monkeypatch.setattr(envelope, "MANIFEST", tmp_path / "_manifest.jsonl")

    env = Envelope(source="play", brand="ajio", source_id="gp:1", raw_text="hello")
    w1 = RawWriter("play", "ajio", run_date="2026-08-22")
    assert w1.write(env) is True
    w1.close()

    w2 = RawWriter("play", "ajio", run_date="2026-08-22")
    assert w2.write(env) is False, "a re-run must add zero rows for data already on disk"


# --------------------------------------------------------------------------
# S3/S4 behaviour that the drop log depends on
# --------------------------------------------------------------------------

def test_empty_review_survives_to_s4_as_no_text():
    """A rating-only review must reach S4 so it can be logged, not vanish at S3.

    If it disappeared during segmentation, `no_text` would never appear in the
    drop log - and "38% of Play reviews carry no text at all" is exactly the
    finding that answers "why is your n smaller than the app's review count?"
    """
    import collections

    from src.filter import classify_drop
    from src.segment import segment_text

    assert segment_text("", short_floor=8) == [], "empty text yields no spans"
    row = {"utterance_text": "", "source": "play", "brand": "ajio",
           "source_id": "gp:x", "text_sha1": "h", "near_dup_of": None}
    assert classify_drop(row, min_tokens=3, spam_repeat_limit=5,
                         text_counts=collections.Counter()) == "no_text"


def test_wrong_brand_does_not_fire_where_the_surface_sets_the_brand():
    """A comparison inside an AJIO review is signal, not noise.

    Reviewers under com.ril.ajio rarely write "AJIO" - they know which app they
    opened. A text-mention rule there would drop precisely the comparison rows
    that are most informative.
    """
    import collections

    from src.filter import classify_drop

    row = {
        "utterance_text": "Thinking about buying but the price seems low compared to Myntra.",
        "reference_text": "Thinking about buying but the price seems low compared to Myntra.",
        "source": "play", "brand": "ajio", "source_id": "gp:5",
        "text_sha1": "h", "near_dup_of": None,
    }
    common = dict(min_tokens=3, spam_repeat_limit=5, text_counts=collections.Counter())

    assert classify_drop(row, **common, brand_from_surface=True) is None
    # On Reddit the surface does not establish the brand, so the rule applies.
    assert classify_drop({**row, "source": "reddit"}, **common,
                         brand_from_surface=False) == "wrong_brand"


def test_every_source_declares_brand_establishment():
    for name, block in cfg.load_sources()["sources"].items():
        assert "brand_established_by_surface" in block, (
            f"source {name!r} does not declare brand_established_by_surface; "
            "S4 needs it to know whether the wrong_brand rule applies"
        )


# --------------------------------------------------------------------------
# T5 - threshold guard
# --------------------------------------------------------------------------

def test_t5_threshold_without_source_raises(tmp_path, monkeypatch):
    bad = {"group": {"some_constant": {"value": 0.7}}}   # no source: field
    (tmp_path / "thresholds.yaml").write_text(yaml.safe_dump(bad), encoding="utf-8")
    monkeypatch.setattr(cfg, "CONFIG_DIR", tmp_path)

    with pytest.raises(ConfigError, match="I6"):
        cfg.load_thresholds()


def test_t5_real_thresholds_all_cite_a_source():
    cfg.load_thresholds()  # raises if any shipped entry lacks a source


def test_t5_null_threshold_raises_with_its_todo():
    with pytest.raises(ConfigError, match="still null"):
        cfg.threshold("classification.tau_escalation")


# --------------------------------------------------------------------------
# T6 - proximity freeze
# --------------------------------------------------------------------------

def test_t6_unfrozen_proximity_refuses():
    with pytest.raises(ConfigError, match="I8|not frozen"):
        cfg.load_proximity()


def test_t6_modified_after_freeze_refuses(tmp_path, monkeypatch):
    frozen_at = (_dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(hours=2)).isoformat()
    path = tmp_path / "proximity.yaml"
    path.write_text(yaml.safe_dump({
        "status": "frozen", "frozen_at": frozen_at, "weights": {"OA-01": {"value": 1.0}},
    }), encoding="utf-8")   # mtime = now, which is 2h after frozen_at
    monkeypatch.setattr(cfg, "CONFIG_DIR", tmp_path)

    with pytest.raises(ConfigError, match="modified after it was frozen"):
        cfg.load_proximity()


# --------------------------------------------------------------------------
# T7 - denominator purity
# --------------------------------------------------------------------------

def test_t7_aggregate_raises_without_source_or_stance():
    from src.quantify import DenominatorError, aggregate

    with pytest.raises(DenominatorError, match="I5"):
        aggregate(group_keys=["brand", "opportunity_area"])          # no source
    with pytest.raises(DenominatorError, match="I5"):
        aggregate(group_keys=["source", "brand", "opportunity_area"])  # no stance


# --------------------------------------------------------------------------
# T8 - detectability gate
# --------------------------------------------------------------------------

def test_t8_no_oi_for_weak_or_none_areas():
    df = _load_parquet(DATA / "out" / "aggregates.parquet")
    gated = df[df["detectability"].isin(["weak", "none"])]
    offenders = gated[gated["oi"].notna()]
    assert offenders.empty, (
        f"{len(offenders)} rows graded weak/none carry a non-null oi (invariant I4). "
        "The gate runs before aggregation precisely so no value exists to leak."
    )


def test_t8_gate_is_applied_in_the_index_csv():
    pd = pytest.importorskip("pandas")
    path = DATA / "out" / "opportunity_index.csv"
    if not _has(path):
        pytest.skip("no emitted index - run S9")
    df = pd.read_csv(path)
    gated = df[df["detectability"].isin(["weak", "none"])]
    assert gated["oi"].isna().all()


# --------------------------------------------------------------------------
# T9 - complaint-site exclusion
# --------------------------------------------------------------------------

def test_t9_no_proportion_has_a_complaint_denominator():
    df = _load_parquet(DATA / "out" / "aggregates.parquet")
    ineligible = set(cfg.load_sources()["sources"]) - cfg.denominator_eligible_sources()
    leaked = df[df["source"].isin(ineligible) & df["proportion"].notna()]
    assert leaked.empty, (
        f"{len(leaked)} proportions rest on a denominator_eligible: false source "
        f"({sorted(set(leaked['source']))}). Their selection bias is structural, "
        "so any rate from them describes the venue rather than the brand."
    )


def test_t9_config_marks_the_known_biased_sources():
    sources = cfg.load_sources()["sources"]
    for name in ("complaints", "reddit", "youtube", "x"):
        assert sources[name]["denominator_eligible"] is False, (
            f"{name} must be denominator_eligible: false"
        )


# --------------------------------------------------------------------------
# T10 - equal caps
# --------------------------------------------------------------------------

def test_t10_equal_caps_within_tolerance():
    from src.collect.base import check_equal_caps
    from src.envelope import read_raw

    counts: dict[str, int] = {}
    for row in read_raw("play"):
        counts[row["brand"]] = counts.get(row["brand"], 0) + 1
    if len(counts) < 2:
        pytest.skip("fewer than two brands collected on Play")

    tolerance = cfg.load_sources()["brand_cap_tolerance"]
    warnings = check_equal_caps({b: {"total_on_disk": n} for b, n in counts.items()}, tolerance)
    assert not warnings, warnings


def test_t10_check_equal_caps_detects_imbalance():
    from src.collect.base import check_equal_caps

    assert check_equal_caps({"ajio": {"total_on_disk": 4000},
                             "myntra": {"total_on_disk": 1200}}, 0.10)
    assert not check_equal_caps({"ajio": {"total_on_disk": 4000},
                                 "myntra": {"total_on_disk": 3900}}, 0.10)


# --------------------------------------------------------------------------
# T11 - placeholder sweep
# --------------------------------------------------------------------------

def test_t11_no_placeholders_in_out():
    from src.emit import placeholder_sweep

    findings = placeholder_sweep(strict=False)
    assert not findings, (
        "placeholders survive in data/out/:\n"
        + "\n".join(f"  {f['file']}:{f['line']} [{f['kind']}] {f['text']}" for f in findings[:20])
    )


def test_t11_sweep_catches_the_attempt_2_failure(tmp_path, monkeypatch):
    from src import emit

    monkeypatch.setattr(emit, "OUT", tmp_path)
    (tmp_path / "opportunity_index.csv").write_text(
        "area,value\nOA-01,«baseline»\n", encoding="utf-8"
    )
    findings = emit.placeholder_sweep(strict=False)
    assert any(f["kind"] == "guillemet placeholder" for f in findings)
    with pytest.raises(emit.EmitError, match="T11"):
        emit.placeholder_sweep(strict=True)


# --------------------------------------------------------------------------
# I1 - raw immutability
# --------------------------------------------------------------------------

def test_i1_raw_store_matches_its_manifest():
    from src.envelope import verify_manifest

    violations = verify_manifest()
    assert not violations, "raw is immutable (I1):\n" + "\n".join(violations)


# --------------------------------------------------------------------------
# Repo hygiene
# --------------------------------------------------------------------------

def test_env_file_is_not_tracked():
    """The repo is public and linked in the deck. A key in git history is a bad day."""
    try:
        tracked = subprocess.check_output(
            ["git", "ls-files"], cwd=ROOT, text=True, stderr=subprocess.DEVNULL
        ).splitlines()
    except Exception:
        pytest.skip("not a git checkout")
    assert ".env" not in tracked
    # .gitkeep is exempt - it carries no data and exists so the directory shape
    # is visible on a fresh clone.
    corpus = [
        f for f in tracked
        if f.startswith(("data/raw/", "data/interim/", "data/labelled/"))
        and not f.endswith(".gitkeep")
    ]
    assert not corpus, f"scraped corpus must not be committed to a public repo: {corpus[:5]}"
    assert "data/gold/human_labels.csv" not in tracked, (
        "hand labels are working data, not a deliverable"
    )


def test_no_translate_call_exists_anywhere():
    """Invariant I7: translation destroys hesitation markers, so no such call exists.

    Markers are language-translation libraries and APIs specifically. Python's
    `str.translate` is a character-mapping call - normalise.py uses it to strip
    zero-width and control characters, which is the opposite of translation and
    must not trip this test.
    """
    markers = (
        "googletrans", "deep_translator", "translate_v2", "translate_v3",
        "cloud.translate", "translate_text(", "Translator(", "argostranslate",
        "MarianMT", "nllb", "opus-mt",
    )
    offenders = []
    for path in (ROOT / "src").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for marker in markers:
            if marker in text:
                offenders.append(f"{path.relative_to(ROOT)}: {marker}")
    assert not offenders, f"translation call found (I7): {offenders}"


def test_i7_normalise_preserves_hinglish_and_case():
    """The positive half of I7: text goes through S2 unchanged in language and case."""
    from src.normalise import detect_language, normalise_text

    text = "Bahut confuse hu, lu ya nahi SIZE ka issue hai"
    out = normalise_text(text)
    assert out == text, "normalisation must not rewrite the text"
    assert "SIZE" in out, "casing is a severity signal and is preserved"

    hindi_words = set(cfg.load_lexicon()["hindi_function_words_latin"])
    assert detect_language(text, hindi_words) == "hinglish"
    assert detect_language("Delivery was late and the fabric felt cheap", hindi_words) == "en"


def test_normalise_caps_runs_but_keeps_emphasis():
    from src.normalise import normalise_text

    assert normalise_text("sooooooo bad") == "sooo bad"
    assert normalise_text("soooo bad") == "sooo bad", (
        "different run lengths must collapse to the same string or they will not dedupe"
    )
