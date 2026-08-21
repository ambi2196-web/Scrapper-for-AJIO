# AI Discovery Engine — Output Specification

**AJIO · Wishlist → Purchase.** Spec for the scraper + analysis layer. You build the scraper; this defines what it must emit so that the analysis, the deck slide, and the problem definition all have something to stand on.

Brief's bar: the engine must go *"beyond summarizing reviews or performing sentiment analysis"* — it must **identify, quantify where possible, and compare** opportunity areas. Those three verbs are the spec. Sentiment scoring is explicitly not the deliverable; treat any sentiment field as an input, never an output.

---

## 1. The central design problem — read this before writing any code

**Wishlist abandonment leaves almost no textual trace.**

Nobody writes a Play Store review saying "I saved a kurta on 4 June and never bought it." People write reviews when something *happened to them*: a late delivery, a refused refund, a fake product, a size that did not fit. Non-purchase is a **silent non-event** — the same structural problem identified in Attempt 2, arriving again in a new project.

Naming this honestly was one of Attempt 2's genuine strengths. But Attempt 2 then cited percentages from the corpus it had just argued was blind, and a grader cannot credit both. So the rule for this engine is strict:

> **If the engine cannot see a phenomenon, it does not produce a number about that phenomenon.** Not a small number. Not a caveated number. No number.

This is not a limitation to apologise for — it is the design constraint that makes the engine interesting. An engine that says *"here is what public text can and cannot resolve, and here is precisely how far the resolvable part gets us"* is a stronger artefact than one producing a tidy pie chart of invented certainty.

### What follows from it

The engine must not hunt for complaints about wishlists. It must hunt for **traces of the conditions that make a saved item fail to convert** — which do generate text, because each is an event:

| Tree node (file 02) | Does it leave a trace? | What the trace looks like |
|---|---|---|
| **V — viability** | **Yes, strongly** | "Out of stock in my size within a day", "item disappeared from my saved list", "price changed after I saved it" |
| **D — reversal tolerance** | **Yes, strongly** | Return, refund, pickup and exchange complaints — abundant in the AJIO corpus |
| **D — conviction** | **Yes, moderately** | Size inconsistency across brands, quality-vs-photo mismatch, fabric surprises |
| **D — closure** | **Weakly** | Occasional "couldn't decide between", mostly in Reddit and YouTube comments, not reviews |
| **R — return/recall** | **Barely** | Almost no one narrates their own forgetting |
| **X — execution** | Yes | Payment failure, cart emptied, checkout errors |
| **Intent mix** | **No** | Not resolvable from public text. **Interviews only.** |

**This table is a deck slide.** It is the engine's honesty artefact and its most defensible output: a map of what public evidence can and cannot adjudicate, produced *before* the results. It also pre-empts the obvious evaluator question — "how do you know this from reviews?" — by answering it on the slide instead of in Q&A.

---

## 2. Source map

Grounded on an actual availability check, 19 Aug 2026. Volumes are order-of-magnitude, to be replaced by real counts once the scraper runs.

### Tier 1 — high volume, low wishlist-specificity

| Source | Handle / ID | Rough scale | Value | Caveat |
|---|---|---|---|---|
| Google Play reviews | `com.ril.ajio` | Very large | V, D-reversal, X | Heavily skewed to logistics; ratings-only reviews are noise |
| Apple App Store reviews | `id1113425372` | Moderate | Same | Smaller, more affluent skew |
| PissedConsumer | `ajio.pissedconsumer.com` | ~7.6K | D-reversal, very rich narratives | Selection bias toward anger — **never quote a rate from this source** |
| Trustpilot | `ajio.com` | Moderate | D-reversal | Same bias |
| Reviews.io | AJIO store | ~2.1K | Mixed | Same |
| MouthShut | AJIO reviews | Moderate | India-specific, long-form | Older skew |

### Tier 2 — lower volume, much higher wishlist-specificity

| Source | Value | Why it matters |
|---|---|---|
| **AJIO PDP reviews & Q&A** (item-level) | **D-conviction, V-size** | The single richest source for pre-purchase hesitation, because Q&A is *literally people asking questions before buying*. Prioritise this. |
| Reddit — `r/IndianFashion`, `r/india`, `r/OnlineShoppingIndia`, `r/IndianStreetwear`, brand and haul threads | Closure, intent, aspiration | Only place people narrate their own decision process |
| YouTube comments on AJIO haul/review videos | Conviction, closure | High volume per video, conversational, under-scraped |
| X / Twitter `@AJIOLife` support replies | V, X | Timestamped and complaint-typed |
| Quora — AJIO quality/sizing/returns questions | Conviction | Long-form, pre-purchase framing |

### Tier 3 — comparison set (**do not skip — the brief demands comparison**)

Run a reduced pipeline over **Myntra** and **Nykaa Fashion**. The comparative differential is what converts "AJIO users complain about X" (unfalsifiable — every retailer's users complain about X) into "**AJIO users raise X at n× the rate of Myntra users on structurally identical sources**". That differential is the engine's most credible single output and it is unavailable from AJIO data alone.

---

## 3. Record schema

One row per extracted **utterance**, not per review. A single review can contain three distinct signals and must produce three rows, or prevalence counts will be wrong.

```jsonc
{
  "utterance_id":      "sha1(source_id + char_span)",
  "source":            "play | appstore | reddit | youtube | pdp_qa | pdp_review | pissedconsumer | trustpilot | mouthshut | quora | x",
  "brand":             "ajio | myntra | nykaafashion",
  "source_id":         "native review/comment/post id",
  "url":               "permalink or null",
  "captured_at":       "ISO-8601",
  "posted_at":         "ISO-8601 | null",       // null is common — track null rate, it caps trend claims
  "raw_text":          "verbatim, unedited",
  "span":              [start, end],            // char offsets into raw_text
  "language":          "en | hi | hinglish | other",
  "rating":            1-5 | null,
  "helpful_votes":     int | null,

  // --- classification layer ---
  "tree_node":         "R | V | D | X | none",
  "sub_node":          "in_stock | size_available | still_listed | conviction_fit | conviction_quality | closure | reversal_tolerance | recall | checkout | payment | none",
  "opportunity_area":  "see §4 taxonomy",
  "temporal_stance":   "pre_purchase | at_purchase | post_purchase | unclear",   // ← the most important field
  "hesitation_marker": true | false,
  "severity":          1-3,
  "confidence":        0.0-1.0,
  "classifier_version":"v1",

  // --- provenance ---
  "evidence_quote":    "the exact substring justifying the label",
  "human_reviewed":    true | false,
  "human_label":       "..." | null
}
```

### Fields that carry unusual weight

**`temporal_stance`** — the field that makes this engine different from a review summariser. Post-purchase complaints are abundant and mostly irrelevant to a wishlist metric. Pre-purchase hesitation is rare and directly relevant. **Report every prevalence figure split by stance, never blended.** If you build one thing carefully, build this.

**`hesitation_marker`** — flags decision-language independent of topic: *"was about to buy but"*, *"kept it in my list"*, *"still thinking"*, *"almost ordered"*, *"had it saved"*, *"debating between"*, *"waiting to see if"*. Build this as an explicit lexicon plus an LLM pass, and **report the lexicon in the appendix** — a stated lexicon is auditable, a black-box classifier is not.

**`evidence_quote`** — every classification must carry the substring that justifies it. This is what lets you put three real verbatims on a slide in ninety seconds instead of re-reading the corpus at 2am on 2 September.

---

## 4. Opportunity-area taxonomy

Fixed and closed. An open taxonomy produces incomparable buckets, and comparison is the requirement. Twelve areas, each mapped to a tree node — the mapping is what lets engine output feed the metric tree instead of sitting beside it.

Each area carries a **`detectability`** grade, assigned from §1's visibility table and frozen before any classification runs. This field is what stops the engine from contradicting itself.

| ID | Opportunity area | Node | Detectability |
|---|---|---|---|
| OA-01 | Size unavailable in wanted variant | V | Strong |
| OA-02 | Item sold out / delisted after saving | V | Strong |
| OA-03 | Size inconsistency across brands — cannot predict fit | D-conviction | Strong |
| OA-04 | Quality vs. imagery mismatch | D-conviction | Strong |
| OA-05 | Fabric / material uncertainty | D-conviction | Moderate |
| OA-06 | Return process friction | D-reversal | Strong |
| OA-07 | Refund delay / dispute | D-reversal | Strong |
| OA-08 | Exchange unavailable or hard | D-reversal | Moderate |
| OA-09 | Cannot choose between similar options | D-closure | **Weak** |
| OA-10 | Forgot / never came back | R | **None** |
| OA-11 | Delivery timing uncertainty | D-reversal | Strong |
| OA-12 | Checkout / payment failure | X | Strong |

### The detectability gate

**Areas graded Weak or None are excluded from the ranked opportunity index** — the same hard gate applied to monetary-only areas in §5.2. They still appear in the output, greyed, labelled *"not adjudicable from public text."*

Without this gate the engine commits the Attempt 2 error with the sign reversed: OA-10 would return a near-zero prevalence, and the deck would read that as *"forgetting is not the problem"* when it actually means *"forgetting is not narratable."* A low number from a blind instrument is more dangerous than no number, because it looks like evidence.

Two greyed rows on the engine slide, each with its reason, do real work: they show the method knew its own limits before it produced results, and they hand the interviews a job that only interviews can do.

Add areas only with a written justification and a re-run of the full corpus. A taxonomy that grows mid-analysis silently invalidates every earlier count.

---

## 5. The quantify-and-compare layer

This is the part the brief is actually testing, and the part most submissions will skip. Four outputs. Each must be reproducible from the utterance table by a query you can show.

### 5.1 Prevalence — with an honest denominator

For each opportunity area:

```
prevalence(OA, source, stance) = utterances(OA, source, stance) / total classified utterances(source, stance)
```

**Denominator rules, non-negotiable:**
- Never mix sources in one denominator — a selection-biased complaint site and a Play Store sample are not the same population
- Never blend temporal stances
- Report **Wilson score intervals**, not point estimates. `X.X% [lo–hi]` on n=N is a different claim from `X.X%`, and the deck should show the difference *(format illustration only — no real figures exist yet)*
- Where the `posted_at` null rate is high enough that the dated subsample is no longer representative, do not make trend claims from that source. **The cut-off is `?` until actual per-source null rates are observed** — picking a round number now is the Attempt 2 threshold failure in advance

### 5.2 Severity-weighted opportunity index

Prevalence alone ranks loudness, not importance. Four factors, each independently defensible:

```
OI(area) = prevalence × mean_severity × wishlist_proximity × addressability
```

| Factor | Range | Source | Notes |
|---|---|---|---|
| `prevalence` | 0–1 | Computed **on one named reference source** — see below | Pre-purchase stance only |
| `mean_severity` | 1–3 | Classifier | Rubric must be written down and published in the appendix |
| `wishlist_proximity` | 0–1 | **Fixed table, frozen and timestamped before S5 runs** | How directly this area sits on the 30-day wishlist path |
| `addressability` | 0 or 1 | Constraint check | **0 if the only fix is a monetary incentive.** Hard gate, not a soft weight |
| `detectability` | gate | §4 table | Weak/None → excluded from the ranking |

**Which source's prevalence?** §5.1 forbids mixing sources in a denominator, so `OI` is undefined unless a reference source is named. **Use AJIO PDP Q&A as the reference** — it is pre-purchase by construction, which is the property the whole index depends on. Every other source is then reported as a *robustness check*: if the ranking holds across sources, say so; where it flips, that flip is itself a finding about which population you are hearing from.

> **Freeze `wishlist_proximity` before S5, not before S7.** S5 (classification) already reveals the shape of the answer, so freezing after it means tuning weights with the result in view. The index's entire defensibility rests on this one point — set the table, timestamp it, commit it, and put the timestamp in the appendix. If it is tuned after seeing results, the index is a rationalisation with a formula on top, and that is exactly what a sharp evaluator probes.

`addressability` as a binary gate is how the no-monetary-incentives constraint becomes a visible part of the method rather than a line of prose. Areas gated to zero should still appear in the output, greyed, with the reason — showing what you excluded and why is a rigour signal.

### 5.3 Comparative differential — the credibility engine

For each area, on structurally matched sources only (Play vs Play, PDP-QA vs PDP-QA):

```
differential(OA) = prevalence_ajio / prevalence_competitor_pool
```

where the competitor pool is Myntra + Nykaa Fashion utterances **pooled into a single proportion, excluding AJIO**.

Two details that matter and are easy to get wrong:

- **Pool, do not average.** A ratio against the mean of three brand-level rates does not match the test you would run on it. A two-proportion z-test compares *two* proportions — AJIO's and the pool's — so the statistic and the test have to be defined against the same pair.
- **Exclude AJIO from the denominator.** If AJIO is inside the category mean, the ratio is compressed toward 1 and, with only three brands, is arithmetically capped near 3× regardless of how extreme the real difference is. An excluded pool has no such ceiling.

Report the z-test p-value and both n's alongside every differential. Rank by differential, not absolute prevalence.

**Why this matters more than anything else in the engine:** "AJIO users complain about returns" is true of every Indian fashion retailer and adjudicates nothing. "AJIO pre-purchase utterances raise size-unavailability at *N.N×* the pooled competitor rate (p = *?*, n = *?*)" is a finding. The first sentence is a summary; the second is a discovery. The brief's "compare" verb is asking for the second.

*(The italicised placeholders are deliberate. No figure in this file is a result — every number in the final deck comes from a run of this pipeline or it does not appear.)*

### 5.4 The blind-spot register — a required output, not an appendix

A table of what the engine **could not** resolve, published alongside the findings:

| Phenomenon | Why invisible | What would resolve it |
|---|---|---|
| Intent mix at save time | Nobody narrates their own saving | Interviews |
| Silent forgetting | Non-events generate no text | Product analytics (unavailable) |
| Bypass share | Requires clickstream | Assumption + sensitivity |
| Emotional payoff of saving | Not a complaint, so not in a complaint corpus | Interviews (divergence log M8) |

Publishing this **before** the findings inoculates every number that follows. It also does something quietly valuable: it makes the six interviews look *necessary* rather than obligatory, because the register names exactly what only they can answer.

---

## 6. Pipeline stages

| Stage | Output | Note |
|---|---|---|
| S1 Collect | Raw JSONL per source, deduped by native id | Store raw separately from processed. Never overwrite raw. |
| S2 Normalise | Unified record, language detected, Hinglish preserved | Do **not** translate — translation destroys hesitation markers |
| S3 Segment | Review → utterances | Sentence-ish, with char spans preserved |
| S4 Filter | Drop ratings-only, spam, seller-bot text | Log drop counts by reason. The drop log is itself a finding. |
| S5 Classify | Node, sub-node, area, stance, severity | LLM + lexicon. **Two-pass: classify, then adversarially re-check a sample** |
| S6 Validate | Human-label a stratified sample, compute Cohen's κ vs the classifier | **If κ falls below "substantial agreement" the numbers do not go on a slide.** See note. |
| S7 Quantify | Prevalence tables + Wilson intervals | Per source, per stance |
| S8 Compare | Differentials + significance | Matched sources only |
| S9 Emit | `opportunity_index.csv`, `blind_spots.md`, `verbatims.md` | The deck reads from these three files only |

**Two numbers in S6 need earning rather than picking** — this is the exact Attempt 2 failure mode (`0.70 / 0.50 / +2pp / 80%` chosen sensibly, justified never), so handle it here rather than on the slide:

- **The κ threshold.** Do not assert 0.6 as a house rule. Cite Landis & Koch's conventional interpretation bands for kappa, in which 0.61–0.80 is the "substantial agreement" range, and state that the engine's numbers require reaching that band. A cited convention is defensible; a round number is not.
- **The sample size.** Derive it, do not pick 100. The requirement is: *n large enough that the Wilson interval on the smallest area you intend to report is narrower than the smallest differential you intend to claim.* You cannot compute that until you have seen rough prevalences after S5 — so the plan is: run S5, read the rough rates, back out n, then sample. Write the derivation into the appendix; it is three lines and it converts the project's historical weak spot into a visible strength.

**S6 is the stage that will be tempting to skip on 1 September.** It is also the one that separates a quantified engine from a plausible one. Put it in the plan now so it survives time pressure — and if time collapses, cut sources rather than validation.

---

## 7. What the engine slide should show

One slide, and it should not be architecture. Architecture diagrams are the default and they are boring because they claim nothing.

Better structure: **method → what it could see → what it found → what it could not see.** Suggested contents:

- A one-line method statement (sources, n, stance split, κ)
- The ranked opportunity index — bars, sorted by differential, with the monetary-gated areas greyed out and labelled
- The blind-spot register, compressed to three lines
- One verbatim that captures the top area

The greyed-out gated areas are worth the pixels: they show the constraint was engineered into the method rather than remembered at the end.

---

## 8. Build order

Roughly two days, given the 24 August research deadline:

1. **PDP reviews + Q&A for a sample of AJIO categories** — highest signal-to-noise, and Q&A is inherently pre-purchase. Start here even though Play Store is easier.
2. **Play Store + App Store, all three brands** — volume and the comparison baseline
3. **Reddit + YouTube comments** — the only source for closure and intent language
4. **Complaint sites** — verbatims and severity only. **Never a prevalence denominator.**

If time runs short, cut breadth, not S6 validation. A validated engine over three sources beats an unvalidated one over nine, and the deck can defend the first.

---

## 9. Open decisions for you

| # | Decision | Why it is yours |
|---|---|---|
| D1 | Category scope — all AJIO, or 2–3 categories? | Narrow gives depth and a cleaner size story; broad gives a defensible denominator. Note the Attempt 2 compounding-narrowing risk — if you narrow, narrow *once*, here, and say why. |
| D2 | Hinglish handling — classify natively or filter out? | Filtering is easy and biases the sample toward English-preferring users, who are plausibly a different segment |
| D3 | Whether to scrape logged-in PDP Q&A | Access and ToS question — your call on approach |
| D4 | Comparison set — both Myntra and Nykaa, or just Myntra? | Both is stronger; one is affordable |
| D5 | `wishlist_proximity` values | **Must be frozen and timestamped before S5 runs** — S5 already shows you the shape of the answer. Send me the table and I will pressure-test it. |

---

## 10. Log

| Date | Change | Reason |
|---|---|---|
| 19 Aug 2026 | Created. Source map grounded on availability check | Phase B |
| 19 Aug 2026 | Added `detectability` gate; fixed OI reference source; pooled the competitor differential to match the z-test; replaced picked thresholds (κ, n, null rate) with derivations; moved the proximity freeze to pre-S5 | Adversarial audit against the Attempt 2 failure modes |
