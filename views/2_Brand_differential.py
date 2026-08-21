"""Page 2 — Brand differential. AJIO vs the pooled competitors, matched surfaces only."""
from __future__ import annotations

import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

import plotly.graph_objects as go
import streamlit as st

from dashboard import data_access as da

st.title("Brand differential")
st.caption(
    "AJIO against the pooled competitor proportion, within one surface and one temporal stance. "
    "AJIO is excluded from the pool it is compared against."
)

comps = da.comparisons()
if comps.empty:
    da.empty_state("S8 (compare)", "python -m src.cli compare")
    st.stop()

names = da.area_names()

with st.expander("Why the comparison is built this way", expanded=False):
    st.markdown("""
**AJIO is excluded from its own comparison pool.** Including it would dilute the
difference toward zero by exactly the amount AJIO contributes — which makes a
real gap look smaller *and* an absent gap look like nothing, so the error is
invisible in both directions.

**Matched surfaces only.** Play-vs-Play, PDP-Q&A-vs-PDP-Q&A. There is no
defensible Play-vs-PDP comparison: an app-store review is written by someone who
already transacted and is now rating an app; a product-page question is written
by someone deciding whether to. A difference between those two measures the
surface, not the brand. `src/compare.py` raises rather than compute it.

**The ratio never travels alone.** Ratio, p-value and both denominators are one
record and are rendered together. "AJIO is 2.3× worse" reads very differently
once you can see it rests on 11 utterances against 9.
""")

c1, c2 = st.columns(2)
surfaces = sorted(comps["structurally_matched_group"].dropna().unique())
surface = c1.selectbox("Matched surface", surfaces)
stances = [s for s in da.STANCE_ORDER if s in set(comps["temporal_stance"])]
stance = c2.selectbox("Temporal stance", stances)

view = comps[
    (comps["structurally_matched_group"] == surface)
    & (comps["temporal_stance"] == stance)
].copy()

if view.empty:
    st.warning("No comparisons in this cell.")
    st.stop()

view["area_label"] = view["opportunity_area"].map(lambda c: f"{c} · {names.get(c, '')}".strip(" ·"))
reportable = view[view["gate_reason"].isna() & view["ratio"].notna()]

# --- paired bars ------------------------------------------------------------
if reportable.empty:
    st.info("Every comparison in this cell is gated.")
else:
    plot = reportable.sort_values("difference", ascending=True)
    fig = go.Figure()
    fig.add_trace(go.Bar(
        name="AJIO", y=plot["area_label"], x=plot["p_ajio"], orientation="h",
        marker_color=da.BRAND_COLOURS["ajio"],
        customdata=plot[["n_ajio"]].values,
        hovertemplate="AJIO %{x:.1%}  (n=%{customdata[0]:.0f})<extra></extra>",
    ))
    fig.add_trace(go.Bar(
        name="Competitor pool", y=plot["area_label"], x=plot["p_pool"], orientation="h",
        marker_color=da.BRAND_COLOURS["myntra"],
        customdata=plot[["n_pool"]].values,
        hovertemplate="Pool %{x:.1%}  (n=%{customdata[0]:.0f})<extra></extra>",
    ))
    fig.update_layout(
        barmode="group", height=max(360, 58 * len(plot)),
        xaxis=dict(title="share of utterances in cell", tickformat=".0%", gridcolor="#EEF1F7"),
        yaxis=dict(title=""), plot_bgcolor="white", paper_bgcolor="white",
        legend=dict(orientation="h", y=1.08, x=0),
        margin=dict(l=10, r=30, t=40, b=40), font=dict(size=13),
    )
    st.plotly_chart(fig, use_container_width=True)

    # --- significance view --------------------------------------------------
    st.subheader("Difference and significance")
    sig = plot.copy()
    sig["colour"] = sig["significant_at_alpha"].map({True: "#B91C1C", False: "#C7CBD6"})
    fig2 = go.Figure(go.Bar(
        y=sig["area_label"], x=sig["difference"], orientation="h",
        marker_color=sig["colour"],
        customdata=sig[["p", "n_ajio", "n_pool", "ratio"]].values,
        hovertemplate=(
            "<b>%{y}</b><br>difference %{x:+.1%} (AJIO − pool)<br>"
            "ratio %{customdata[3]:.2f}×<br>p = %{customdata[0]:.4f}<br>"
            "n_ajio %{customdata[1]:.0f} · n_pool %{customdata[2]:.0f}<extra></extra>"
        ),
    ))
    fig2.add_vline(x=0, line_width=1, line_color="#111827")
    fig2.update_layout(
        height=max(320, 44 * len(sig)),
        xaxis=dict(title="AJIO − pool (percentage points)", tickformat="+.0%", gridcolor="#EEF1F7"),
        yaxis=dict(title=""), plot_bgcolor="white", paper_bgcolor="white",
        margin=dict(l=10, r=30, t=20, b=40), font=dict(size=13),
    )
    st.plotly_chart(fig2, use_container_width=True)
    st.caption(
        f"Bars in red are significant at α={reportable['alpha'].iloc[0]} on a two-proportion "
        "z-test. Grey bars are differences this corpus cannot distinguish from zero — they are "
        "shown rather than dropped, because an absent difference is also a finding."
    )

# --- the record -------------------------------------------------------------
st.subheader("Full comparison record")
table = view.copy()
table["comparison"] = table.apply(da.format_ratio, axis=1)
st.dataframe(
    table[["opportunity_area", "comparison", "p_ajio", "p_pool", "n_ajio", "n_pool",
           "pool_brands", "detectability", "gate_reason"]],
    use_container_width=True, hide_index=True,
    column_config={
        "opportunity_area": "Area",
        "comparison": st.column_config.TextColumn("Ratio (p, n)", width="large"),
        "p_ajio": st.column_config.NumberColumn("AJIO", format="%.1%%"),
        "p_pool": st.column_config.NumberColumn("Pool", format="%.1%%"),
        "n_ajio": st.column_config.NumberColumn("n AJIO", format="%d"),
        "n_pool": st.column_config.NumberColumn("n pool", format="%d"),
        "pool_brands": "Pool composition",
        "gate_reason": st.column_config.TextColumn("Gate", width="medium"),
    },
)
