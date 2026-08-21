"""Overview — the landing view.

Sets up the argument the other pages support: what was measured, on which
surface, at which point in the purchase, and what the engine refused to measure.
"""
from __future__ import annotations

import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import streamlit as st

from dashboard import data_access as da


def kpi(col, label: str, value: str, sub: str = "") -> None:
    col.markdown(
        f'<div class="kpi"><div class="l">{label}</div>'
        f'<div class="v">{value}</div><div class="s">{sub}</div></div>',
        unsafe_allow_html=True,
    )


st.title("Where the wishlist stalls")
st.caption(
    "Opportunity areas in the AJIO wishlist→purchase journey, discovered and sized from "
    "public conversation. Every figure carries its denominator and its interval; every "
    "area the public record cannot adjudicate is shown as a refusal, not a blank."
)

agg = da.aggregates()
comps = da.comparisons()
ev = da.evidence()
kappa = da.kappa_report()

if not da.pipeline_has_run():
    st.warning("**The pipeline has not produced artifacts yet.** This is a fresh clone.")
    st.markdown("""
Run the stages in order, then redeploy:

```bash
python -m src.cli collect play          # S1
python -m src.cli normalise             # S2
python -m src.cli segment                # S3
python -m src.cli filter                 # S4
python -m src.cli freeze-proximity       # I8 — before any classification
python -m src.cli classify a             # S5 lane A
python -m src.cli classify c             # S5 lane C (independent annotator)
python -m src.cli classify consolidate
python -m src.cli validate model-kappa   # S6
python -m src.cli quantify               # S7
python -m src.cli compare                # S8
python -m src.cli emit                   # S9 — writes the three deck files
```

`emit` also writes `data/dashboard/`, which is what this app reads.
""")
    st.stop()

# --- headline row -----------------------------------------------------------
c1, c2, c3, c4 = st.columns(4)

reportable, gated = da.gated_split(agg)
n_utterances = int(agg["n_area"].sum()) if not agg.empty else 0
n_pre = int(agg[agg["temporal_stance"] == "pre_purchase"]["n_area"].sum()) if not agg.empty else 0

kpi(c1, "Utterances classified", f"{n_utterances:,}",
    f"{len(agg['source'].unique())} sources · one row per utterance, not per review")
kpi(c2, "Pre-purchase utterances", f"{n_pre:,}",
    f"{n_pre / n_utterances:.0%} of corpus — the only stance that speaks to a wishlist decision"
    if n_utterances else "")

if kappa and kappa.get("gate"):
    cls = "pass" if kappa["gate"] == "PASS" else "fail"
    worst = min(
        (r.get("kappa") for r in kappa.get("per_field", {}).values() if r.get("kappa") is not None),
        default=None,
    )
    kpi(c3, "Reliability gate",
        f'<span class="{cls}">{kappa["gate"]}</span>',
        f"weakest field κ={worst:.2f} · floor {kappa.get('kappa_floor')} (Landis & Koch)"
        if worst is not None else "")
else:
    kpi(c3, "Reliability gate", "not yet run",
        "S6 has not produced a human-vs-model κ")

kpi(c4, "Areas gated", f"{len(gated)}",
    "carried as null with a reason — never as a computed value")

st.divider()

# --- what this is / how to read it -----------------------------------------
left, right = st.columns([3, 2])

with left:
    st.subheader("How to read this")
    st.markdown("""
**Three things constrain every number here, by construction rather than by convention.**

**Denominators never mix.** A proportion is always *within* one source and one
`temporal_stance`. "12% mention sizing" is not a fact until you say 12% of which
surface and at which point in the purchase — a pre-purchase sizing *question* and
a post-purchase sizing *complaint* are different phenomena that share a word.

**Not every source can carry a rate.** Reddit, YouTube and the complaint
aggregators are marked `denominator_eligible: false`. People arrive at a
complaint site because they have a complaint, so a rate computed there describes
the venue, not the brand. Those sources supply mechanism and verbatims; the
pipeline refuses them a denominator in code.

**Some areas get no number at all.** Where public text cannot adjudicate an area,
the opportunity index is `null` with a stated reason. The gate runs *before*
aggregation, so no value exists that could later leak into a slide.
""")

with right:
    st.subheader("Reference source")
    st.markdown("""
Product-page **Q&A is the reference surface** for the opportunity index, because
it is the only public text that is pre-purchase *by construction* rather than by
classification.

A review is written by someone who already bought; their account of what made
them hesitate is a reconstruction. A question on a product page is a person
hesitating in public, in the present tense, before deciding — which is the
closest textual proxy to a wishlist decision that exists in public data.

App-store reviews carry the volume and the brand-vs-brand differential. Q&A
carries the construct validity.
""")

if len(gated):
    st.markdown(
        f'<div class="gate"><strong>{len(gated)} rows are gated.</strong> '
        f'They appear throughout with their reason attached. A gated row is a finding '
        f'about where the public record runs out — see the Blind spots page.</div>',
        unsafe_allow_html=True,
    )

st.divider()

with st.sidebar:
    st.markdown("### Run")
    mf = da.manifest()
    if mf:
        st.caption(f"Artifacts generated {mf.get('generated_at', 'unknown')}")
        for name, count in (mf.get("files") or {}).items():
            st.caption(f"· {name}: {count:,} rows")
    st.caption(
        "This app reads committed artifacts only. It performs no scraping and makes "
        "no LLM calls — the deployed dashboard has no network path to either."
    )
