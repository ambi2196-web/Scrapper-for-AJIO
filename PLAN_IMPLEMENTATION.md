# Implementation plan — phase by phase

**AJIO · Wishlist → Purchase · Attempt 3**
Build plan for the discovery engine specified in `04_scraper_requirements.md`.

**Business metric:** wishlist → purchase conversion.
**What the engine has to deliver:** identify candidate opportunity areas, quantify
them *where the public record can bear a number*, and compare AJIO against
structurally matched competitors — with every figure traceable to a quote and
every refusal to produce a figure stated as such.

---

## The shape of the argument

Before the phases, the thing they build toward. The deck has to survive three
questions, and the whole architecture is arranged around them.

**"How do you know this is what stalls a wishlist, rather than just what annoys
people?"** — Answered by `temporal_stance`. A post-purchase complaint about
delivery cannot explain why an item sat unbought; a pre-purchase worry about
delivery can. Everything is grouped by stance, and product-page Q&A is the
reference source because it is pre-purchase *by construction* rather than by
classification.

**"How do you know the classifier is right?"** — Answered by two independent
reliability numbers: model-vs-model κ (two vendors' models as blind annotators)
and human-vs-model κ on a stratified hand-labelled sample. Reported per field,
because a blended κ hides the case where stance is the weak one.

**"Why should I believe this number?"** — Answered by never showing a proportion
without its Wilson interval and its denominator, never mixing denominators
across sources or stances, and refusing a number entirely where the text cannot
adjudicate.

Every phase below exists to make one of those three answers available.

---

## Phase 0 — Unblock (½ day) · ✅ **complete, 22 Aug 2026**

All four blockers cleared. `03_engine_spec.md` was located in
`F:\PM Course Case studies\Attempt 3\` and is now committed to the repo.

| Item | Resolution |
|---|---|
| **Taxonomy** | Transcribed from 03 §4. 12 areas, frozen as `tax_v1`. OA-09 (closure) and OA-10 (forgetting) gated by detectability. Locked by `test_taxonomy_matches_the_spec`. |
| **D5 — `wishlist_proximity`** | Settled and **frozen before any classification ran**, commit `406dcc7`. Principle: proximity grades how much of an area's harm is created or amplified by the time gap a wishlist introduces. |
| **D1 — category scope** | BROAD, 8 categories. Narrowed once, here. 128 strata × 8 products ≈ 1,024 PDPs. |
| **D3 — PDP ToS** | Public-only, robots honoured, **PDP cut outright if robots disallows** — enforced by `robots_preflight()` so a disallow cannot be routed around via Playwright. |

**Two gaps this phase surfaced in the Phase 1 build, both now fixed:**

- **`addressability` was missing from the OI formula.** 03 §5.2 specifies four
  factors; the build had three. It is now a per-area hard 0/1 gate with a
  required rationale, multiplying into the index. All twelve areas score 1 —
  OA-07 (refund delay) was the closest call and is explicitly not gated, because
  paying a refund already owed is a process fix, not an incentive.
- **The index had no named reference source.** 03 §5.1 forbids mixing sources in
  a denominator, which leaves OI undefined unless one is named. PDP Q&A is now
  the declared reference; every other source is flagged `is_reference_cell:
  false` and reported as a robustness check.

**Exit criteria — all met**
- `python -c "from src.config import load_taxonomy; load_taxonomy()"` returns ✅
- `python -m src.cli freeze-proximity` succeeded and is committed ✅
- `docs/decisions.md` records D1, D3, D5 with dates ✅
- `pytest` green, including five new taxonomy-fidelity tests ✅

---

## Phase 1 — Foundations (½ day) · ✅ **complete**

The router first, because everything downstream depends on it and it is the part
that fails silently.

Shipped:
- Config layer with I6/I8 enforcement (`src/config.py`)
- Immutable raw store with SHA-256 manifest (`src/envelope.py`)
- Router: persistent token buckets, RPM/TPM/RPD/TPD, ledger replay on boot,
  full-jitter backoff, `Retry-After`, deferred queue (`src/llm/router.py`)
- Acceptance tests T1–T11 (`tests/test_acceptance.py`) — **22 passing, 7 skipped
  pending a corpus**

**Verify:** `python -m pytest tests -q` · `python -m src.llm.router --status`

---

## Phase 2 — Collection (1 day)

Order is by value-at-risk, not by ease.

### 2a · Google Play — 3 brands (~2h)
`python -m src.cli collect play`

Both `lang='en'` and `lang='hi'`; they return partly different slices and the
Hindi slice is where the Hinglish lives. **Verify each package id on the live
listing first** — a wrong id returns `[]` rather than raising, which looks
exactly like "this brand has no reviews". The collector asserts `emitted > 0` to
turn that into a stop.

Caps must be **equal across brands**. An unequal cap contaminates the
differential: the deeper slice reaches further back in the review timeline, into
a different app version and a different pricing regime, so the two proportions
would not be measuring the same period. T10 checks this.

### 2b · PDP Q&A — the priority source (3–4h, highest risk)
`python -m src.cli collect pdp_qa`

Follow `docs/pdp_capture.md`. **Hard time-box: if the XHR capture is not working
within 3 hours, switch to the Playwright fallback immediately.** The source
matters far more than the method of getting it, and the fallback is 5–10× slower
but never wrong.

Sampling is a methodology decision, not a scraping detail. Do **not** scrape the
homepage: homepage placement correlates with promotion, promotion correlates with
price and stock behaviour, and stock behaviour is one of the things being
measured — so a homepage sample would build the finding into the sampling frame.
Stratify across the rating and review-count distributions instead, and record the
frame to `logs/pdp_sampling_frame.jsonl` as an appendix table.

**If time collapses: take Q&A, skip PDP reviews.**

### 2c · App Store (~30m)
Low volume by construction. Its value is as a second structurally-matched
surface: if a differential appears on Play and vanishes on the App Store, that is
information about the instrument, worth knowing before it reaches a slide.

### 2d · Reddit, YouTube, complaint sites (~2h, cuttable)
All `denominator_eligible: false`. Mechanism, severity and verbatims only. Reddit
is the only surface where people narrate the part of the decision where they
*didn't* buy, which makes it the best place to discover a mechanism and the worst
place to size one.

**Exit criteria**
- `python -m src.cli verify-raw` clean
- Per-brand Play counts within 10% of each other
- PDP sampling frame written and inspectable

---

## Phase 3 — Preparation (½ day)

```bash
python -m src.cli normalise   # S2
python -m src.cli segment     # S3
python -m src.cli filter      # S4
```

**S2** does three things it must not do differently: no translation (I7 —
translating "lu ya nahi" into "should I buy it" produces a grammatical equivalent
that has lost the deferral signal, because nobody writes the first while
confident), no lowercasing of stored text, and no stripping of repeated
characters — runs are capped at three so `soooo` and `sooooooo` dedupe, but the
emphasis survives as a severity signal.

**S3** produces one row per *utterance*, not per review (I2). This is what lets
the engine say "X% of pre-purchase utterances mention sizing" rather than "X% of
reviews", which conflates a review entirely about sizing with one that mentions
it in passing among four other complaints.

**S4**'s drop log is an output. "38% of Play Store reviews carry no text at all"
is a real finding about the instrument, and it pre-empts the question of why n is
smaller than the app's advertised review count.

**Then freeze proximity and commit** — before any classification runs.

**Exit criteria:** utterances-per-review > 1.0 · drop log populated ·
`proximity.yaml` frozen and committed

---

## Phase 4 — Classification (1 day) · **start early, RPD is a daily wall**

### 4a · Derive B before the bulk run (~1h)
```bash
python -m src.cli classify sweep-b
```
Sweep B ∈ {5,10,20,40} against a fixed 100-utterance gold subset. Take the
largest B whose accuracy is statistically indistinguishable from B=5. Throughput
rises monotonically with B, so quality is the only limit and measuring it is the
only honest way to set it. Write the result into `thresholds.yaml`.

### 4b · Lane A — bulk (~4h wall-clock)
```bash
python -m src.cli classify a
```
Gemini 2.5 Flash-Lite over the full corpus. **Start in the morning.** RPD is a
daily wall and hitting it at 4 p.m. costs the day. Resumable by construction —
re-running after an interrupt skips completed ids and makes zero calls for data
already labelled.

### 4c · Lane C — the blind second annotator (~2h)
```bash
python -m src.cli classify c
```
Groq `gpt-oss-120b`, stratified sample, **never shown lane A's labels**.

This is the method choice worth a line in the deck. Groq's free tier caps at
8,000 tokens per *minute* — one B=20 batch is roughly 3,000 of them — and 200,000
per day, which makes it unusable as a bulk classifier. Used instead as an
independent annotator from a different vendor, it produces genuine
inter-annotator disagreement. A second pass by the same model would measure
nothing: a model agrees with itself, and self-agreement is not evidence.

*The constraint produced a better validation design than an unconstrained build
would have. That reads as method maturity rather than thrift, and it is worth
saying out loud.*

### 4d · Consolidate
```bash
python -m src.cli classify consolidate
```
Lane B supersedes lane A where it ran. Lane C stays in separate columns — it is
an independent annotator, not a correction, and merging it would destroy the
disagreement signal S6 needs.

**Exit criteria:** `utterances.parquet` exists · quarantine rate under ~5% ·
`--status` shows quota headroom

---

## Phase 5 — Validation (1 day) · **cut sources before you cut this**

```bash
python -m src.cli validate model-kappa
python -m src.cli labelling-sheet --n <derived>
# hand-label the blind sheet → data/gold/human_labels.csv
python -m src.cli validate sweep-tau
python -m src.cli validate human-kappa
```

**Derive n first**, don't pick 100: large enough that the Wilson interval on the
smallest area you intend to report is narrower than the smallest differential you
intend to claim. Three lines of arithmetic, and they go in the appendix.

**The sheet is blind** — raw text and span, nothing else. If the labeller can see
the model's guess they are an approver, not a second annotator, and approval
rates are not κ.

**Over-sample the disagreement stratum** (where the two models disagreed is where
a human label is most informative), then **reweight to the population** before
computing κ. Unweighted, κ over an over-sampled disagreement stratum *understates*
agreement — the wrong direction of error to publish, because it invites the
reader to discount everything rather than only the weak fields.

**The gate:** below Landis & Koch's substantial band (0.61–0.80), the numbers do
not go on a slide. If a field misses, fix the prompt, bump `classifier_version`,
and **re-run the full corpus** — a corpus half-labelled by each of two prompts has
no single classifier version and no defensible count.

Hand-labelling is the scarcest resource in the build. Protect this block.

---

## Phase 6 — Quantify, compare, emit (½ day)

```bash
python -m src.cli quantify   # S7
python -m src.cli compare    # S8
python -m src.cli emit       # S9
```

**S7** applies the detectability gate *before* aggregation. An area graded weak
or none gets `oi = null` and a reason — not a computed value hidden in the
rendering layer. A number that exists in a CSV can be un-hidden by anyone who
opens it, and at 2 a.m. before submission someone will. A number never computed
cannot leak.

**S8** compares AJIO against the **pooled competitors with AJIO excluded**.
Including it would dilute the difference toward zero by exactly AJIO's own
contribution — making a real gap look smaller *and* an absent gap look like
nothing, so the error is invisible in both directions. Matched surfaces only;
`compare.py` raises on anything else.

**S9** emits exactly three files, and **the deck reads from nothing else**. Any
number in the deck that cannot be traced to a row in one of them does not go in
the deck. T11 sweeps for `«`, `TODO`, `??`, `TBD` and fails loudly — the
automated version of Attempt 2's shipped `«baseline»`.

---

## Phase 7 — Ship (½ day)

```bash
python -m pytest tests -q     # T1–T11 green
python -m src.cli sweep       # placeholder sweep clean
git add -A && git commit && git push
```
Then deploy on Streamlit Cloud — see `PLAN_DASHBOARD.md` §Deployment.

---

## Schedule

| Slot | Phase | Note |
|---|---|---|
| Day 1 PM | **0** unblock · **2a** Play | Taxonomy + D5 first; nothing else can start without them |
| Day 2 AM | **2b** PDP Q&A | Highest value, highest risk. Hard 3h time-box on the capture |
| Day 2 PM | **3** S2–S4 · freeze proximity | Freeze **before** any classification |
| Day 3 AM | **4a–4b** B-sweep, lane A | Start early — RPD is a daily wall |
| Day 3 PM | **4c** lane C · **5** hand-label | Protect the labelling block |
| Day 4 AM | **5** κ · **6** S7–S8 · 2d if the schedule held | |
| Day 4 PM | **6** emit · **7** ship | Read the three output files before calling it done |

**If you are behind:** cut Reddit, YouTube, X and the complaint sites. Keep Play
(3 brands) + PDP Q&A + full S6. That combination still supports the reference
source, the differential and the validation — which is everything the deck needs.
**Cut sources, never S6.** A validated engine on three sources beats an
unvalidated one on nine, and only the first survives questioning.

---

## Risk register

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| PDP endpoint capture fails | Medium | High — loses the reference source | Playwright fallback, hard 3h time-box |
| Taxonomy not transcribed in time | Medium | **Total** — S5 cannot run | Phase 0 is a hard stop; do it first |
| Gemini RPD hit mid-corpus | High | Low | Resumable by construction; start early |
| κ fails the gate on `temporal_stance` | Medium | High — the engine rests on this field | Per-field κ makes it visible early; prompt fix + full re-run |
| Play package id wrong | Low | Medium | Collector asserts `emitted > 0` |
| Sample too small for a claimed differential | Medium | Medium | `min_cell_n` derived; rate suppressed, count still reported |

---

## What "done" means

- [ ] `pytest` green, T1–T11
- [ ] `data/out/` has exactly the three files, placeholder sweep clean
- [ ] Every reported field's κ inside the substantial band, reported per field
- [ ] Every proportion carries a Wilson interval and a denominator
- [ ] Every gated area carries a stated reason
- [ ] Blind-spot register written and honest
- [ ] Dashboard deployed and reading committed artifacts
- [ ] `.env` absent from git history
