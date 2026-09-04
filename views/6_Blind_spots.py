"""Page 6 — Blind spots. What this method cannot see, and why.

This page is not a disclaimer. An area the public record cannot adjudicate is a
finding: it marks where secondary research runs out and where primary research
would have to begin. Saying so plainly is what separates "we measured what we
could" from the implicit and much weaker claim that what was measured is all
there was.
"""
from __future__ import annotations

import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

import pandas as pd
import streamlit as st

from dashboard import data_access as da

st.title("Blind spots")
st.caption("Where the public record runs out — and what it would take to see past it.")

agg = da.aggregates()
tax = da.taxonomy()
names = da.area_names()

st.subheader("Why a gate is better than a small number")
st.markdown("""
When an opportunity area cannot be adjudicated from public text, this engine
returns `null` and a reason — it does not return a small number.

The distinction matters more than it looks. A number that was computed and then
hidden in the presentation layer can be un-hidden by anyone who opens the CSV,
and at 2 a.m. before a submission, someone will. A number that was **never
computed** cannot leak. So the detectability gate runs inside S7, before
aggregation, rather than as a filter in the deck.

It also protects against the more common failure: reporting `0%` for something
the method simply could not observe. Zero and unobservable look identical on a
bar chart and mean opposite things.
""")

# --- gated by detectability -------------------------------------------------
st.subheader("1 · Gated by detectability")
areas = tax.get("opportunity_areas") or []
gated_areas = [a for a in areas if a.get("detectability") in ("weak", "none")]

if not areas:
    st.warning(
        "`config/taxonomy.yaml` is still a stub — the 12 opportunity areas and their "
        "detectability grades have not been transcribed from `03_engine_spec.md`. "
        "The loader raises rather than let the pipeline invent them."
    )
elif gated_areas:
    st.dataframe(
        pd.DataFrame([{
            "Area": a["code"], "Name": a.get("name"),
            "Grade": a.get("detectability"),
            "Why public text cannot adjudicate it": a.get("detectability_rationale", "—"),
        } for a in gated_areas]),
        use_container_width=True, hide_index=True,
    )
else:
    st.success("No area in the frozen taxonomy is graded weak or none.")

# --- in taxonomy, absent from corpus ---------------------------------------
st.subheader("2 · In the taxonomy, absent from the corpus")
st.markdown(
    "Adjudicable in principle, but no utterance was classified into them. "
    "**Absence of evidence is weak evidence of absence here** — these are candidates for "
    "primary research, not for a zero on a chart."
)
if not agg.empty and areas:
    observed = set(agg["opportunity_area"])
    missing = [a for a in areas
               if a["code"] not in observed and a.get("detectability") in ("strong", "moderate")]
    if missing:
        st.dataframe(
            pd.DataFrame([{"Area": a["code"], "Name": a.get("name"),
                           "Grade": a.get("detectability")} for a in missing]),
            use_container_width=True, hide_index=True,
        )
    else:
        st.success("Every adjudicable area in the taxonomy is represented in the corpus.")
else:
    st.info("Run S7 to populate this section.")

# --- sources that cannot carry a rate --------------------------------------
st.subheader("3 · Sources that cannot carry a rate")
st.markdown("""
Reddit, YouTube, the complaint aggregators and X are marked
`denominator_eligible: false` in `config/sources.yaml`, and S7 refuses them a
denominator in code rather than by convention.

The reason is structural rather than sizeable. People arrive at a complaint site
*because* they have a complaint, so the base rate of complaint there is
definitionally near 1 — any proportion computed from it is a statement about
what the venue is for, not about the brand. That is not a bias you can estimate
and correct; it is a bias that makes the quantity meaningless.

What those sources are genuinely good for is the **severity-3 tail** and the
mechanism. People saying they will never use the service again are rare in
app-store reviews and common on complaint sites, and Reddit is the only surface
where anyone narrates the part of the decision where they didn't buy. Those
utterances become verbatims and hypotheses. They never become percentages.
""")

if not agg.empty:
    counts = agg.groupby("source")["n_area"].sum().reset_index(name="utterances")
    counts["carries a rate"] = counts["source"].isin(
        {"play", "appstore", "pdp_qa", "pdp_reviews"}
    ).map({True: "yes", False: "no — verbatims and mechanism only"})
    st.dataframe(counts, use_container_width=True, hide_index=True)

# --- structural limits ------------------------------------------------------
st.subheader("4 · Severity is not reliably adjudicable from short review text")
_mm_obs = da.stat("model", "severity", "observed_agreement")
if _mm_obs is None:
    st.info("Severity reliability has not been computed yet — run `validate model-kappa`.")
else:
    st.markdown(f"""
Measured, not assumed. Two independent models — Gemini 3.5 Flash-Lite and Groq
`gpt-oss-120b`, sharing no prompt and no lineage — agree on only **{_mm_obs:.0%} of
severity judgements** (AC1 {da.fmt_stat("model", "severity", "ac1")}, κ
{da.fmt_stat("model", "severity", "kappa")}).

A field that two independent readers cannot agree on is not a miscalibrated
classifier that a better prompt would fix. It is a field the evidence does not
determine: a short review says *what* went wrong far more reliably than it says
whether the speaker carried on anyway, changed what they did, or left for good.

**The human comparison does not corroborate this as strongly**, and the weaker
claim is the one that gets stated. Against the hand-labelled sample severity
reaches AC1 {da.fmt_stat("human", "severity", "ac1")} (κ
{da.fmt_stat("human", "severity", "kappa")}) — better than the two models manage
with each other. So the finding rests on the model-vs-model disagreement alone,
on n={da.stat("human", "severity", "n")} human-labelled rows, which is too few to
settle it either way.

The consequence is recorded rather than worked around. Where severity enters the
opportunity index it is flagged, and the reliability figures travel with it.
""")

st.subheader("5 · Deliberation leaves no trace on these surfaces")
st.markdown("""
The engine set out to detect **explicit decision language** — someone weighing,
deferring or abandoning a purchase in their own words. It found almost none, and
what it did flag did not survive checking.

The classifier marked `hesitation_marker` on 16.8% of the corpus. A human read a
100-utterance sample and confirmed **none of the 16** flagged there. Inspecting
them shows why: *"Don't ever buy from this app"*, *"Not gonna trust these
people"*, *"Would not recommend"* — warnings to other shoppers and statements of
future refusal, not deliberation. The classifier was detecting negative sentiment
and calling it hesitation.

So no hesitation rate is reported. **The absence is the finding**: people write
reviews *after* something happens to them, and the moment of weighing a purchase
happens before, in private. It is the same blindness that gates OA-09 and OA-10,
reached by a different route — and it is the clearest single argument for why
interviews are necessary rather than merely complementary.
""")

st.subheader("6 · Limits of the method as a whole")
st.markdown("""
**People who never say anything.** Every source here is a record of someone who
chose to write. The shopper who added an item to a wishlist, forgot about it and
never returned leaves no text anywhere. That is plausibly the largest single
group in the metric, and it is invisible to this entire method. No amount of
additional scraping reaches them — only instrumentation or interviews do.

**Stance is inferred, not observed — except on product Q&A.** Everywhere else,
`temporal_stance` is a classifier's reading of the wording. Q&A is the one
surface where pre-purchase is guaranteed by the surface itself, which is why it
is the reference source despite being the smallest and hardest to collect.

**Public text over-represents the extremes.** Mild friction that changed nothing
rarely gets written down. Severity 1 is almost certainly under-counted relative
to its true prevalence, so severity means are conservative in a known direction.

**Timing is patchy.** Several sources return no reliable `posted_at`, so trend
claims are gated by observed null rates rather than attempted everywhere.

**A wishlist is not observable from outside.** The engine measures frictions that
*could* stall a wishlist→purchase transition, and their proximity to that moment
is a frozen judgement recorded in `config/proximity.yaml` — not a measurement.
It is committed before classification runs specifically so it cannot be tuned
once the results are visible.
""")

blind = da.markdown_file("blind_spots.md")
if blind:
    with st.expander("blind_spots.md — the file the deck reads"):
        st.markdown(blind)
