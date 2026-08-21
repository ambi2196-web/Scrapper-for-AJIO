# AJIO · Wishlist → Purchase — Opportunity Discovery Engine

Identifies, quantifies and compares opportunity areas in the AJIO
wishlist → purchase journey, from public conversation about online fashion
shopping in India.

**Sources:** Play Store · App Store · AJIO product-page Q&A and reviews · Reddit
and fashion/shopping communities · YouTube comments · complaint aggregators.

**Method in one line:** every figure carries its denominator and a Wilson
interval, denominators never mix surfaces or purchase stages, and areas the
public record cannot adjudicate get no number at all — they get a stated reason.

📊 **Dashboard:** deploy from `streamlit_app.py` (see `PLAN_DASHBOARD.md`)
📋 **Build plan:** [`PLAN_IMPLEMENTATION.md`](PLAN_IMPLEMENTATION.md)
🎨 **Dashboard plan:** [`PLAN_DASHBOARD.md`](PLAN_DASHBOARD.md)
📐 **Spec:** [`04_scraper_requirements.md`](04_scraper_requirements.md)

---

## Status

| | |
|---|---|
| Pipeline S1–S9 | ✅ implemented |
| Acceptance tests T1–T11 | ✅ 22 passing, 7 skipped pending a corpus |
| Streamlit dashboard, 6 pages | ✅ implemented, empty-state until data exists |
| Taxonomy (12 OAs), frozen as `tax_v1` | ✅ transcribed from 03 §4 |
| `wishlist_proximity` (D5) | ✅ frozen before classification, commit `406dcc7` |
| Category scope (D1), PDP ToS (D3) | ✅ settled 22 Aug 2026 |
| **Collection (S1)** | ⬜ next — Phase 2 |

Phase 0 is closed; every decision is recorded in
[`docs/decisions.md`](docs/decisions.md) with its reason and its date.

Two areas — OA-09 (cannot choose between similar options) and OA-10 (forgot /
never came back) — are **gated by detectability and will never carry a number**.
That is deliberate and is the engine's most important honesty artefact: a review
is written after an event, and not-deciding is not an event. A low number from a
blind instrument is more dangerous than no number, because it looks like
evidence.

---

## Quick start

```bash
pip install -r requirements.txt
cp .env.example .env        # fill in your free-tier keys
```

Then, in order:

```bash
python -m src.cli collect play           # S1  collect
python -m src.cli verify-raw             #     confirm raw is untouched
python -m src.cli normalise              # S2
python -m src.cli segment                # S3
python -m src.cli filter                 # S4
python -m src.cli freeze-proximity       #     I8 — before any classification
python -m src.cli classify sweep-b       # S5  derive the batch size
python -m src.cli classify a             #     bulk pass
python -m src.cli classify c             #     independent second annotator
python -m src.cli classify consolidate
python -m src.cli validate model-kappa   # S6
python -m src.cli labelling-sheet --n 200
python -m src.cli validate human-kappa
python -m src.cli quantify               # S7
python -m src.cli compare                # S8
python -m src.cli emit                   # S9  the three deck files
streamlit run streamlit_app.py
```

Check quota at any point:

```bash
python -m src.llm.router --status
```

---

## The eight invariants

These are assertions in code, not conventions in a document. Each exists because
it is a specific way a study like this goes quietly wrong.

| # | Invariant | Enforced by |
|---|---|---|
| I1 | Raw is immutable | append-only writer + SHA-256 manifest, re-verified in CI |
| I2 | One row per **utterance**, not per review | S3 raises if it emits exactly one row per review, for every review |
| I3 | Every label carries an exact-substring evidence quote | asserted at S5 write time; failures quarantine, never repair |
| I4 | No number for an area graded weak or none | gate applied *before* aggregation, so no value exists to leak |
| I5 | Denominators never mix source or stance | S7 raises if a grouping omits either key |
| I6 | No threshold without a stated source | config loader raises on an entry lacking `source:` |
| I7 | Hinglish preserved, never translated | no translation call in the codebase; a test asserts it |
| I8 | `wishlist_proximity` frozen before classification | S5 refuses to start if the file's mtime postdates its freeze |

**Why I6 and I8 are mechanical.** A previous attempt shipped a slide containing a
literal `«baseline»` placeholder, alongside several constants that were sensible
but never justified. Both are now things the code refuses to do rather than
things someone has to remember at 2 a.m.

---

## Two design choices worth knowing

**Product-page Q&A is the reference source.** Every other surface here is
retrospective — a review is written by someone who already bought, so their
account of what made them hesitate is a reconstruction. A question on a product
page is a person hesitating in public, in the present tense, before deciding. It
is the only public text that is pre-purchase *by construction* rather than by
classification, which is why it gets disproportionate build time despite being
the smallest and hardest source to collect.

**Two vendors' models as independent annotators.** Groq's free tier caps at 8,000
tokens per *minute* — a single 20-utterance batch is roughly 3,000 of them — which
makes it unusable as a bulk classifier. So Gemini does the bulk pass and Groq
runs as a blind second annotator on a stratified sample, producing genuine
inter-annotator disagreement. A second pass by the same model would measure
nothing: a model agrees with itself, and self-agreement is not evidence. The
free-tier constraint forced a better validation design than an unconstrained
build would have produced.

---

## Layout

```
config/          sources · taxonomy · proximity · thresholds · lexicon
prompts/         versioned classifier prompts (classifier_version in every row)
src/
  collect/       S1 — one module per source, all emitting one envelope
  llm/           router (rate limits, ledger, retry) · gemini · groq · schema
  normalise.py segment.py filter.py classify.py validate.py
  quantify.py compare.py emit.py cli.py
views/           Streamlit views (0 Overview - 6 Blind spots)
dashboard/       shared data-access layer for the views
data/
  raw/           immutable, gitignored
  out/           the three deck-facing files — committed
  dashboard/     aggregated snapshot the app reads — committed
logs/            drop log · LLM ledger · quarantine · sampling frames
tests/           T1–T11
```

**What is and isn't committed.** This repo is public and linked in the deck, so
`data/raw/`, `data/interim/` and `data/labelled/` are gitignored. Only the
aggregated outputs and the dashboard snapshot ship — enough to render every
figure and trace every quote, without republishing a scraped corpus. `.env` is
gitignored and a test asserts it is untracked.

---

## Ethics and terms

Public, unauthenticated pages only. `robots.txt` respected (an unreachable
`robots.txt` is treated as a disallow, not as permission). Human-rate request
timing with jitter. No logged-in scraping. Reviewer usernames are not stored —
they are not needed for any analysis here, and a public repo is a poor place for
them.

Free-tier LLM inputs may be used by providers to improve their models. The corpus
is public review text and the classification is not commercially sensitive, so
this is acceptable — but it was decided knowingly and is recorded in
`docs/decisions.md`. Nothing from primary research goes through a free tier.
