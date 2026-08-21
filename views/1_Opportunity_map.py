"""Page 1 — Opportunity map. Every area sized within its cell, with intervals."""
from __future__ import annotations

import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from dashboard import data_access as da

st.title("Opportunity map")
st.caption(
    "Each bar is one opportunity area within one source and one temporal stance. "
    "Bars are never compared across those boundaries, because the denominators are not the same population."
)

agg = da.aggregates()
if agg.empty:
    da.empty_state("S7 (quantify)", "python -m src.cli quantify")
    st.stop()

names = da.area_names()

# --- controls ---------------------------------------------------------------
c1, c2, c3 = st.columns([2, 2, 2])
sources = sorted(agg["source"].unique())
source = c1.selectbox("Source", sources, help="Proportions are only comparable within a source.")
stances = [s for s in da.STANCE_ORDER if s in set(agg["temporal_stance"])]
stance = c2.selectbox("Temporal stance", stances,
                      help="pre_purchase is the stance that speaks to a wishlist decision.")
brands = sorted(agg["brand"].unique())
brand = c3.selectbox("Brand", brands)

view = agg[(agg["source"] == source) & (agg["temporal_stance"] == stance) & (agg["brand"] == brand)].copy()
if view.empty:
    st.warning("No rows in this cell. Try another source/stance/brand combination.")
    st.stop()

view["area_label"] = view["opportunity_area"].map(lambda c: f"{c} · {names.get(c, '')}".strip(" ·"))
reportable, gated = da.gated_split(view)

n_cell = int(view["n_cell"].iloc[0])
st.markdown(
    f"**Denominator for this cell: {n_cell:,} utterances** "
    f"({source} · {brand} · {stance}). Every proportion below shares it."
)

# --- the chart --------------------------------------------------------------
if reportable.empty:
    st.info("Every area in this cell is gated. See the table below for the reasons.")
else:
    plot = reportable.sort_values("proportion", ascending=True)
    fig = go.Figure()
    fig.add_trace(go.Bar(
        y=plot["area_label"], x=plot["proportion"], orientation="h",
        marker_color=da.BRAND_COLOURS.get(brand, "#2F5BEA"),
        error_x=dict(
            type="data", symmetric=False,
            array=(plot["ci_high"] - plot["proportion"]),
            arrayminus=(plot["proportion"] - plot["ci_low"]),
            color="#111827", thickness=1.2, width=4,
        ),
        customdata=plot[["n_area", "n_cell", "ci_low", "ci_high", "severity_mean"]].values,
        hovertemplate=(
            "<b>%{y}</b><br>"
            "proportion %{x:.1%}<br>"
            "95% Wilson CI  %{customdata[2]:.1%} – %{customdata[3]:.1%}<br>"
            "%{customdata[0]:.0f} of %{customdata[1]:.0f} utterances<br>"
            "mean severity %{customdata[4]:.2f}"
            "<extra></extra>"
        ),
    ))
    fig.update_layout(
        height=max(320, 42 * len(plot)),
        xaxis=dict(title="share of utterances in this cell", tickformat=".0%", gridcolor="#EEF1F7"),
        yaxis=dict(title=""),
        plot_bgcolor="white", paper_bgcolor="white",
        margin=dict(l=10, r=30, t=20, b=40),
        font=dict(size=13),
    )
    st.plotly_chart(fig, use_container_width=True)
    st.caption(
        "Error bars are 95% Wilson score intervals. Where two bars' intervals overlap, "
        "the difference between them is not something this corpus can distinguish — "
        "read the overlap, not the bar tips."
    )

# --- opportunity index ------------------------------------------------------
st.subheader("Opportunity index")
st.markdown(
    "`oi = proportion × mean severity × wishlist_proximity`. The proximity weight is frozen "
    "and git-committed *before* any classification runs, so it cannot be tuned after seeing "
    "the results."
)

if not reportable.empty and reportable["oi"].notna().any():
    ranked = reportable.dropna(subset=["oi"]).sort_values("oi", ascending=True)
    fig2 = go.Figure(go.Bar(
        y=ranked["area_label"], x=ranked["oi"], orientation="h",
        marker_color="#111827",
        customdata=ranked[["proportion", "severity_mean", "proximity_weight", "n_cell"]].values,
        hovertemplate=(
            "<b>%{y}</b><br>oi %{x:.4f}<br>"
            "= %{customdata[0]:.1%} × %{customdata[1]:.2f} severity × %{customdata[2]:.2f} proximity<br>"
            "n=%{customdata[3]:.0f}<extra></extra>"
        ),
    ))
    fig2.update_layout(
        height=max(300, 40 * len(ranked)),
        xaxis=dict(title="opportunity index", gridcolor="#EEF1F7"),
        yaxis=dict(title=""), plot_bgcolor="white", paper_bgcolor="white",
        margin=dict(l=10, r=30, t=20, b=40), font=dict(size=13),
    )
    st.plotly_chart(fig2, use_container_width=True)
else:
    st.info(
        "No opportunity index is computable in this cell — either every area is gated, "
        "or config/proximity.yaml has no weight for the areas present."
    )

# --- the table, gated rows included ----------------------------------------
st.subheader("All areas in this cell")
table = view.copy()
table["measured"] = table.apply(da.format_proportion, axis=1)
cols = ["opportunity_area", "measured", "severity_mean", "proximity_weight", "oi",
        "detectability", "gate_reason"]
st.dataframe(
    table[[c for c in cols if c in table.columns]].sort_values(
        "oi", ascending=False, na_position="last"),
    use_container_width=True, hide_index=True,
    column_config={
        "opportunity_area": "Area",
        "measured": st.column_config.TextColumn("Proportion (95% Wilson CI, n)", width="large"),
        "severity_mean": st.column_config.NumberColumn("Mean severity", format="%.2f"),
        "proximity_weight": st.column_config.NumberColumn("Proximity", format="%.2f"),
        "oi": st.column_config.NumberColumn("Index", format="%.4f"),
        "detectability": "Detectability",
        "gate_reason": st.column_config.TextColumn("Gate", width="medium"),
    },
)

if len(gated):
    st.markdown(
        f'<div class="gate">{len(gated)} of {len(view)} areas in this cell are gated. '
        f'They are listed above with <code>oi</code> empty and a reason attached — the value '
        f'was never computed, so there is nothing to un-hide.</div>',
        unsafe_allow_html=True,
    )
