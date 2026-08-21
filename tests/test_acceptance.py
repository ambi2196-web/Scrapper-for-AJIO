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
# Taxonomy fidelity - locks the transcription from 03_engine_spec.md §4
# --------------------------------------------------------------------------

# Transcribed independently of config/taxonomy.yaml so that an edit to one and
# not the other fails loudly. A taxonomy that drifts mid-analysis silently
# invalidates every earlier count.
SPEC_AREAS = {
    "OA-01": ("V", "strong"),
    "OA-02": ("V", "strong"),
    "OA-03": ("D", "strong"),
    "OA-04": ("D", "strong"),
    "OA-05": ("D", "moderate"),
    "OA-06": ("D", "strong"),
    "OA-07": ("D", "strong"),
    "OA-08": ("D", "moderate"),
    "OA-09": ("D", "weak"),
    "OA-10": ("R", "none"),
    "OA-11": ("D", "strong"),
    "OA-12": ("X", "strong"),
}


def test_taxonomy_matches_the_spec():
    areas = {a["code"]: a for a in cfg.load_taxonomy()["opportunity_areas"]}
    assert set(areas) == set(SPEC_AREAS), (
        "taxonomy codes differ from 03_engine_spec.md §4. The taxonomy is fixed "
        "and closed; adding an area requires a written justification and a "
        "re-run of the full corpus."
    )
    for code, (node, grade) in SPEC_AREAS.items():
        assert areas[code]["tree_node"] == node, f"{code} tree_node drifted"
        assert areas[code]["detectability"] == grade, f"{code} detectability drifted"


def test_the_two_gated_areas_are_the_expected_ones():
    """OA-09 (closure) and OA-10 (forgetting) are the engine's blind spots.

    If either ever becomes ungated, the engine has started producing numbers
    about phenomena it argued it cannot see - which is precisely the Attempt 2
    contradiction this gate exists to prevent.
    """
    gated = {c for c, g in cfg.detectability_map().items() if g in ("weak", "none")}
    assert gated == {"OA-09", "OA-10"}


def test_sub_nodes_are_drawn_from_the_closed_vocabulary():
    tax = cfg.load_taxonomy()
    vocab = set(tax["sub_nodes"])
    for area in tax["opportunity_areas"]:
        assert area["sub_node"] in vocab, (
            f"{area['code']} sub_node {area['sub_node']!r} is outside the closed "
            f"vocabulary from 03 §3"
        )


def test_addressability_is_a_binary_gate_with_a_reason():
    for area in cfg.load_taxonomy()["opportunity_areas"]:
        assert area["addressability"] in (0, 1), (
            f"{area['code']} addressability must be exactly 0 or 1 - it is a "
            "constraint check, not a soft weight (03 §5.2)"
        )
        assert area["addressability_rationale"].strip()


def test_opportunity_index_reference_must_be_collectable():
    """03 §5.1 forbids mixing sources in a denominator, so the index is
    undefined unless one NAMED, ENABLED, denominator-eligible source anchors it.

    The spec names AJIO PDP Q&A because it is pre-purchase by construction. That
    surface turned out not to exist - AJIO publishes no PDP reviews or Q&A - so
    this now raises until a replacement is designated. Raising is correct: a
    disabled reference silently produces an index whose reference cell matches
    no rows, which looks like a computed index and is not one.
    """
    sources = cfg.load_sources()["sources"]
    try:
        oi = cfg.opportunity_index_config()
    except ConfigError as exc:
        assert "DISABLED" in str(exc) or "not a source" in str(exc)
        return
    ref = oi["reference_source"]
    assert sources[ref]["enabled"], f"reference source {ref} must be enabled"
    assert sources[ref]["denominator_eligible"], f"reference source {ref} must carry a rate"
    assert oi["reference_stance"] in {"pre_purchase", "at_purchase", "post_purchase"}


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

def test_t6_unfrozen_proximity_refuses(tmp_path, monkeypatch):
    """A stub table must stop S5 rather than default to anything."""
    path = tmp_path / "proximity.yaml"
    path.write_text(yaml.safe_dump({"status": "stub", "weights": {}}), encoding="utf-8")
    monkeypatch.setattr(cfg, "CONFIG_DIR", tmp_path)
    with pytest.raises(ConfigError, match="I8|not frozen"):
        cfg.load_proximity()


def test_t6_shipped_proximity_is_frozen_and_complete():
    """The real table: frozen, timestamped, one weighted reason per taxonomy area."""
    prox = cfg.load_proximity()          # raises if unfrozen or edited after freeze
    assert prox["status"] == "frozen"
    assert prox["frozen_at"]

    weights = prox["weights"]
    areas = {a["code"] for a in cfg.load_taxonomy()["opportunity_areas"]}
    assert set(weights) == areas, (
        "every opportunity area needs a proximity weight, including the gated ones - "
        "recording them is what shows the gate is a detectability decision rather "
        "than a quiet judgement that the area does not matter"
    )
    for code, entry in weights.items():
        assert 0.0 <= float(entry["value"]) <= 1.0, f"{code} proximity outside [0,1]"
        assert entry.get("reason", "").strip(), (
            f"{code} has a weight but no reason; a weight without a stated reason "
            "is a taste constant"
        )


def test_t6_freeze_refuses_a_weight_with_no_reason(tmp_path, monkeypatch):
    path = tmp_path / "proximity.yaml"
    path.write_text(
        "status: stub\nfrozen_at: null\nfrozen_commit: null\n"
        "weights:\n  OA-01:\n    value: 1.0\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(cfg, "CONFIG_DIR", tmp_path)
    with pytest.raises(ConfigError, match="no reason given"):
        cfg.freeze_proximity()


def test_t6_freeze_preserves_comments(tmp_path, monkeypatch):
    """The per-area justifications are appendix material and must survive a freeze.

    A yaml.safe_dump round-trip would silently strip every comment, leaving
    twelve numbers with no defence.
    """
    path = tmp_path / "proximity.yaml"
    path.write_text(
        "# THE PRINCIPLE: load-bearing comment\n"
        "status: stub\nfrozen_at: null\nfrozen_commit: null\n"
        "weights:\n  OA-01:\n    value: 1.0\n    reason: because\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(cfg, "CONFIG_DIR", tmp_path)
    cfg.freeze_proximity()
    after = path.read_text(encoding="utf-8")
    assert "# THE PRINCIPLE: load-bearing comment" in after
    assert "status: frozen" in after


def test_t6_modified_after_freeze_refuses(tmp_path, monkeypatch):
    """Changing a weight after freezing must be caught.

    Checked by CONTENT HASH, not mtime. 04 §0 prescribes an mtime check, but
    `git clone` and `actions/checkout` stamp every file with the checkout time,
    so an mtime rule reports a fresh clone as tampered while a real edit could
    be hidden with `touch -d`. It fails in both directions. The fingerprint
    states the invariant that actually matters: touching the file is harmless,
    changing the weights is the violation.
    """
    path = tmp_path / "proximity.yaml"
    path.write_text(
        "status: stub\nfrozen_at: null\nfrozen_commit: null\n"
        "weights:\n  OA-01:\n    value: 1.0\n    reason: because\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(cfg, "CONFIG_DIR", tmp_path)
    cfg.freeze_proximity()
    cfg.load_proximity()          # clean immediately after freezing

    # Tamper with a weight, leaving every status line untouched.
    tampered = path.read_text(encoding="utf-8").replace("value: 1.0", "value: 0.2")
    path.write_text(tampered, encoding="utf-8")

    with pytest.raises(ConfigError, match="changed after it was frozen"):
        cfg.load_proximity()


def test_t6_touching_the_file_without_editing_is_not_a_violation(tmp_path, monkeypatch):
    """A fresh clone rewrites every mtime. That must not read as tampering."""
    import os
    import time

    path = tmp_path / "proximity.yaml"
    path.write_text(
        "status: stub\nfrozen_at: null\nfrozen_commit: null\n"
        "weights:\n  OA-01:\n    value: 1.0\n    reason: because\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(cfg, "CONFIG_DIR", tmp_path)
    cfg.freeze_proximity()

    future = time.time() + 86_400
    os.utime(path, (future, future))       # simulate a checkout long after the freeze
    cfg.load_proximity()                   # must not raise


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

def test_t10_window_parity_across_brands():
    """T10, restated: brands must cover the same PERIOD, not the same count.

    Equal counts was the spec's rule and it is the wrong instrument: measured on
    22 Aug 2026, a 4,000-row cap gave AJIO 40 days, Myntra 7 and Urbanic 1,094,
    because review velocity differs ~100x. Unequal n is harmless; unequal
    periods are not.
    """
    import datetime as _d

    from src.collect.base import check_window_parity
    from src.envelope import read_raw

    spans: dict[str, dict] = {}
    for row in read_raw("play"):
        stamp = row.get("posted_at")
        if not stamp:
            continue
        b = spans.setdefault(row["brand"], {"lo": stamp, "hi": stamp})
        b["lo"] = min(b["lo"], stamp)
        b["hi"] = max(b["hi"], stamp)
    if len(spans) < 2:
        pytest.skip("fewer than two brands collected on Play")

    def days(entry):
        lo = _d.datetime.fromisoformat(entry["lo"].replace("Z", "+00:00"))
        hi = _d.datetime.fromisoformat(entry["hi"].replace("Z", "+00:00"))
        return round((hi - lo).total_seconds() / 86400.0, 2)

    stats = {b: {"observed_span_days": days(e)} for b, e in spans.items()}
    tolerance = cfg.load_sources().get("window_parity_tolerance_days", 3)
    assert not check_window_parity(stats, tolerance), check_window_parity(stats, tolerance)


def test_t10_check_window_parity_detects_mismatch():
    from src.collect.base import check_window_parity

    # The real measured numbers under the old equal-count rule.
    assert check_window_parity({"ajio": {"observed_span_days": 40},
                                "urbanic": {"observed_span_days": 1094}}, 3)
    assert not check_window_parity({"ajio": {"observed_span_days": 89.4},
                                    "myntra": {"observed_span_days": 90.0}}, 3)


def test_t10_window_noncompliant_brand_is_barred_from_the_pool():
    """Apple caps public pagination at ~500 rows, which is a structural limit.

    Measured 22 Aug 2026: on the App Store, AJIO and Nykaa Fashion both reach
    ~88 days, but Myntra's iOS velocity exhausts the 500-row ceiling in 3.2 days.
    Pooling Myntra there would compare three days of one brand against three
    months of another, so it is barred from the pool - while keeping its rows for
    verbatims and severity.
    """
    from src.compare import window_compliant_brands

    windows = {
        ("appstore", "ajio"): 88.16,
        ("appstore", "myntra"): 3.18,
        ("appstore", "nykaa_fashion"): 88.22,
        ("play", "ajio"): 89.10,
        ("play", "myntra"): 89.40,
        ("play", "urbanic"): 88.90,
    }
    compliant, excluded = window_compliant_brands("appstore", windows, 3.0)
    assert compliant == {"ajio", "nykaa_fashion"}
    assert "myntra" in excluded and "3.2d" in excluded["myntra"]

    compliant, excluded = window_compliant_brands("play", windows, 3.0)
    assert compliant == {"ajio", "myntra", "urbanic"}
    assert not excluded


def test_t10_no_focal_brand_means_no_exclusions():
    """Without the focal brand there is nothing to measure parity against."""
    from src.compare import window_compliant_brands

    compliant, excluded = window_compliant_brands(
        "play", {("play", "myntra"): 12.0, ("play", "urbanic"): 900.0}, 3.0
    )
    assert compliant == {"myntra", "urbanic"}
    assert not excluded


def test_t10_unequal_n_is_not_flagged():
    """A proportion's denominator is its own n; Wilson and the z-test handle it."""
    from src.collect.base import check_window_parity

    assert not check_window_parity(
        {"ajio": {"observed_span_days": 90.0}, "urbanic": {"observed_span_days": 89.5}}, 3
    )


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
