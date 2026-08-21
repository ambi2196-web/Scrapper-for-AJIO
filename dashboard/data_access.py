"""Dashboard data layer. Reads committed artifacts only - never scrapes, never calls an LLM.

Two rules this module exists to enforce at the rendering boundary:

  1. A gated row (oi is null, gate_reason set) renders as "not adjudicable",
     never as 0 and never as blank. A blank cell reads as "small"; an explicit
     gate reads as "we decided not to claim this", which is the true statement.
  2. A proportion never renders without its interval and its denominator. If the
     chart cannot show the interval, the chart does not show the proportion.
"""
from __future__ import annotations

import json
import pathlib
from typing import Any

import pandas as pd
import streamlit as st

ROOT = pathlib.Path(__file__).resolve().parent.parent
DASHBOARD_DATA = ROOT / "data" / "dashboard"
OUT = ROOT / "data" / "out"
CONFIG = ROOT / "config"

GATED_LABEL = "not adjudicable from public text"

BRAND_COLOURS = {
    "ajio": "#2F5BEA",
    "myntra": "#8B93A7",
    "nykaa_fashion": "#B8BFCF",
}
STANCE_ORDER = ["pre_purchase", "at_purchase", "post_purchase", "unclear"]


@st.cache_data(show_spinner=False)
def _read_parquet(name: str) -> pd.DataFrame:
    for base in (DASHBOARD_DATA, OUT):
        path = base / f"{name}.parquet"
        if path.exists():
            return pd.read_parquet(path)
    return pd.DataFrame()


@st.cache_data(show_spinner=False)
def manifest() -> dict[str, Any]:
    path = DASHBOARD_DATA / "manifest.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def aggregates() -> pd.DataFrame:
    return _read_parquet("aggregates")


def comparisons() -> pd.DataFrame:
    return _read_parquet("comparisons")


def evidence() -> pd.DataFrame:
    return _read_parquet("evidence")


def drop_log() -> pd.DataFrame:
    return _read_parquet("drop_log")


@st.cache_data(show_spinner=False)
def opportunity_index() -> pd.DataFrame:
    path = OUT / "opportunity_index.csv"
    return pd.read_csv(path) if path.exists() else pd.DataFrame()


@st.cache_data(show_spinner=False)
def markdown_file(name: str) -> str | None:
    path = OUT / name
    return path.read_text(encoding="utf-8") if path.exists() else None


@st.cache_data(show_spinner=False)
def taxonomy() -> dict[str, Any]:
    import yaml

    path = CONFIG / "taxonomy.yaml"
    return yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else {}


@st.cache_data(show_spinner=False)
def area_names() -> dict[str, str]:
    return {a["code"]: a.get("name", a["code"])
            for a in (taxonomy().get("opportunity_areas") or [])}


@st.cache_data(show_spinner=False)
def kappa_report() -> dict[str, Any]:
    """Latest human-vs-model kappa, if S6 has run."""
    path = ROOT / "logs" / "s6_human_vs_model.jsonl"
    if not path.exists():
        return {}
    lines = [l for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
    return json.loads(lines[-1]) if lines else {}


# --------------------------------------------------------------------------
# Rendering helpers - the two rules above, in code
# --------------------------------------------------------------------------

def format_proportion(row: pd.Series) -> str:
    """A proportion with its Wilson interval and denominator, or an honest gate."""
    if row.get("gate_reason"):
        return GATED_LABEL
    p = row.get("proportion")
    if p is None or pd.isna(p):
        return "—"
    lo, hi = row.get("ci_low"), row.get("ci_high")
    n = row.get("n_cell")
    if lo is None or pd.isna(lo):
        return f"{p:.1%} (n={int(n)})"
    return f"{p:.1%}  [{lo:.1%}–{hi:.1%}]  n={int(n)}"


def format_ratio(row: pd.Series) -> str:
    """A ratio never renders without its two denominators and its p."""
    ratio = row.get("ratio")
    if ratio is None or pd.isna(ratio):
        return GATED_LABEL if row.get("gate_reason") else "—"
    p = row.get("p")
    n_a, n_p = row.get("n_ajio"), row.get("n_pool")
    sig = "significant" if row.get("significant_at_alpha") else "not significant"
    return f"{ratio:.2f}×  (p={p:.3f}, {sig})  n_ajio={int(n_a)}, n_pool={int(n_p)}"


def gated_split(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Split into (reportable, gated). Gated rows are shown, never dropped."""
    if df.empty or "gate_reason" not in df.columns:
        return df, df.head(0)
    return df[df["gate_reason"].isna()], df[df["gate_reason"].notna()]


def pipeline_has_run() -> bool:
    return not aggregates().empty or not opportunity_index().empty


def empty_state(stage: str, command: str) -> None:
    """Shown when an artifact is missing. Names the exact command that makes it."""
    st.info(
        f"**No data for this view yet.**\n\n"
        f"It is produced by {stage}. Run:\n\n```bash\n{command}\n```\n\n"
        f"The dashboard reads committed artifacts only — it never scrapes and never "
        f"calls an LLM, so nothing here can be generated from the browser."
    )
