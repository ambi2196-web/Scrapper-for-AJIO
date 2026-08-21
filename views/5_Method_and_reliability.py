"""Page 5 — Method & reliability. The page that makes the numbers believable.

Most dashboards put this in an appendix nobody opens. It sits in the main
navigation here because the reliability statistics are the reason to trust
anything on the other pages, and because a κ that fails its gate has to be as
visible as the finding it disqualifies.
"""
from __future__ import annotations

import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

import json

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from dashboard import data_access as da

st.title("Method & reliability")

ROOT = da.ROOT
LOGS = ROOT / "logs"


def _last_jsonl(name: str) -> dict:
    path = LOGS / name
    if not path.exists():
        return {}
    lines = [l for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
    return json.loads(lines[-1]) if lines else {}


# --------------------------------------------------------------------------
tab_kappa, tab_drops, tab_pipeline, tab_quota = st.tabs(
    ["Reliability (κ)", "Drop log", "Pipeline & invariants", "LLM budget"]
)

# --- κ ---------------------------------------------------------------------
with tab_kappa:
    st.subheader("Two independent reliability numbers")
    st.markdown("""
**1 · Model vs model.** Lane A (Gemini 2.5 Flash-Lite) against lane C (Groq
`gpt-oss-120b`), each labelling the same utterances independently. Lane C never
sees lane A's output — a "please check this label" framing would produce
agreement bias and inflate κ while measuring nothing.

This design came out of a constraint rather than an ambition: Groq's free tier
caps at 8,000 tokens per *minute* and 200,000 per day, which makes it unusable
as a bulk classifier. Using it instead as a second annotator from a different
vendor turns that limitation into genuine inter-annotator disagreement — which
is a stronger validation design than running the same model twice would have been.

**2 · Human vs model.** A blind hand-labelled sample, over-sampled in the stratum
where the two models disagreed (that is where a human label is most
informative), then reweighted to the population before κ is computed. Without
the reweighting, κ would *understate* agreement — the wrong direction of error to
publish.
""")

    human = _last_jsonl("s6_human_vs_model.jsonl")
    model = _last_jsonl("s6_model_vs_model.jsonl")

    if not human and not model:
        st.info(
            "No κ has been computed yet. Run:\n\n"
            "```bash\npython -m src.cli validate model-kappa\n"
            "python -m src.cli labelling-sheet --n 200\n"
            "# hand-label, save as data/gold/human_labels.csv\n"
            "python -m src.cli validate human-kappa\n```"
        )
    else:
        rows = []
        for label, report in (("human vs lane A", human), ("lane A vs lane C", model)):
            for field, res in (report.get("per_field") or {}).items():
                rows.append({
                    "comparison": label, "field": field,
                    "kappa": res.get("kappa"), "band": res.get("band"),
                    "n": res.get("n"),
                    "ci_low": (res.get("ci95") or [None, None])[0],
                    "ci_high": (res.get("ci95") or [None, None])[1],
                })
        kdf = pd.DataFrame(rows)

        if not kdf.empty:
            floor = human.get("kappa_floor", 0.61)
            fig = go.Figure()
            for comparison, block in kdf.groupby("comparison"):
                fig.add_trace(go.Bar(
                    name=comparison, x=block["field"], y=block["kappa"],
                    marker_color="#2F5BEA" if "human" in comparison else "#8B93A7",
                    error_y=dict(
                        type="data", symmetric=False,
                        array=(block["ci_high"] - block["kappa"]).fillna(0),
                        arrayminus=(block["kappa"] - block["ci_low"]).fillna(0),
                        color="#111827", thickness=1.2, width=4,
                    ),
                    customdata=block[["band", "n"]].values,
                    hovertemplate="κ=%{y:.3f} (%{customdata[0]}) · n=%{customdata[1]:.0f}<extra></extra>",
                ))
            fig.add_hline(y=floor, line_dash="dash", line_color="#B91C1C",
                          annotation_text=f"substantial-agreement floor κ={floor}",
                          annotation_position="top left")
            fig.update_layout(
                barmode="group", height=420,
                yaxis=dict(title="Cohen's κ", range=[0, 1], gridcolor="#EEF1F7"),
                xaxis=dict(title=""), plot_bgcolor="white", paper_bgcolor="white",
                legend=dict(orientation="h", y=1.1, x=0),
                margin=dict(l=10, r=30, t=40, b=30), font=dict(size=13),
            )
            st.plotly_chart(fig, use_container_width=True)
            st.caption(
                "κ is reported **per field**, deliberately. A blended κ hides the case where "
                "`temporal_stance` — the field the entire engine rests on — is the weak one."
            )
            st.dataframe(kdf, use_container_width=True, hide_index=True)

        if human.get("gate"):
            if human["gate"] == "PASS":
                st.success(f"**Gate: PASS.** {human.get('gate_action')}")
            else:
                st.error(f"**Gate: FAIL.** {human.get('gate_action')}")
            st.caption(f"Floor cited to {human.get('floor_source')}.")

# --- drop log ---------------------------------------------------------------
with tab_drops:
    st.subheader("What was dropped, and why")
    st.markdown(
        "The drop profile is itself a finding about each instrument. "
        "*\"38% of Play Store reviews carry no text at all\"* is a real statement about the "
        "surface — and it pre-empts the obvious question of why n is smaller than the app's "
        "advertised review count."
    )

    s4 = _last_jsonl("s4_report.jsonl")
    if not s4:
        st.info("No drop log yet. Run `python -m src.cli filter`.")
    else:
        c1, c2, c3 = st.columns(3)
        c1.metric("Utterances in", f"{s4.get('utterances_in', 0):,}")
        c2.metric("Kept", f"{s4.get('kept', 0):,}")
        c3.metric("Drop rate", f"{s4.get('drop_rate', 0):.1%}")

        by_reason = s4.get("by_reason") or {}
        if by_reason:
            reasons = pd.DataFrame(
                sorted(by_reason.items(), key=lambda kv: kv[1]), columns=["reason", "count"]
            )
            fig = go.Figure(go.Bar(
                y=reasons["reason"], x=reasons["count"], orientation="h", marker_color="#8B93A7",
                hovertemplate="%{y}: %{x:,} dropped<extra></extra>",
            ))
            fig.update_layout(
                height=max(260, 42 * len(reasons)),
                xaxis=dict(title="utterances dropped", gridcolor="#EEF1F7"),
                yaxis=dict(title=""), plot_bgcolor="white", paper_bgcolor="white",
                margin=dict(l=10, r=30, t=20, b=40), font=dict(size=13),
            )
            st.plotly_chart(fig, use_container_width=True)

        by_source = s4.get("by_source") or {}
        if by_source:
            st.markdown("**Per source**")
            rows = []
            for src, block in by_source.items():
                row = {"source": src, "total": block.get("total"), "drop_rate": block.get("drop_rate")}
                row.update(block.get("dropped") or {})
                rows.append(row)
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

    s2 = _last_jsonl("s2_report.jsonl")
    if s2:
        st.markdown("**Language mix and `posted_at` coverage**")
        c1, c2 = st.columns(2)
        with c1:
            st.write(s2.get("language_distribution"))
        with c2:
            nulls = s2.get("posted_at_null_rate_by_source") or {}
            st.write({k: f"{v:.1%}" for k, v in nulls.items()})
        st.caption(
            "A source whose `posted_at` is largely null cannot support a trend claim. "
            "The cut-off is derived from these observed rates rather than picked as a round number."
        )

# --- pipeline ---------------------------------------------------------------
with tab_pipeline:
    st.subheader("Stages")
    st.markdown("""
| Stage | Does | Guarantee |
|---|---|---|
| **S1** collect | one envelope per item, per source | raw is append-only and checksummed; a later mutation is detectable |
| **S2** normalise | Unicode, language, near-duplicate hash | no translation, no lowercasing of stored text |
| **S3** segment | reviews → utterances | `raw_text[start:end]` reconstructs each utterance exactly |
| **S4** filter | drop with a logged reason | the drop log is an appendix table |
| **S5** classify | Gemini bulk + Groq second annotator | evidence quote must be an exact substring, or the row is quarantined |
| **S6** validate | κ per field, two comparisons | below the substantial band, numbers do not ship |
| **S7** quantify | Wilson intervals, gated | denominators never mix source or stance |
| **S8** compare | two-proportion z-test | AJIO excluded from its own pool; matched surfaces only |
| **S9** emit | three files | nothing unfinished survives the placeholder sweep |
""")

    st.subheader("Invariants, and how each is enforced")
    st.markdown("""
These are assertions in code, not conventions in a document. Each one exists
because it is a way a study like this quietly goes wrong.

| # | Invariant | Enforced by |
|---|---|---|
| I1 | Raw is immutable | append-only writer + SHA-256 manifest, re-verified in CI |
| I2 | One row per **utterance**, not per review | S3 raises if it emits exactly one row per review for every review |
| I3 | Every label carries an exact-substring evidence quote | asserted at S5 write time; failures go to quarantine, never to a repaired value |
| I4 | No number for an area graded weak or none | detectability gate applied *before* aggregation, so no value exists to leak |
| I5 | Denominators never mix source or stance | S7 raises if a grouping omits either key |
| I6 | No threshold without a stated source | the config loader raises on an entry lacking `source:` |
| I7 | Hinglish preserved, never translated | no translate call exists in the codebase, and a test asserts it |
| I8 | `wishlist_proximity` frozen before classification | a SHA-256 of the weights is recorded at freeze time and re-verified on every load |
""")

    st.info(
        "**Why the fuss.** A previous attempt shipped a slide containing a literal "
        "`«baseline»` placeholder, and several constants that were sensible but never "
        "justified. Every invariant above turns one of those failure modes from something "
        "you have to remember into something the code refuses to do."
    )

# --- quota ------------------------------------------------------------------
with tab_quota:
    st.subheader("LLM budget — free tiers only, $0.00 spent")
    st.markdown("""
| Lane | Provider / model | Role | Binding limit |
|---|---|---|---|
| **A** | Gemini 2.5 Flash-Lite | bulk classification, full corpus | 1,500 requests/day |
| **B** | Gemini 2.5 Flash | escalation where confidence < τ, one utterance per call | 1,500 requests/day |
| **C** | Groq `gpt-oss-120b` | blind second annotator, stratified sample | **200,000 tokens/day** |

The routing was decided from the token arithmetic, not from vendor speed claims.
At a batch of 20, one call is roughly 3,000 tokens — more than a third of Groq's
entire 8,000-token *per-minute* free budget, which drops the effective rate to
about two requests a minute and caps the day at ~1,300 utterances. Gemini's free
tier does twenty to forty times that. So Groq is not the bulk classifier, and
building it as one would have failed on the first afternoon.
""")

    ledger = LOGS / "llm_ledger.jsonl"
    if ledger.exists():
        rows = [json.loads(l) for l in ledger.read_text(encoding="utf-8").splitlines() if l.strip()]
        if rows:
            df = pd.DataFrame(rows)
            df["tokens"] = df.get("prompt_tokens", 0).fillna(0) + df.get("completion_tokens", 0).fillna(0)
            summary = (df.groupby(["lane", "provider", "model"])
                       .agg(calls=("outcome", "size"),
                            ok=("outcome", lambda s: (s == "ok").sum()),
                            tokens=("tokens", "sum"),
                            median_latency=("latency_s", "median"),
                            rate_limit_wait=("rate_limit_wait_s", "sum"))
                       .reset_index())
            st.dataframe(summary, use_container_width=True, hide_index=True)
            st.caption("Every call is logged: provider, model, tokens, latency, attempt, outcome, cost=0.")
    else:
        st.info("No LLM calls logged yet. `python -m src.cli quota` prints live quota state.")
