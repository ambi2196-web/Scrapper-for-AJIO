"""S9 - emit exactly three deck-facing files, plus the placeholder sweep.

opportunity_index.csv, blind_spots.md, verbatims.md. The deck reads from nothing
else. Any number in the deck that cannot be traced to a row in one of these
three files does not go in the deck - which is a rule about the deck, but it is
enforceable only because this stage is the sole writer of them.

blind_spots.md is not an apology section. An area the engine cannot adjudicate
from public text is a finding: it says where the public record runs out and
where primary research would have to start. Listing them is what separates "we
measured what we could" from "we measured what there was".

T11, the placeholder sweep, runs last and fails loudly. Attempt 2 shipped a
literal «baseline» placeholder to a slide. This is the automated version of
never doing that again.
"""
from __future__ import annotations

import json
import pathlib
import re
from typing import Any

from src.config import ROOT, load_taxonomy
from src.envelope import now_ist

OUT = ROOT / "data" / "out"
DASHBOARD_DATA = ROOT / "data" / "dashboard"
LOGS = ROOT / "logs"

THE_THREE_FILES = ("opportunity_index.csv", "blind_spots.md", "verbatims.md")

# T11: nothing that looks like an unfinished thought survives to emit.
PLACEHOLDER_PATTERNS = [
    (re.compile(r"«[^»]*»"), "guillemet placeholder"),
    (re.compile(r"\bTODO\b", re.IGNORECASE), "TODO marker"),
    (re.compile(r"\bXXX\b"), "XXX marker"),
    (re.compile(r"\bTBD\b", re.IGNORECASE), "TBD marker"),
    (re.compile(r"\bFIXME\b", re.IGNORECASE), "FIXME marker"),
    (re.compile(r"(?<![\w?])\?{2,}(?![\w?])"), "bare question-mark placeholder"),
    (re.compile(r"\bplaceholder\b", re.IGNORECASE), "the word placeholder"),
    (re.compile(r"\blorem ipsum\b", re.IGNORECASE), "lorem ipsum"),
]


class EmitError(RuntimeError):
    pass


# --------------------------------------------------------------------------
# 1. opportunity_index.csv
# --------------------------------------------------------------------------

def emit_opportunity_index() -> Any:
    import pandas as pd

    agg_path = OUT / "aggregates.parquet"
    cmp_path = OUT / "comparisons.parquet"
    if not agg_path.exists():
        raise EmitError("data/out/aggregates.parquet missing - run S7")
    agg = pd.read_parquet(agg_path)
    comparisons = pd.read_parquet(cmp_path) if cmp_path.exists() else pd.DataFrame()

    cols = ["source", "brand", "temporal_stance", "opportunity_area", "detectability",
            "n_area", "n_cell", "proportion", "ci_low", "ci_high", "severity_mean",
            "proximity_weight", "oi", "gate_reason"]
    index = agg[[c for c in cols if c in agg.columns]].copy()

    if not comparisons.empty:
        index = index.merge(
            comparisons[["source", "temporal_stance", "opportunity_area",
                         "ratio", "difference", "p", "n_ajio", "n_pool", "significant_at_alpha"]],
            on=["source", "temporal_stance", "opportunity_area"], how="left",
        )

    index = index.sort_values(["source", "temporal_stance", "oi"], ascending=[True, True, False])
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / "opportunity_index.csv"
    index.to_csv(path, index=False)
    return index


# --------------------------------------------------------------------------
# 2. blind_spots.md
# --------------------------------------------------------------------------

def emit_blind_spots() -> str:
    import pandas as pd

    tax = load_taxonomy()
    agg = pd.read_parquet(OUT / "aggregates.parquet") if (OUT / "aggregates.parquet").exists() else pd.DataFrame()

    gated = [a for a in tax["opportunity_areas"] if a["detectability"] in ("weak", "none")]
    observed_areas = set(agg["opportunity_area"]) if not agg.empty else set()
    never_seen = [a for a in tax["opportunity_areas"]
                  if a["code"] not in observed_areas and a["detectability"] in ("strong", "moderate")]

    lines = [
        "# Blind spots",
        "",
        f"_Generated {now_ist()} by S9. Read alongside `opportunity_index.csv`._",
        "",
        "This register exists because the boundary of the method is itself a finding.",
        "An area listed here is not an area that does not matter — it is an area the",
        "public record cannot adjudicate, and therefore one where a number would be a",
        "guess wearing a decimal point.",
        "",
        "## 1. Gated by detectability",
        "",
        "These areas carry `oi = null` in the index. The gate is applied before",
        "aggregation, so no value was computed and none can leak into a slide.",
        "",
    ]
    if gated:
        lines += ["| Area | Name | Grade | Why public text cannot adjudicate it |",
                  "|---|---|---|---|"]
        for a in gated:
            rationale = a.get("detectability_rationale") or "—"
            lines.append(f"| {a['code']} | {a['name']} | {a['detectability']} | {rationale} |")
    else:
        lines.append("_No area is graded weak or none in the frozen taxonomy._")

    lines += ["", "## 2. In the taxonomy, absent from the corpus", ""]
    if never_seen:
        lines += ["Adjudicable in principle, but no utterance was classified into them.",
                  "Absence of evidence here is weak evidence of absence: these are",
                  "candidates for primary research rather than for a zero.", "",
                  "| Area | Name | Grade |", "|---|---|---|"]
        for a in never_seen:
            lines.append(f"| {a['code']} | {a['name']} | {a['detectability']} |")
    else:
        lines.append("_Every adjudicable area in the taxonomy is represented in the corpus._")

    lines += ["", "## 3. Sources that cannot carry a rate", "",
              "Reddit, YouTube, the complaint aggregators and X are marked",
              "`denominator_eligible: false`. They supply mechanism and verbatims. Their",
              "selection bias is structural rather than sizeable — people arrive at a",
              "complaint site because they have a complaint — so a proportion computed",
              "from them would describe the venue, not the brand. S7 refuses them in code.",
              ""]

    if not agg.empty and "gate_reason" in agg.columns:
        other = agg[agg["gate_reason"].notna()]
        reasons = other["gate_reason"].value_counts().to_dict()
        if reasons:
            lines += ["## 4. Other gates triggered in this run", "",
                      "| Reason | Rows |", "|---|---|"]
            lines += [f"| {r} | {n} |" for r, n in reasons.items()]
            lines.append("")

    text = "\n".join(lines)
    (OUT / "blind_spots.md").write_text(text, encoding="utf-8", newline="\n")
    return text


# --------------------------------------------------------------------------
# 3. verbatims.md
# --------------------------------------------------------------------------

def emit_verbatims(top_n: int = 3) -> str:
    """Top evidence quotes per area by severity, with source and permalink.

    This is the file that saves you at 2 a.m. the night before submission: every
    claim in the deck should have a quote sitting behind it, already traced.
    """
    import pandas as pd

    path = ROOT / "data" / "labelled" / "utterances.parquet"
    if not path.exists():
        raise EmitError("data/labelled/utterances.parquet missing - run S5")
    df = pd.read_parquet(path)
    df = df[df["opportunity_area"].notna() & (df["opportunity_area"] != "none")]

    tax = {a["code"]: a for a in load_taxonomy()["opportunity_areas"]}

    lines = ["# Verbatims", "", f"_Generated {now_ist()} by S9._", "",
             "Top quotes per opportunity area, ranked by severity then by confidence.",
             "Every quote is an exact substring of the utterance it came from (invariant I3),",
             "so each one traces back to a character span in a specific review.", ""]

    for area, block in df.groupby("opportunity_area"):
        meta = tax.get(area, {})
        lines.append(f"## {area} — {meta.get('name', 'unknown area')}")
        if meta.get("definition"):
            lines.append(f"_{meta['definition']}_")
        lines.append("")
        for stance, sblock in block.groupby("temporal_stance"):
            ranked = sblock.sort_values(["severity", "confidence"], ascending=False).head(top_n)
            if ranked.empty:
                continue
            lines.append(f"**{stance}** ({len(sblock)} utterances)")
            lines.append("")
            for _, row in ranked.iterrows():
                quote = (row["evidence_quote"] or "").replace("\n", " ").strip()
                sev = row.get("severity")
                url = row.get("url") or ""
                link = f"[{row['source']}]({url})" if url else row["source"]
                lines.append(f"- > {quote}")
                lines.append(f"  — {link} · {row['brand']} · severity {sev} · "
                             f"lang {row.get('language')} · `{row['utterance_id'][:12]}`")
            lines.append("")

    text = "\n".join(lines)
    (OUT / "verbatims.md").write_text(text, encoding="utf-8", newline="\n")
    return text


# --------------------------------------------------------------------------
# 4. Dashboard snapshot
# --------------------------------------------------------------------------

# The run reports the dashboard renders. These live in logs/, which is
# gitignored - the ledger and the drop log carry per-call and per-row detail
# that has no business in a public repo. But the *summary* line of each is pure
# derived statistic: counts, rates, kappas. No review text, no ids, no
# reviewer names.
#
# Shipping them is not a relaxation of B5, it is what B5 was for. The
# reliability figures ARE the evidence for every other number in the app, and
# for eight days they did not travel: the hand-labelled sample was scored on 31
# Aug, the gate was computed, and the deployed dashboard went on reporting
# "no kappa has been computed yet" because the only copy sat in an ignored file.
# A validation nobody can see has not been performed, as far as a reader is
# concerned.
REPORTS_IN_SNAPSHOT = (
    "s6_human_vs_model",   # human gold set vs lane A - the reliability gate
    "s6_model_vs_model",   # lane A vs lane C - two independent annotators
    "s4_report",           # drop profile
    "s2_report",           # language mix, posted_at coverage
    "s9_report",
)


def read_jsonl(path: pathlib.Path) -> tuple[list[dict[str, Any]], int]:
    """Parse an append-only log, tolerating torn lines. Returns (rows, skipped).

    These logs are appended by long-running stages, and one line in the 2,575-row
    LLM ledger is a fragment: a 429 body split across two lines by two writers
    interleaving mid-append. A strict reader turns that single corrupt byte range
    into a failed emit - one lost line taking down the whole snapshot, including
    the reliability report that has nothing to do with it. Skipped lines are
    counted and reported rather than passed over in silence.
    """
    rows: list[dict[str, Any]] = []
    skipped = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            skipped += 1
    return rows, skipped


def _latest_line(name: str) -> dict[str, Any] | None:
    """Last well-formed JSON object in logs/<name>.jsonl, or None."""
    path = LOGS / f"{name}.jsonl"
    if not path.exists():
        return None
    rows, _ = read_jsonl(path)
    return rows[-1] if rows else None


def _ledger_summary() -> list[dict[str, Any]] | None:
    """Per-lane call totals. Aggregated deliberately: the ledger itself holds one
    row per call and is not a public artifact."""
    import pandas as pd

    path = LOGS / "llm_ledger.jsonl"
    if not path.exists():
        return None
    rows, skipped = read_jsonl(path)
    if not rows:
        return None
    if skipped:
        print(f"  note: {skipped} unparseable line(s) in llm_ledger.jsonl skipped")
    df = pd.DataFrame(rows)
    for col in ("prompt_tokens", "completion_tokens", "latency_s", "rate_limit_wait_s"):
        if col not in df.columns:
            df[col] = 0
    df["tokens"] = df["prompt_tokens"].fillna(0) + df["completion_tokens"].fillna(0)
    summary = (df.groupby(["lane", "provider", "model"])
               .agg(calls=("outcome", "size"),
                    ok=("outcome", lambda s: (s == "ok").sum()),
                    tokens=("tokens", "sum"),
                    median_latency=("latency_s", "median"),
                    rate_limit_wait=("rate_limit_wait_s", "sum"))
               .reset_index())
    return json.loads(summary.to_json(orient="records"))


def _emit_reports_snapshot() -> dict[str, Any]:
    """Write data/dashboard/reports.json - the summary lines the app renders."""
    payload: dict[str, Any] = {"generated_at": now_ist()}
    present: dict[str, Any] = {}
    for name in REPORTS_IN_SNAPSHOT:
        latest = _latest_line(name)
        if latest is not None:
            payload[name] = latest
            present[name] = latest.get("at")
    ledger = _ledger_summary()
    if ledger is not None:
        payload["llm_ledger_summary"] = ledger
        present["llm_ledger_summary"] = len(ledger)

    (DASHBOARD_DATA / "reports.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8", newline="\n",
    )
    return present


def emit_dashboard_snapshot() -> dict[str, Any]:
    """Small aggregated parquet set the Streamlit app reads.

    Deliberately aggregated, not raw. The public repo carries what the dashboard
    needs to render and nothing more: the full scraped corpus stays local.
    """
    import pandas as pd

    DASHBOARD_DATA.mkdir(parents=True, exist_ok=True)
    written = {}

    for name in ("aggregates", "comparisons"):
        src = OUT / f"{name}.parquet"
        if src.exists():
            df = pd.read_parquet(src)
            df.to_parquet(DASHBOARD_DATA / f"{name}.parquet", index=False)
            written[name] = len(df)

    labelled = ROOT / "data" / "labelled" / "utterances.parquet"
    if labelled.exists():
        df = pd.read_parquet(labelled)
        keep = ["utterance_id", "source", "brand", "temporal_stance", "opportunity_area",
                "severity", "confidence", "hesitation_marker", "language", "posted_at",
                "evidence_quote", "url", "escalated"]
        slim = df[[c for c in keep if c in df.columns]].copy()
        # Quotes only, never the full review body: the dashboard needs the
        # evidence, not a republished corpus.
        slim.to_parquet(DASHBOARD_DATA / "evidence.parquet", index=False)
        written["evidence"] = len(slim)

    for name in ("drop_log", "s2_report", "s4_report"):
        src = LOGS / f"{name}.jsonl"
        if src.exists():
            rows, _ = read_jsonl(src)
            if rows:
                pd.DataFrame(rows).to_parquet(DASHBOARD_DATA / f"{name}.parquet", index=False)
                written[name] = len(rows)

    written["reports"] = _emit_reports_snapshot()

    manifest = {"generated_at": now_ist(), "files": written}
    (DASHBOARD_DATA / "manifest.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8", newline="\n"
    )
    return manifest


# --------------------------------------------------------------------------
# 5. T11 - the placeholder sweep
# --------------------------------------------------------------------------

def placeholder_sweep(strict: bool = True) -> list[dict[str, Any]]:
    """Scan data/out/ for anything that looks unfinished. Fails loudly and names it."""
    findings: list[dict[str, Any]] = []
    for path in sorted(OUT.rglob("*")):
        if not path.is_file() or path.suffix not in (".csv", ".md", ".json", ".txt"):
            continue
        try:
            rel = str(path.relative_to(ROOT))
        except ValueError:
            rel = str(path)
        text = path.read_text(encoding="utf-8", errors="replace")
        for lineno, line in enumerate(text.splitlines(), 1):
            for pattern, label in PLACEHOLDER_PATTERNS:
                if pattern.search(line):
                    findings.append({
                        "file": rel, "line": lineno,
                        "kind": label, "text": line.strip()[:200],
                    })

    if findings and strict:
        detail = "\n".join(
            f"  {f['file']}:{f['line']}  [{f['kind']}]  {f['text']}" for f in findings[:40]
        )
        raise EmitError(
            f"T11 placeholder sweep failed: {len(findings)} occurrence(s) in data/out/.\n"
            f"{detail}\n"
            "  Attempt 2 shipped a literal «baseline» to a slide. Nothing unfinished leaves S9."
        )
    return findings


def run(strict: bool = True) -> dict[str, Any]:
    index = emit_opportunity_index()
    emit_blind_spots()
    emit_verbatims()
    snapshot = emit_dashboard_snapshot()

    missing = [f for f in THE_THREE_FILES if not (OUT / f).exists()]
    if missing:
        raise EmitError(f"S9 did not produce {missing}")

    findings = placeholder_sweep(strict=strict)
    report = {"at": now_ist(), "stage": "S9", "rows_in_index": int(len(index)),
              "files": list(THE_THREE_FILES), "dashboard_snapshot": snapshot,
              "placeholder_findings": len(findings)}
    LOGS.mkdir(parents=True, exist_ok=True)
    with (LOGS / "s9_report.jsonl").open("a", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(report, ensure_ascii=False, default=str) + "\n")
    return report
