# 04 — Scraper & Classification Requirements

**AJIO · Wishlist → Purchase · Attempt 3**
Implementation spec for the AI Discovery Engine, stages S1–S9. Written to be handed directly to a coding agent (Claude Code / Cursor) or executed by hand.

**Reads with:** `03_engine_spec.md` (the *what* and the *why*). This file is the *how*. Where the two disagree, `03_engine_spec.md` wins — it is the design contract; this file is the build.

**LLM providers: Groq free tier + Gemini free tier only.** No paid inference. §4 explains why that constraint produces a *better* method rather than a compromised one.

---

## 0. Non-negotiable invariants

Any implementation that violates one of these is wrong regardless of how well it runs. A coding agent should treat these as assertions, not preferences.

| # | Invariant | Enforced by |
|---|---|---|
| I1 | **Raw is immutable.** S1 output is written once and never modified, re-written, or deleted by any later stage. | `data/raw/` opened append-only; checksum manifest written at S1 close |
| I2 | **One row per utterance, not per review.** A review with three signals produces three rows. | S3 emits ≥1 row per input review, never exactly 1 by construction |
| I3 | **Every classification carries `evidence_quote`, an exact substring of `raw_text`.** | Assert `evidence_quote in raw_text` at S5 write time; failures go to a quarantine file, not to the table |
| I4 | **No number is produced for a phenomenon graded Weak or None in the detectability table.** | `detectability` gate applied in S7 *before* aggregation, not filtered in the deck |
| I5 | **Denominators never mix sources, and never mix `temporal_stance`.** | S7 groups by `(source, brand, stance)`; a query that omits either grouping key raises |
| I6 | **No thresholds are hard-coded from taste.** Every constant is either derived at runtime, cited to a published convention, or left as `null` with a `TODO` naming the derivation. | `config/thresholds.yaml` — every entry needs a `source:` field; loader raises on a missing one |
| I7 | **Hinglish is preserved, never translated.** Translation destroys hesitation markers. | S2 detects language and sets a flag; no translate call exists anywhere in the codebase |
| I8 | **`wishlist_proximity` is frozen and git-committed with a timestamp before S5 runs.** | S5 refuses to start if `config/proximity.yaml` has an mtime later than its recorded `frozen_at` |

> I6 and I8 exist because Attempt 2 lost on exactly this: sensible constants, never justified, and one `«baseline»` placeholder that shipped. Encode the discipline in the loader so it survives 2 September.

---

## 1. Repo layout

```
ajio-engine/
├── config/
│   ├── sources.yaml          # per-source ids, endpoints, page caps
│   ├── taxonomy.yaml         # the 12 OAs, node map, detectability grades — FROZEN
│   ├── proximity.yaml        # wishlist_proximity table + frozen_at timestamp
│   ├── thresholds.yaml       # every numeric constant, each with a source: field
│   └── lexicon.yaml          # hesitation markers, EN + Hinglish — published in the appendix
├── prompts/
│   ├── classify_v1.txt       # S5 pass 1 — Gemini
│   ├── classify_v1_groq.txt  # S5 pass 2 — Groq, same task, different framing
│   └── segment_v1.txt        # S3 fallback for messy multi-signal reviews
├── src/
│   ├── collect/              # S1 — one module per source, all emit the same envelope
│   │   ├── play.py  appstore.py  pdp.py  reddit.py  youtube.py  complaints.py  x.py
│   ├── normalise.py          # S2
│   ├── segment.py            # S3
│   ├── filter.py             # S4
│   ├── classify.py           # S5  ← the LLM router lives here
│   ├── llm/
│   │   ├── router.py         # provider selection, rate limiting, retry, budget ledger
│   │   ├── gemini.py  groq.py
│   │   └── schema.py         # pydantic models; the JSON contract
│   ├── validate.py           # S6
│   ├── quantify.py           # S7
│   ├── compare.py            # S8
│   └── emit.py               # S9
├── data/
│   ├── raw/{source}/{brand}/{YYYY-MM-DD}.jsonl     # immutable
│   ├── interim/                                    # S2–S4
│   ├── labelled/utterances.parquet                 # S5 output
│   ├── gold/human_labels.csv                       # S6 — the only hand-made file
│   └── out/                                        # S9: the three deck-facing files
├── logs/
│   ├── drop_log.jsonl        # S4 — every drop with a reason. This is itself a finding.
│   ├── llm_ledger.jsonl      # every call: provider, model, tokens, latency, cost=0
│   └── quarantine.jsonl      # I3 failures
└── tests/
```

**Storage format:** JSONL for raw (append-only, survives a crash mid-run), Parquet from S5 onward (typed, fast to group). Never CSV as an intermediate — a verbatim containing a comma or newline will silently corrupt a row and you will not notice until the numbers are wrong.

---

## 2. S1 — Collection requirements, per source

All collectors emit the same envelope and nothing else. Classification happens later; a collector that classifies is a bug.

```jsonc
{
  "source": "play",
  "brand": "ajio",
  "source_id": "gp:AOqpTOH...",      // native id, used for dedupe
  "url": "https://...|null",
  "captured_at": "2026-08-22T11:04:00+05:30",
  "posted_at": "2026-06-14T00:00:00Z|null",
  "raw_text": "verbatim, unedited, unescaped",
  "rating": 3,
  "helpful_votes": 12,
  "meta": { }                         // source-specific extras, never read by later stages
}
```

### 2.1 Google Play — `com.ril.ajio`

- **Library:** `google-play-scraper` (v1.2.7 on PyPI, confirmed available). Unofficial; no API key.
- **Call:** `reviews()` with continuation tokens, `lang='en'`, `country='in'`, `sort=Sort.NEWEST`.
- **Do also fetch `lang='hi'`** — it returns a partly different slice. Dedupe by `source_id` afterwards.
- **Brands:** `com.ril.ajio` · `com.myntra.android` · `com.fsn.nykaa.nykaafashion` — **verify each package id on the Play listing before running; a wrong id fails silently by returning an empty list, not an error.** Assert `len(reviews) > 0` per brand.
- **Volume target:** collect until the continuation token exhausts *or* a per-brand cap in `sources.yaml` is hit. **Set the cap equal across brands** — an unequal cap contaminates the §5.3 differential, because the two proportions would then be measured on differently-deep slices of the review timeline.
- **Politeness:** 1–2 s sleep between pages, jittered. This is an unofficial scraper; hammering it gets the IP throttled and you lose a day.

### 2.2 Apple App Store — `id1113425372`

- **Library:** `app-store-scraper` (v0.3.5, confirmed available), country `in`.
- Low volume. Value is as a second structurally-matched surface for the differential, not for prevalence in its own right.
- Same equal-cap rule as Play.

### 2.3 AJIO PDP reviews & Q&A — **the priority source, and the hardest**

This is the reference source for the §5.2 opportunity index because it is *pre-purchase by construction*. It is worth disproportionate build time.

**Approach, in order of preference:**

1. **Find the JSON endpoint.** AJIO's PDP is a client-rendered app; reviews and Q&A arrive over XHR. Open a PDP in Chrome DevTools → Network → filter XHR → note the request that returns the review payload, its path, query params (product code, page, size), and required headers. Replay it with `httpx`. **Do not guess the endpoint from this document — capture it live, because it will have changed.** One captured request gives you paginated JSON and makes the rest of this source trivial.
2. **Fallback:** Playwright headless, `networkidle`, scroll-to-load, parse the rendered DOM. ~5–10× slower; acceptable for a few hundred products.

**Product sampling — this is a methodology decision, not a scraping detail.** Do not scrape "whatever products are on the homepage": homepage placement correlates with promotion, which correlates with price and stock behaviour, which is one of the things being measured. Instead:

- Fix a category set (see D1 in `03_engine_spec.md` — **narrow once, here, and write down why**).
- Within each category, take a **stratified sample across the rating distribution and the review-count distribution**, not the top-sorted list. Top-sorted lists are ordered by popularity, and popular items have systematically different stock behaviour.
- Record the sampling frame in `logs/` — the frame is an appendix table.

**Q&A is more valuable than reviews here.** A question asked on a PDP is a person hesitating in public, in the present tense, before buying. That is the single closest textual proxy to a wishlist decision that exists in public data. If time collapses, scrape Q&A and skip PDP reviews.

**ToS:** D3 in `03_engine_spec.md` is still open. Decide before running: public unauthenticated pages only, respect `robots.txt`, human-rate request timing, no logged-in scraping. Whatever you decide, **write the decision and its date into this file's log** — an evaluator may ask, and "we used only publicly accessible pages at human request rates" is an answer.

### 2.4 Reddit

- **Library:** `praw` (v8.0.3, confirmed available). Free API, OAuth script app — takes ~3 minutes to register, gives 100 QPM, well above what you need.
- Subreddits: `r/IndianFashion`, `r/india`, `r/OnlineShoppingIndia`, `r/IndianStreetwear`, plus `r/all` search on `ajio OR myntra OR "nykaa fashion"`.
- **Collect comments, not just submissions.** The decision-process narration lives in comments.
- Reddit is the only source with real **closure and intent** language. It is also small and self-selected — **verbatims and mechanism discovery only, never a prevalence denominator.** Mark `denominator_eligible: false` in `sources.yaml`.

### 2.5 YouTube comments

- **YouTube Data API v3**, free quota 10,000 units/day. `commentThreads.list` costs 1 unit and returns up to 100 comments → ~1M comments/day of headroom. Quota is not your constraint here; relevance is.
- `search.list` costs **100 units** per call — use it sparingly (≈20 searches, then work from a fixed video id list). Budget: searches first, then comment pulls.
- Video selection: AJIO/Myntra/Nykaa haul, review, and "worth it?" videos. Record the video id list; it is an appendix table for the same reason the PDP sampling frame is.

### 2.6 Complaint sites — PissedConsumer, Trustpilot, Reviews.io, MouthShut

- Plain `httpx` + `selectolax` (faster than BeautifulSoup, same job). Respect `robots.txt`, 2 s delay.
- **`denominator_eligible: false` for all four.** These are severity and verbatim sources. Their selection bias is not a caveat you can size — it is structural, and any rate computed from them is meaningless. `03_engine_spec.md` §2 already says "never quote a rate from this source"; enforce it in config so the S7 code cannot accidentally include them.

### 2.7 X / @AJIOLife

Lowest priority. The free X API tier is effectively unusable for read volume. Treat as opportunistic: if a public search surface yields data cheaply, take it; otherwise cut it and say so in the method line. Do not spend build hours here.

### 2.8 Dedupe

Dedupe on `(source, brand, source_id)` at S1 close. Then a **second, near-duplicate pass** at S2 on `sha1(normalised_text)` — complaint sites syndicate, and the same complaint appearing on three sites would triple-count in a prevalence numerator.

---

## 3. S2–S4 — Normalise, segment, filter

### S2 Normalise
- Unicode NFC, strip zero-width and control chars, collapse runs of >3 identical chars (`sooooo` → `sooo`) **but keep the emphasis** — it is a severity signal.
- Language: `lingua-py` or `fasttext-langdetect`. Three-way label `en | hi | hinglish | other`. Hinglish = Latin script + Hindi lexicon hits; a simple word-list rule is fine and is more auditable than a model.
- **No translation. No lowercasing of the stored text.** (Lowercase only inside matchers, on a copy.)
- Emit `posted_at` null-rate per source into `logs/` — I5's trend-claim gate depends on it.

### S3 Segment
- Sentence-split (`pysbd`, handles Indian-English punctuation better than naive regex), then **merge adjacent sentences that share a subject** so an utterance is a complete thought, not a fragment.
- **Preserve exact char offsets into `raw_text`.** `span: [start, end]`, and `raw_text[start:end]` must reconstruct the utterance byte-for-byte. Assert this.
- `utterance_id = sha1(f"{source}|{source_id}|{start}|{end}")` — stable across re-runs, which is what makes S5 resumable.
- Reviews under ~8 words: emit as a single utterance, do not split.

### S4 Filter — and the drop log is an output
Drop, logging a reason for each:

| Reason code | Rule |
|---|---|
| `no_text` | Rating-only review, empty after normalisation |
| `too_short` | < 3 tokens after stopwords |
| `spam` | URL-heavy, repeated across >5 source_ids, promo/seller boilerplate |
| `not_shopping` | App-crash-only, "nice app", pure star-rating language |
| `wrong_brand` | Mentions a competitor only, in a brand's own corpus |
| `near_dup` | Matches an existing `sha1(normalised_text)` |

**`logs/drop_log.jsonl` gets a line per drop.** Aggregate drop counts by reason and source into the appendix. "38% of Play Store reviews carry no text at all" is a real finding about the instrument, and it also pre-empts the "why is your n smaller than the app's review count?" question.

---

## 4. S5 — Classification, and the Groq + Gemini routing

### 4.1 The free-tier budget, computed

Per-call token cost with a batch of *B* utterances, assuming a ~900-token taxonomy/rubric preamble resent every call (**no prompt caching on free tier — do not assume it**), ~45 input and ~60 output tokens per utterance:

```
tokens_per_call ≈ 900 + B × 105
```

Published free-tier ceilings (**re-verify before the run — these move**):

| Provider / model | RPM | RPD | TPM | TPD | Daily utterance ceiling @ B=20 | Binding limit |
|---|---|---|---|---|---|---|
| Gemini 2.5 Flash-Lite | 30 | 1,500 | 1,000,000 | — | **~30,000** | RPD |
| Gemini 2.5 Flash | 15 | 1,500 | 1,000,000 | — | **~30,000** | RPD |
| Groq `openai/gpt-oss-120b` | 30 | 1,000 | **8,000** | **200,000** | **~1,300** | **TPD** |

**The decisive fact: Groq's free tier is throughput-poor for this workload.** At B=20 a single call is ~3,000 tokens, which exceeds a third of Groq's 8K *per-minute* budget — the effective ceiling drops to ~2 requests/minute, and the 200K/day token cap allows roughly 1,300 utterances a day. Gemini's free tier does ~20–40× that.

So: **Groq is not the bulk classifier. Do not build it as one.** Its speed advantage — real on paid tiers — is entirely erased by the free-tier TPM cap.

### 4.2 The routing, and why it is a method improvement rather than a workaround

| Lane | Provider | Job | Volume |
|---|---|---|---|
| **A — bulk** | Gemini 2.5 Flash-Lite | S5 pass 1: classify every utterance | Full corpus |
| **B — escalation** | Gemini 2.5 Flash | Re-classify where pass 1 returns `confidence < τ`, singly (B=1) for full attention | Low-confidence tail |
| **C — second annotator** | Groq `gpt-oss-120b` | Independently classify a stratified sample, blind to lane A's labels | ~1,000–1,500/day |

Lane C is the point. `03_engine_spec.md` §6 asks for a "two-pass: classify, then adversarially re-check." **If both passes are the same model, the re-check measures nothing** — a model agrees with itself, and self-agreement is not evidence. Running the second pass on a *different model family from a different vendor* produces genuine inter-annotator disagreement.

That disagreement is then usable in two ways, and both are defensible on a slide:

1. **Disagreement rate is a reportable reliability statistic** — model-vs-model Cohen's κ, reported alongside the human-vs-classifier κ from S6. Two independent reliability numbers is a stronger claim than one.
2. **Disagreement stratifies the S6 human sample.** Utterances where Gemini and Groq disagree are exactly where a human label is most informative. Sample disproportionately from the disagreement stratum, then reweight. This gets a defensible κ from a smaller hand-labelled sample — which matters, because the hand-labelling is *your* time and it is the scarcest resource in the whole build.

Write this into the appendix as a named method choice. It is the kind of thing an evaluator notices, and "we used two vendors' models as independent annotators because one model checking itself measures nothing" is a sentence that does work.

> Note what just happened: a free-tier constraint forced a *better* validation design than an unconstrained build would have produced. If the deck has room, that is worth one line — it reads as method maturity rather than as thrift.

### 4.3 τ, B, and every other constant — derived, not picked

Per I6, none of these ship as taste:

| Constant | How to set it | Not |
|---|---|---|
| `τ` (escalation confidence floor) | Plot pass-1 accuracy against the gold set, bucketed by self-reported confidence. Set τ at the bucket where accuracy first drops below the corpus mean. | 0.7 |
| `B` (batch size) | Sweep B ∈ {5, 10, 20, 40} against a fixed 100-utterance gold subset. Pick the **largest B whose accuracy is statistically indistinguishable from B=5**. Throughput rises monotonically with B; quality is the only limit, so measure it. | 20 because it looks reasonable |
| κ acceptance band | Cite Landis & Koch's conventional bands; require the "substantial agreement" range (0.61–0.80). | "we used 0.6" |
| S6 sample size *n* | Derive after S5: *n* large enough that the Wilson interval on the smallest area you intend to report is narrower than the smallest differential you intend to claim. Three lines of arithmetic; put them in the appendix. | 100 |
| `posted_at` null-rate cut-off for trend claims | Set from observed per-source null rates after S2. Until observed, `null` in `thresholds.yaml` with a `TODO`. | a round number now |

**`config/thresholds.yaml` loader raises on any entry lacking a `source:` field.** That is the mechanical enforcement of the one failure mode that has cost the most across attempts.

### 4.4 The classification prompt

`prompts/classify_v1.txt`, versioned; `classifier_version` in every row records which prompt file produced the label. A prompt edit mid-run without a version bump invalidates every count.

Structure:

```
ROLE
You are labelling utterances from Indian fashion e-commerce reviews and Q&A.
Text may be English, Hindi, or Hinglish (Hindi in Latin script). Label the
Hinglish as written — do not translate it, and do not treat it as noise.

TAXONOMY  (verbatim from config/taxonomy.yaml — closed, do not invent codes)
  OA-01 … OA-12 with one-line definitions
  tree_node ∈ {R, V, D, X, none}
  sub_node  ∈ { … }

THE FIELD THAT MATTERS MOST — temporal_stance
  pre_purchase   : speaker has not yet bought this item. Includes all questions
                   asked before ordering, and all "thinking about / about to /
                   still deciding" language.
  at_purchase    : speaker is in checkout or payment.
  post_purchase  : speaker has received or is awaiting an order.
  unclear        : do not guess. `unclear` is a correct answer.

SEVERITY RUBRIC (1–3, published in the appendix)
  1 mild friction, speaker proceeded anyway
  2 friction that changed what the speaker did
  3 speaker abandoned, or states they will not use the service again

HESITATION MARKERS
  Set hesitation_marker=true only if the text contains explicit decision
  language. The lexicon in config/lexicon.yaml is indicative, not exhaustive.

RULES
  - `none` and `unclear` are correct answers. Do not stretch to fit a code.
  - evidence_quote MUST be an exact substring of the input text, copied
    character-for-character. If you cannot find one, return confidence 0.
  - confidence is your own calibrated probability that the label is right.
  - Output ONLY valid JSON matching the schema. No prose, no markdown fence.

INPUT
  [{"utterance_id": "...", "text": "..."}, ...]

OUTPUT
  [{"utterance_id": "...", "tree_node": "...", "sub_node": "...",
    "opportunity_area": "...", "temporal_stance": "...",
    "hesitation_marker": bool, "severity": 1|2|3, "confidence": 0.0-1.0,
    "evidence_quote": "..."}, ...]
```

**Requirements on the call:**
- **Gemini:** use `response_mime_type="application/json"` + `response_schema`. Structured output eliminates the whole class of "model wrapped it in ```json" failures. Do not parse free text.
- **Groq:** `response_format={"type": "json_object"}`. Groq's JSON mode is looser than Gemini's schema mode — validate hard on receipt.
- **`temperature=0`** everywhere. A classifier with temperature is not reproducible, and reproducibility is what lets you re-run after a taxonomy fix.
- Validate every response against `src/llm/schema.py` (pydantic). Any item that fails schema validation, or fails the `evidence_quote in raw_text` assertion (I3), goes to `logs/quarantine.jsonl` — **never to the table with a repaired value.** A silently repaired label is a fabricated label.
- **Lane C must be blind.** The Groq prompt sees the text only; never pass lane A's label into lane C's context. A "please check this label" framing produces agreement bias and destroys the independence that makes lane C worth running.

### 4.5 Router requirements — `src/llm/router.py`

This module is where free-tier builds fail. Requirements:

- **Token-bucket limiter per (provider, model)** for RPM *and* TPM, plus a persistent daily counter for RPD/TPD that survives a process restart (`logs/llm_ledger.jsonl`, replayed on boot). A limiter that resets when you restart the script will burn the day's quota by mid-afternoon.
- **Estimate tokens before sending** and block until the bucket allows it. Reacting to 429s is too late on an 8K TPM budget — one oversized Groq call locks you out for a minute.
- **Retry with exponential backoff + full jitter** on 429/500/503. Cap at 5 attempts, then park the batch in a `deferred/` queue and continue. Never let one bad batch stall the run.
- **Honour `Retry-After`** when present.
- **Resumability is a hard requirement.** Before each batch, check which `utterance_id`s already have rows. The run *will* be interrupted — by a quota wall, a laptop sleep, or a crash — and a pipeline that restarts from zero is a pipeline you cannot afford to run twice.
- **Budget ledger:** log provider, model, prompt tokens, completion tokens, latency, attempt count, outcome for every call. At any moment `python -m src.llm.router --status` should print quota consumed and quota remaining per provider today. You need this to answer "can I finish tonight?" without guessing.
- **Two keys, both from env** (`GEMINI_API_KEY`, `GROQ_API_KEY`), loaded via `.env`, and `.env` in `.gitignore`. The repo will be linked in the deck; a key in git history is a bad day.

### 4.6 Free-tier terms — know this before running

Free-tier inputs and outputs may be used by the provider to improve their models; paid tiers generally are not. **Check each provider's current terms before the run.** For this project the corpus is public review text and the classification is not commercially sensitive, so this is acceptable — but decide it knowingly, and note it in one line in the appendix. Do not send anything from the interviews through a free tier.

---

## 5. S6 — Validation

The stage that will be tempting to skip on 1 September. Build it now.

1. **Model-vs-model κ** (lane A vs lane C) over the lane C sample. Cheap, automatic, and reportable on its own.
2. **Stratified human sample.** Strata: `opportunity_area` × `temporal_stance`, **over-sampled in the Gemini/Groq disagreement stratum** (§4.2). Reweight to the population when computing κ — an unweighted κ over an over-sampled disagreement stratum understates agreement, which is the wrong direction of error to publish.
3. **Blind labelling.** The labelling sheet shows `raw_text`, the utterance span, and nothing else. If you can see the model's guess, you are not a second annotator — you are an approver.
4. **Cohen's κ, human vs lane A.** Report per-field: `opportunity_area`, `temporal_stance`, `severity` separately. Blended κ hides the case where stance — the field the whole engine rests on — is the weak one.
5. **The gate:** below Landis & Koch's "substantial agreement" band (0.61–0.80), the numbers do not go on a slide. If a field misses, fix the prompt, bump `classifier_version`, and **re-run the full corpus** — not the sample.

**Time-box:** if the schedule collapses, cut *sources*, not S6. A validated engine on three sources beats an unvalidated one on nine, and only the first is defensible under questioning.

---

## 6. S7–S9 — Quantify, compare, emit

Formulas are in `03_engine_spec.md` §5. Implementation requirements only:

- **S7** groups by `(source, brand, temporal_stance, opportunity_area)`. **The detectability gate is applied here, before aggregation** (I4) — areas graded Weak/None are carried through with `oi = null` and `gate_reason = "not adjudicable from public text"`, never with a computed value that a later stage might accidentally render.
- **Wilson score intervals** on every proportion: `statsmodels.stats.proportion.proportion_confint(method='wilson')`. Point estimates never leave S7 unaccompanied.
- **S8** runs the two-proportion z-test **AJIO vs the pooled competitor proportion, AJIO excluded from the pool** (`statsmodels.stats.proportion.proportions_ztest`). Emit `ratio`, `p`, `n_ajio`, `n_pool` together as one record — they must be impossible to separate downstream, because the ratio alone is not a claim.
- **Matched sources only.** S8 raises if asked to compare across source types. Play-vs-Play, PDP-QA-vs-PDP-QA. There is no defensible Play-vs-PDP comparison.
- **S9 emits exactly three files** — `opportunity_index.csv`, `blind_spots.md`, `verbatims.md` — and **the deck reads from nothing else.** Any number in the deck that cannot be traced to a row in one of these three files does not go in the deck.
- `verbatims.md` groups the top 3 `evidence_quote`s per area by severity, with source and permalink. This is what saves you at 2 a.m. on 2 September.

---

## 7. Acceptance tests

A coding agent should treat these as the definition of done. `tests/` should contain each.

| # | Test | Passes when |
|---|---|---|
| T1 | Span reconstruction | For 1,000 random utterances, `raw_text[start:end] == utterance_text` exactly |
| T2 | Evidence-quote integrity | 100% of rows in `utterances.parquet` satisfy `evidence_quote in raw_text` |
| T3 | Idempotent re-run | Re-running S1–S5 with no new data adds zero rows and makes zero LLM calls |
| T4 | Interrupt-resume | `kill -9` mid-S5, restart → no duplicate rows, no re-classification of completed ids |
| T5 | Threshold guard | A `thresholds.yaml` entry without a `source:` field raises at load |
| T6 | Proximity freeze | S5 refuses to start if `proximity.yaml` mtime > its `frozen_at` |
| T7 | Denominator purity | S7 raises if asked to aggregate across `source` or across `temporal_stance` |
| T8 | Detectability gate | No row with `detectability ∈ {weak, none}` has a non-null `oi` anywhere in `data/out/` |
| T9 | Complaint-site exclusion | No proportion anywhere has a complaint-site denominator |
| T10 | Equal caps | Per-brand collected counts on Play are within the configured tolerance of each other |
| T11 | **Placeholder sweep** | No `?`, `«`, `TODO`, `XXX`, or `null` survives in `data/out/` at emit time — or if one does, S9 fails loudly and names it |

T11 is the automated version of Attempt 2's shipped `«baseline»`. Run it as a pre-commit hook on `data/out/`.

---

## 8. Build order and time budget

Today is **21 Aug**. Research deadline **24 Aug**. Submission **3 Sep, 3:59 PM IST**.

| Slot | Work | Note |
|---|---|---|
| 21 Aug, PM | Skeleton + router + limiter + ledger. Play Store collector for all 3 brands. | Router first. Everything downstream depends on it and it is the part that silently fails. |
| 22 Aug, AM | PDP Q&A endpoint capture + collector. Sampling frame written down. | The highest-value and highest-risk source. If the endpoint capture fails by noon, fall back to Playwright immediately — do not spend the afternoon on it. |
| 22 Aug, PM | S2–S4. Drop log. Freeze `proximity.yaml`, commit, timestamp. | **Freeze before any classification runs.** |
| 23 Aug, AM | S5 lane A over the full corpus. B-sweep first on 100 utterances. | Start the bulk run early — RPD is a *daily* wall, and hitting it at 4 p.m. costs you the day. |
| 23 Aug, PM | Lane C (Groq) on the sample. Model-vs-model κ. Hand-label the disagreement stratum. | Hand-labelling is the scarce resource. Protect this block. |
| 24 Aug, AM | S6 κ, S7, S8. Reddit + YouTube if the schedule held. | |
| 24 Aug, PM | S9 emit. Blind-spot register written. Read the three output files. | Findings due. |

**If you are behind on 23 Aug:** cut Reddit, YouTube, X, and the complaint sites. Keep Play (3 brands) + PDP Q&A + full S6. That combination still supports the reference source, the differential, and the validation — which is everything the deck actually needs.

---

## 9. Open decisions carried from `03_engine_spec.md`

D1 (category scope), D2 (Hinglish), D3 (PDP ToS approach), D4 (comparison set), D5 (`wishlist_proximity` values) are still open and are yours. **D5 blocks S5 and therefore blocks 23 Aug** — settle it on 22 Aug at the latest, commit the table, and send it over for a pressure-test before you freeze it.

---

## 10. Log

| Date | Change | Reason |
|---|---|---|
| 21 Aug 2026 | Created. Implementation spec for S1–S9 under Groq + Gemini free tiers. Library availability confirmed on PyPI; free-tier ceilings taken from provider docs on this date. | Build phase |
| 21 Aug 2026 | Routing decided from the token budget rather than from vendor speed claims: Gemini for bulk, Groq as an independent second annotator. | Groq free tier's 8K TPM / 200K TPD makes it unusable for bulk; the constraint yields a genuine inter-annotator design |
| | *Record the D3 ToS decision here when made* | |
