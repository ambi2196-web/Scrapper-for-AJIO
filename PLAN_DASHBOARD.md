# Dashboard plan

**AJIO · Wishlist → Purchase · Streamlit**
The visual layer over `data/out/` and `data/dashboard/`.

---

## What this dashboard is for

Not "showing the findings". Its job is to make a claim **inspectable in one
click** — and to make the engine's refusals as visible as its answers.

That second half is the design decision everything else follows from. A
conventional dashboard shows what was measured and quietly omits what wasn't,
which leaves the reader to assume the measured set is the whole set. This one
shows gated areas in the same tables as reported ones, with their reason
attached, because *where the public record runs out* is a finding about the
method and the method is the part being assessed.

Three rules, enforced in `dashboard/data_access.py` rather than remembered:

1. **A proportion never renders without its Wilson interval and its denominator.**
   If a chart can't show the interval, it doesn't show the proportion.
2. **A gated row renders as "not adjudicable from public text"** — never as `0`,
   never as blank. Zero and unobservable look identical on a bar chart and mean
   opposite things.
3. **A ratio never travels alone.** Ratio, p-value and both denominators render
   together. "2.3× worse" reads differently once you can see it rests on 11
   utterances against 9.

---

## Page architecture

Six pages. The narrative runs: *what did we find* → *is AJIO worse* → *does it
happen before the purchase* → *show me the words* → *why believe it* → *what
can't we see*.

### 🧭 1 · Opportunity map — `views/1_Opportunity_map.py`
**Question:** which frictions are biggest, within one comparable population?

Controls fix source × stance × brand, and the cell's shared denominator is stated
above the chart. Two charts:

- **Horizontal bars with Wilson error bars.** Sorted by prevalence. The caption
  tells the reader to read the overlap, not the bar tips — where two intervals
  overlap, this corpus cannot distinguish the areas.
- **Opportunity index bars.** `oi = proportion × mean severity × wishlist_proximity`,
  with the decomposition in the hover so the number is never a black box.

Below: the full table **including gated rows**, `oi` empty, reason attached.

*Why bars and not a treemap.* The comparison is one-dimensional magnitude with
uncertainty, and uncertainty is the point. A treemap has nowhere to put an
interval, and area is harder to compare than length.

### ⚖️ 2 · Brand differential — `views/2_Brand_differential.py`
**Question:** is AJIO worse than the alternatives, and by enough to matter?

- **Paired bars** — AJIO against the pooled competitors, per area.
- **Signed difference bars** with a zero line; significant differences in red,
  non-significant in grey.

Non-significant bars are **shown, not dropped** — an absent difference is a
finding, and a chart that silently omits them implies every area tested came
back positive.

An expander states the two rules the comparison rests on: AJIO excluded from its
own pool, matched surfaces only.

### ⏳ 3 · Stance & hesitation — `views/3_Stance_and_hesitation.py`
**Question:** does this friction happen *before* the purchase? — the page that
answers the actual business question.

- **100% stacked bars** per area, sorted by pre-purchase share. `unclear` is
  shown as its own band rather than redistributed: the classifier is instructed
  not to guess, so a wide unclear band is a statement about the text.
- **Scatter: prevalence × pre-purchase concentration**, marker size = severity.
  The upper-right quadrant is the opportunity. A common friction that is entirely
  post-purchase sits bottom-right and cannot be what stalls a wishlist. Median
  lines orient the eye and carry no decision rule — said so in the caption, so
  nobody reads them as thresholds.
- **Hesitation-marker rates**, areas with n < 5 omitted because a rate on three
  rows is noise.
- **Language mix**, with the note on why Hinglish is never translated.

### 🔍 4 · Evidence — `views/4_Evidence.py`
**Question:** show me the actual words.

Filter by area / stance / source / minimum severity, plus full-text search across
quotes. Two views: a readable card stream (blockquote + provenance line + source
link) and a sortable table with CSV export.

Every quote is an exact substring of its source utterance (invariant I3), so any
number elsewhere walks back to sentences a human can read and disagree with.
That is the difference between a finding and an assertion.

### 🔬 5 · Method & reliability — `views/5_Method_and_reliability.py`
**Question:** why should I believe any of this?

In the main navigation, not an appendix — a κ that fails its gate has to be as
visible as the finding it disqualifies. Four tabs:

- **Reliability (κ)** — grouped bars, per field, both comparisons, CI whiskers, a
  dashed floor line at 0.61 cited to Landis & Koch, and a PASS/FAIL banner.
- **Drop log** — drop counts by reason and by source. The instrument's profile.
- **Pipeline & invariants** — S1–S9 with each stage's guarantee, and the I1–I8
  table with *how each is enforced*.
- **LLM budget** — the routing table, the token arithmetic behind it, and the
  ledger summary. `$0.00`.

### 🕳️ 6 · Blind spots — `views/6_Blind_spots.py`
**Question:** what can't this method see?

Gated-by-detectability, in-taxonomy-but-absent, sources that cannot carry a rate,
and the structural limits — including the one that matters most: **people who
never write anything.** The shopper who wishlisted, forgot, and never returned
leaves no text on any surface. No amount of extra scraping reaches them; only
instrumentation or interviews do.

---

## Visual system

Restrained on purpose. The charts carry uncertainty, so the palette should not
compete with the error bars.

| | |
|---|---|
| **AJIO** | `#2F5BEA` — the only saturated colour, used for the focal brand and pre-purchase |
| **Competitors** | `#8B93A7` / `#B8BFCF` greys — present, not shouting |
| **Significant** | `#B91C1C` — used *only* for statistical significance, never decoratively |
| **Gated** | `#F59E0B` left border on an amber panel — a caution, not an error |
| **Ink / grid** | `#111827` on `#EEF1F7` |

Stance uses a single-hue ramp (deep blue → grey) rather than four categorical
colours, because stance is ordered — pre → at → post — and a categorical palette
would hide that.

Chart defaults: white plot background, horizontal bars for anything with long
labels, `.0%` tick format on proportions, hover templates that always include the
denominator.

---

## Data contract

The app reads **committed artifacts only**. It performs no scraping and makes no
LLM calls — the deployed dashboard has no network path to either, which is both a
safety property and the reason the Cloud build is fast.

```
data/dashboard/aggregates.parquet    ← S7, per (source, brand, stance, area)
data/dashboard/comparisons.parquet   ← S8
data/dashboard/evidence.parquet      ← quotes + labels, no full review bodies
data/dashboard/drop_log.parquet      ← S4
data/dashboard/manifest.json         ← generation timestamp + row counts
data/out/opportunity_index.csv       ← the deck-facing index
data/out/blind_spots.md
data/out/verbatims.md
logs/s6_*.jsonl                      ← κ reports
```

`src/emit.py::emit_dashboard_snapshot()` writes `data/dashboard/`. It carries
evidence **quotes**, never full review bodies — the dashboard needs the evidence,
not a republished corpus on a public repo.

**Missing artifacts degrade gracefully**: each page shows the exact command that
produces its data rather than a stack trace.

---

## Deployment

**1 · Push** (done — see README).

**2 · Streamlit Cloud** → [share.streamlit.io](https://share.streamlit.io) → *New app*

| Field | Value |
|---|---|
| Repository | `ambi2196-web/Scrapper-for-AJIO` |
| Branch | `main` |
| Main file | `streamlit_app.py` |

**3 · Dependencies — nothing to configure.** Streamlit Cloud looks for
`requirements.txt` at the repo root and installs it automatically, **ignoring any
other filename regardless of what the UI's dependencies-file setting says.** So
`requirements.txt` holds the five packages the dashboard needs, and the pipeline's
dependencies live in `requirements-pipeline.txt`, installed explicitly.

The first deploy failed on exactly this: the full pipeline list was in
`requirements.txt`, and `app-store-scraper` pins `requests==2.23.0` while
`google-genai` needs `>=2.28.1` — unsatisfiable. That library was already dead
code (the App Store collector uses Apple's RSS feed over httpx) and is now gone
from the pipeline file too.

**4 · Secrets: none.** Deliberately. The dashboard reads files; it has no keys to
leak. `.streamlit/secrets.toml` is gitignored anyway.

**5 · Updating.** Re-run `python -m src.cli emit`, commit `data/dashboard/` and
`data/out/`, push. Streamlit Cloud redeploys on push.

> If the Cloud UI won't let you point at a non-default requirements file, rename
> `requirements.txt` → `requirements-pipeline.txt` and
> `requirements.txt` → `requirements.txt`. The pipeline is run locally
> from an explicit file, so nothing breaks.

---

## Build order

The dashboard is written and pushed. What remains is data-dependent:

| Step | Depends on | State |
|---|---|---|
| Pages 1, 2, 6 render | Taxonomy transcribed + S7/S8 run | scaffolded, empty-state |
| Page 3 renders | S5 consolidate | scaffolded, empty-state |
| Page 4 renders | S5 + S9 | scaffolded, empty-state |
| Page 5 κ charts | S6 | scaffolded, empty-state |
| Cloud deploy | first `emit` | ready |

Every page currently shows its empty state with the command that fills it, so
deploying now is useful: it verifies the build before there is data to debug at
the same time.

**Local preview:**

```bash
streamlit run streamlit_app.py
```
