"""AJIO Wishlist→Purchase — Opportunity Discovery Engine. Streamlit entry point.

Streamlit Cloud looks for this file at the repo root. It owns page config and
the shared stylesheet, then hands off to `st.navigation`; the views live in
`views/` and are listed explicitly so each gets a real title and icon rather
than one derived from a filename.

Design premise: this dashboard's job is not to look impressive, it is to make a
claim inspectable in one click. Every number can be traced to the utterance
behind it, and every number the engine refused to compute is shown as a refusal
rather than hidden. A dashboard that shows only the findings hides the method,
and the method is the part being assessed.
"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import streamlit as st

st.set_page_config(
    page_title="AJIO · Wishlist→Purchase Opportunity Engine",
    page_icon="🧭",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Shared stylesheet. Defined once here so every view inherits it - the `kpi` and
# `gate` classes are used across several of them.
st.markdown("""
<style>
  .block-container {padding-top: 2.2rem; max-width: 1400px;}
  h1 {font-size: 1.9rem; letter-spacing: -0.02em;}
  .kpi {background:#F4F6FB; border:1px solid #E4E8F2; border-radius:12px; padding:1rem 1.15rem;}
  .kpi .v {font-size:1.75rem; font-weight:650; letter-spacing:-0.02em; color:#111827;}
  .kpi .l {font-size:.75rem; text-transform:uppercase; letter-spacing:.06em; color:#6B7280;}
  .kpi .s {font-size:.78rem; color:#6B7280; margin-top:.25rem;}
  .gate {background:#FFF7ED; border-left:3px solid #F59E0B; padding:.6rem .9rem;
         border-radius:6px; font-size:.85rem; color:#7C2D12;}
  .pass {color:#047857; font-weight:600;}
  .fail {color:#B91C1C; font-weight:600;}
</style>
""", unsafe_allow_html=True)

VIEWS = "views"

navigation = st.navigation([
    st.Page(f"{VIEWS}/0_Overview.py", title="Overview", icon="🧭", default=True),
    st.Page(f"{VIEWS}/1_Opportunity_map.py", title="Opportunity map", icon="📊"),
    st.Page(f"{VIEWS}/2_Brand_differential.py", title="Brand differential", icon="⚖️"),
    st.Page(f"{VIEWS}/3_Stance_and_hesitation.py", title="Stance & hesitation", icon="⏳"),
    st.Page(f"{VIEWS}/4_Evidence.py", title="Evidence", icon="🔍"),
    st.Page(f"{VIEWS}/5_Method_and_reliability.py", title="Method & reliability", icon="🔬"),
    st.Page(f"{VIEWS}/6_Blind_spots.py", title="Blind spots", icon="🕳️"),
])

navigation.run()
