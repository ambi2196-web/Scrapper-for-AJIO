"""Page 4 — Evidence. The verbatims behind every claim, searchable.

Every quote here is an exact substring of the utterance it came from (invariant
I3), so any number elsewhere in the dashboard can be walked back to specific
sentences that a human can read and disagree with. That is the difference
between a finding and an assertion.
"""
from __future__ import annotations

import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

import streamlit as st

from dashboard import data_access as da

st.title("Evidence")
st.caption(
    "Quotes are copied character-for-character from the source text and validated as exact "
    "substrings before they enter the table. Anything that failed that check went to quarantine, "
    "not to a repaired value."
)

ev = da.evidence()
if ev.empty:
    da.empty_state("S5 + S9", "python -m src.cli classify consolidate && python -m src.cli emit")
    st.stop()

names = da.area_names()

f1, f2, f3, f4 = st.columns([2, 2, 2, 2])
areas = ["(all)"] + sorted(ev["opportunity_area"].dropna().unique())
area = f1.selectbox("Opportunity area", areas,
                    format_func=lambda c: c if c == "(all)" else f"{c} · {names.get(c, '')}".strip(" ·"))
stance = f2.selectbox("Stance", ["(all)"] + [s for s in da.STANCE_ORDER if s in set(ev["temporal_stance"])])
source = f3.selectbox("Source", ["(all)"] + sorted(ev["source"].dropna().unique()))
severity = f4.select_slider("Minimum severity", options=[1, 2, 3], value=1)

query = st.text_input("Search within quotes", placeholder="e.g. size, refund, delivery, sale, lu ya nahi")

view = ev.copy()
if area != "(all)":
    view = view[view["opportunity_area"] == area]
if stance != "(all)":
    view = view[view["temporal_stance"] == stance]
if source != "(all)":
    view = view[view["source"] == source]
if "severity" in view.columns:
    view = view[view["severity"].fillna(1) >= severity]
if query:
    view = view[view["evidence_quote"].str.contains(query, case=False, na=False)]

st.markdown(f"**{len(view):,} quotes** match. Sorted by severity, then by classifier confidence.")

if view.empty:
    st.info("Nothing matches. Widen the filters, or try a different search term.")
    st.stop()

sort_cols = [c for c in ("severity", "confidence") if c in view.columns]
view = view.sort_values(sort_cols, ascending=False)

tab_cards, tab_table = st.tabs(["Read", "Table"])

with tab_cards:
    page_size = 25
    total_pages = max(1, (len(view) + page_size - 1) // page_size)
    page = st.number_input("Page", 1, total_pages, 1, step=1) if total_pages > 1 else 1
    chunk = view.iloc[(page - 1) * page_size: page * page_size]

    for _, row in chunk.iterrows():
        sev = row.get("severity")
        badge = {3: "🔴 severity 3", 2: "🟠 severity 2", 1: "🟡 severity 1"}.get(sev, "· severity —")
        hes = " · explicit decision language" if row.get("hesitation_marker") else ""
        st.markdown(f"> {row['evidence_quote']}")
        meta = (f"{badge}{hes} · **{row.get('opportunity_area')}** "
                f"{names.get(row.get('opportunity_area'), '')} · {row.get('temporal_stance')} · "
                f"{row.get('source')} / {row.get('brand')} · {row.get('language')}")
        if row.get("url"):
            meta += f" · [source]({row['url']})"
        if row.get("escalated"):
            meta += " · re-classified on the escalation lane"
        st.caption(meta)
        st.divider()

with tab_table:
    cols = [c for c in ("evidence_quote", "opportunity_area", "temporal_stance", "severity",
                        "confidence", "hesitation_marker", "source", "brand", "language", "url")
            if c in view.columns]
    st.dataframe(
        view[cols], use_container_width=True, hide_index=True,
        column_config={
            "evidence_quote": st.column_config.TextColumn("Quote", width="large"),
            "opportunity_area": "Area",
            "temporal_stance": "Stance",
            "severity": st.column_config.NumberColumn("Sev", format="%d"),
            "confidence": st.column_config.NumberColumn("Conf", format="%.2f"),
            "hesitation_marker": st.column_config.CheckboxColumn("Hesitation"),
            "url": st.column_config.LinkColumn("Link"),
        },
    )
    st.download_button(
        "Download these quotes as CSV",
        view[cols].to_csv(index=False).encode("utf-8"),
        file_name="verbatims_filtered.csv", mime="text/csv",
    )

verbatims = da.markdown_file("verbatims.md")
if verbatims:
    with st.expander("verbatims.md — the file the deck reads"):
        st.markdown(verbatims)
