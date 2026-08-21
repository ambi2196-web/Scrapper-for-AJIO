"""Page 3 — Stance & hesitation. Where in the journey each friction appears.

This is the page that answers the actual business question. An area with a high
overall prevalence but a post-purchase-only stance profile cannot be what keeps
items sitting in a wishlist — the speaker had already bought. An area that is
smaller overall but concentrated in pre-purchase utterances is a much better
candidate for the metric, and the stance split is the only way to see that.
"""
from __future__ import annotations

import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from dashboard import data_access as da

st.title("Stance & hesitation")
st.caption(
    "The same friction, split by where the speaker stood in the purchase. "
    "Only pre-purchase utterances can explain why something stays in a wishlist."
)

agg = da.aggregates()
ev = da.evidence()
if agg.empty:
    da.empty_state("S7 (quantify)", "python -m src.cli quantify")
    st.stop()

names = da.area_names()

c1, c2 = st.columns(2)
source = c1.selectbox("Source", sorted(agg["source"].unique()))
brand = c2.selectbox("Brand", sorted(agg["brand"].unique()))

view = agg[(agg["source"] == source) & (agg["brand"] == brand)].copy()
view = view[view["gate_reason"].isna()]
if view.empty:
    st.warning("No ungated rows for this source/brand.")
    st.stop()

view["area_label"] = view["opportunity_area"].map(lambda c: f"{c} · {names.get(c, '')}".strip(" ·"))

# --- stance composition per area -------------------------------------------
st.subheader("Stance composition, per area")
st.markdown(
    "Each row sums to 100% of that area's utterances. **Read the left-hand share**: "
    "it is the fraction of this friction that was voiced by someone who had not yet bought."
)

pivot = view.pivot_table(
    index="area_label", columns="temporal_stance", values="n_area", aggfunc="sum"
).fillna(0)
for stance in da.STANCE_ORDER:
    if stance not in pivot.columns:
        pivot[stance] = 0
pivot = pivot[da.STANCE_ORDER]
shares = pivot.div(pivot.sum(axis=1).replace(0, 1), axis=0)
shares = shares.sort_values("pre_purchase", ascending=True)

stance_colours = {
    "pre_purchase": "#2F5BEA",
    "at_purchase": "#7C9BF2",
    "post_purchase": "#C7CBD6",
    "unclear": "#EEF1F7",
}
fig = go.Figure()
for stance in da.STANCE_ORDER:
    fig.add_trace(go.Bar(
        name=stance, y=shares.index, x=shares[stance], orientation="h",
        marker_color=stance_colours[stance],
        customdata=pivot.loc[shares.index, stance].values.reshape(-1, 1),
        hovertemplate=f"<b>%{{y}}</b><br>{stance}: %{{x:.0%}} (%{{customdata[0]:.0f}} utterances)<extra></extra>",
    ))
fig.update_layout(
    barmode="stack", height=max(340, 40 * len(shares)),
    xaxis=dict(title="", tickformat=".0%", gridcolor="#EEF1F7", range=[0, 1]),
    yaxis=dict(title=""), plot_bgcolor="white", paper_bgcolor="white",
    legend=dict(orientation="h", y=1.08, x=0),
    margin=dict(l=10, r=30, t=40, b=30), font=dict(size=13),
)
st.plotly_chart(fig, use_container_width=True)
st.caption(
    "`unclear` is a correct classification, not a failure — the prompt instructs the "
    "classifier not to guess. A wide unclear band is a statement about the text, and it is "
    "shown rather than redistributed."
)

# --- prevalence vs pre-purchase concentration -------------------------------
st.subheader("Prevalence against pre-purchase concentration")
st.markdown(
    "The upper-right quadrant is where the opportunity is: frictions that are both common "
    "**and** voiced before the purchase. A common friction that is entirely post-purchase "
    "sits bottom-right and cannot be what stalls a wishlist."
)

totals = pivot.sum(axis=1)
scatter = pd.DataFrame({
    "area": shares.index,
    "utterances": totals.loc[shares.index].values,
    "pre_share": shares["pre_purchase"].values,
})
sev = view.groupby("area_label")["severity_mean"].mean()
scatter["severity"] = scatter["area"].map(sev).fillna(1.0)

fig2 = go.Figure(go.Scatter(
    x=scatter["utterances"], y=scatter["pre_share"], mode="markers+text",
    text=scatter["area"].str.slice(0, 6), textposition="top center",
    marker=dict(
        size=(scatter["severity"] * 14).clip(10, 44),
        color=scatter["pre_share"], colorscale=[[0, "#C7CBD6"], [1, "#2F5BEA"]],
        line=dict(width=1, color="#FFFFFF"), showscale=False,
    ),
    customdata=scatter[["area", "severity"]].values,
    hovertemplate=("<b>%{customdata[0]}</b><br>%{x:.0f} utterances<br>"
                   "%{y:.0%} pre-purchase<br>mean severity %{customdata[1]:.2f}<extra></extra>"),
))
fig2.add_hline(y=float(scatter["pre_share"].median()), line_dash="dot", line_color="#9CA3AF")
fig2.add_vline(x=float(scatter["utterances"].median()), line_dash="dot", line_color="#9CA3AF")
fig2.update_layout(
    height=520,
    xaxis=dict(title="utterances in this area", gridcolor="#EEF1F7"),
    yaxis=dict(title="share voiced pre-purchase", tickformat=".0%", gridcolor="#EEF1F7"),
    plot_bgcolor="white", paper_bgcolor="white",
    margin=dict(l=10, r=30, t=20, b=40), font=dict(size=13),
)
st.plotly_chart(fig2, use_container_width=True)
st.caption("Marker size is mean severity. Dotted lines are medians, not thresholds — "
           "they orient the eye and carry no decision rule.")

# --- hesitation markers -----------------------------------------------------
if not ev.empty and "hesitation_marker" in ev.columns:
    st.subheader("Explicit decision language")
    st.markdown(
        "`hesitation_marker` is set only where the speaker is visibly weighing, deferring or "
        "abandoning a purchase in the text itself. It is a stricter signal than stance: stance "
        "says *when* they spoke, this says they were **audibly undecided while speaking**."
    )
    sub = ev[(ev["source"] == source) & (ev["brand"] == brand)]
    if not sub.empty:
        hes = (sub.groupby("opportunity_area")["hesitation_marker"]
               .agg(["mean", "sum", "count"]).reset_index()
               .rename(columns={"mean": "rate", "sum": "n_marked", "count": "n"}))
        hes = hes[hes["n"] >= 5].sort_values("rate", ascending=True)
        if not hes.empty:
            hes["label"] = hes["opportunity_area"].map(lambda c: f"{c} · {names.get(c, '')}".strip(" ·"))
            fig3 = go.Figure(go.Bar(
                y=hes["label"], x=hes["rate"], orientation="h", marker_color="#111827",
                customdata=hes[["n_marked", "n"]].values,
                hovertemplate=("<b>%{y}</b><br>%{x:.0%} carry explicit decision language<br>"
                               "%{customdata[0]:.0f} of %{customdata[1]:.0f}<extra></extra>"),
            ))
            fig3.update_layout(
                height=max(280, 38 * len(hes)),
                xaxis=dict(title="share with an explicit hesitation marker",
                           tickformat=".0%", gridcolor="#EEF1F7"),
                yaxis=dict(title=""), plot_bgcolor="white", paper_bgcolor="white",
                margin=dict(l=10, r=30, t=20, b=40), font=dict(size=13),
            )
            st.plotly_chart(fig3, use_container_width=True)
            st.caption("Areas with fewer than 5 utterances are omitted — a rate on 3 rows is noise.")

    lang = ev[(ev["source"] == source)]
    if not lang.empty and "language" in lang.columns:
        with st.expander("Language mix — why Hinglish is never translated"):
            counts = lang["language"].value_counts()
            st.bar_chart(counts)
            st.markdown(
                "Hinglish is preserved exactly as written. Translating it would normalise "
                "phrasing like *\"lu ya nahi\"* into *\"should I buy it\"* — grammatically "
                "equivalent, but the first is a much stronger deferral signal, because "
                "nobody writes it while confident. No translate call exists anywhere in the "
                "codebase, and a test asserts that."
            )
