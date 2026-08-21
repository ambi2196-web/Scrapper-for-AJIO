# Decision log

Every open decision, its status, and the date it was settled. An evaluator may
ask about any of these, and "we decided X on date Y for reason Z" is an answer
where a shrug is not.

Decisions carried from `03_engine_spec.md` are D1–D5. Decisions made during the
build are B1 onward.

---

## Settled

### D1 · Category scope for PDP sampling
**Settled:** 22 Aug 2026 · **Decision:** BROAD — sample across all major categories.

Eight categories: women's ethnic, women's western, women's footwear, men's
topwear, men's bottomwear, men's footwear, kids' clothing, bags & accessories.

**Reason.** A narrow scope buys depth in one size story but makes the
denominator category-specific, and the brief asks for opportunity areas across
the wishlist journey rather than within one department. Breadth also protects
the differential: Myntra and Nykaa Fashion have different category mixes, so a
narrow AJIO scope would end up comparing unlike catalogues and the comparison is
the credibility engine.

Narrowed **once**, here, per 03 §9 D1 — the Attempt 2 compounding-narrowing risk
is that scope keeps tightening downstream until the finding rests on a sliver.
No further narrowing is permitted without a written note here.

**Enforced by:** `config/sources.yaml` `pdp_qa.sampling.categories`, plus
`forbid_homepage_sampling: true`. The sampling frame stratifies on category ×
rating band × review-count band — 128 strata, 8 products each, ~1,024 PDPs — and
is written to `logs/pdp_sampling_frame.jsonl` as an appendix table.

`products_per_stratum: 8` is a **collection budget, not a statistical
threshold**. Whether it yields enough Q&A utterances for the reference source
cannot be known until S5; verify against `min_cell_n_for_reporting` afterwards
and top up if short.

---

### D3 · PDP terms-of-service approach
**Settled:** 22 Aug 2026 · **Decision:** public-only, robots-honoured, **and PDP
is cut outright if robots.txt disallows.**

The position, implemented in `src/collect/base.py`:

- Public, unauthenticated pages only
- `robots.txt` checked per origin and honoured; an **unreachable** `robots.txt`
  is treated as a disallow rather than as permission
- Human-rate request timing, 2 s base with jitter
- No logged-in scraping, no session tokens, no CAPTCHA circumvention
- No storage of reviewer usernames or other personal identifiers

**The pre-commitment is the part that matters.** Playwright renders a page
whether or not robots.txt permits fetching it, so a disallow could be quietly
routed around by switching `mode: playwright`. That route is closed in advance:
`PDPCollector.robots_preflight()` runs before any collection and raises,
cutting the source rather than falling back.

Deciding this before running is what makes "we honoured robots.txt" a true
sentence rather than one that survived only because nobody checked. A cut is not
a failure — it goes in the method line, and Play plus the App Store still carry
the differential while Reddit and YouTube still carry mechanism.

---

### D5 · `wishlist_proximity` values
**Settled:** 22 Aug 2026 · **Frozen:** before any classification ran ·
**Commit:** `406dcc7`

**The principle** — this is what has to survive questioning, more than any
individual number:

> A wishlist introduces a **time gap** between intent and purchase. That gap is
> the only thing structurally distinguishing "wishlist → purchase" from "browse
> → purchase". So proximity grades how much of an area's harm is **created or
> amplified by the gap**, rather than harm that would befall an immediate
> purchase equally.

The obvious alternative — "is it on the R→V→D→X chain?" — was rejected because
all twelve areas are on that chain, so it does not discriminate and would
collapse the weight to a constant. The gap test discriminates, and it points at
interventions that are specifically *wishlist* interventions rather than general
conversion fixes.

| Weight | Areas | Rationale |
|---|---|---|
| 1.0 | OA-02, OA-10 | The gap **is** the mechanism — "sold out after saving", forgetting |
| 0.9 | OA-01, OA-09 | Strongly amplified by the gap — size-level stock decays fastest; a wishlist *is* the undecided set |
| 0.7 | OA-03, OA-04 | Hesitation the gap prolongs but does not create |
| 0.6 | OA-05 | Narrower, lower-volume form of OA-04 |
| 0.5 | OA-06, OA-11 | Anticipated downside risk dampening commitment |
| 0.4 | OA-07, OA-08 | Overwhelmingly post-purchase; damages the *next* cycle more than this one |
| 0.3 | OA-12 | On the path, but a checkout fix helps all conversion equally |

**OA-12 is the contestable one and is flagged as such in the file.** The
counter-view — that W30 should weight any on-path failure equally regardless of
gap-specificity — would put it at 0.8+. Considered and rejected at freeze time
because the index exists to rank wishlist interventions.

**Sensitivity, required before presenting:** re-rank with every weight set to
1.0 and report whether the top three areas change. Stable ranking means the
weights are not doing the work and the finding is robust; a flip means lead with
prevalence instead.

**Enforced by:** `config/proximity.yaml` carries `frozen_at` and `frozen_commit`;
`src/config.py` compares the file mtime and S5 refuses to start if it was touched
later. `freeze_proximity()` also refuses to freeze any weight lacking a `reason`.

---

### B1 · LLM routing decided from token arithmetic, not vendor speed claims
**Settled:** 21 Aug 2026

Groq's free tier allows 8,000 tokens per minute and 200,000 per day. A
20-utterance batch is roughly 3,000 tokens — more than a third of the entire
per-minute budget — which drops the effective rate to about two requests a minute
and caps the day at roughly 1,300 utterances. Gemini's free tier does twenty to
forty times that.

So Gemini runs the bulk pass and Groq runs as an independent second annotator on
a stratified sample. Groq's speed advantage is real on paid tiers and entirely
erased by the free-tier TPM cap; building it as the bulk classifier would have
failed on the first afternoon.

**The consequence is better than the constraint.** A second pass by the same
model measures nothing — a model agrees with itself. Two vendors' model families
produce genuine inter-annotator disagreement, which is both a reportable
reliability statistic and the stratification variable for the human sample.

### B2 · X / Twitter cut
**Settled:** 22 Aug 2026

The free X API read tier does not support the volume needed, and non-API scraping
is brittle and a terms problem. X is `denominator_eligible: false` regardless, so
the most it could contribute is verbatims — which Reddit already supplies at
lower cost and with better decision-process narration.

`sources.yaml` has `x.enabled: false` and `src/collect/x.py` raises with this
reason, so the cut is recorded rather than appearing as an omission.

### B3 · Complaint aggregators excluded from every denominator
**Settled:** 21 Aug 2026

PissedConsumer, Trustpilot, MouthShut and Reviews.io are severity and verbatim
sources only. People arrive at a complaint site *because* they have a complaint,
so the base rate of complaint there is definitionally near 1 and any proportion
computed from it describes what the venue is for, not the brand.

This is not a bias that can be estimated and corrected — it makes the quantity
meaningless. Enforced in `sources.yaml` and refused in `src/quantify.py` (T9).

### B4 · Free-tier provider terms accepted knowingly
**Settled:** 22 Aug 2026

Free-tier inputs and outputs may be used by providers to improve their models;
paid tiers generally are not. The corpus here is public review text and the
classification is not commercially sensitive, so this is acceptable.

**Nothing from primary research — interviews, internal data — goes through a free
tier.** Re-check both providers' current terms before the run; they move.

### B5 · Public repo carries outputs, not the corpus
**Settled:** 22 Aug 2026

The repo is public and linked in the deck. `data/raw/`, `data/interim/` and
`data/labelled/` are gitignored. Only `data/out/` and the aggregated
`data/dashboard/` snapshot are committed — enough to render every figure and
trace every quote, without republishing a scraped corpus wholesale.

Reviewer usernames are not collected at all. They are not needed for any analysis
in the engine, and a public repo is a poor place for them.

### B6 · Streamlit dashboard reads committed artifacts only
**Settled:** 22 Aug 2026

The deployed app performs no scraping and makes no LLM calls.
`requirements-dashboard.txt` omits every collection and provider library, so the
deployed dashboard has no library capable of an outbound call. It is also why the
Cloud build is fast and needs no secrets.

### B9 · I8 enforced by content hash, not file mtime
**Settled:** 22 Aug 2026 · **Departs from 04 §0, deliberately**

04 §0 specifies that S5 "refuses to start if `config/proximity.yaml` has an
mtime later than its recorded `frozen_at`". That check cannot work for a
git-tracked file: `git clone` and `actions/checkout` stamp every file with the
checkout time, so a fresh clone always looks modified and CI failed on exactly
this. Meanwhile a real edit could be hidden with `touch -d`.

It fails in both directions — false alarm on an honest clone, silent pass on a
dishonest edit — so it is replaced with a SHA-256 fingerprint over `weights` and
`scale`, recorded as `frozen_sha256` at freeze time and re-verified on every
load.

This is strictly stronger and states the invariant more precisely: **touching
the file is harmless; changing the weights is the violation.** Two tests cover
both directions — a tampered weight raises, and a file whose mtime is a day in
the future loads clean.

### B7 · `addressability` implemented as a hard gate, not a weight
**Settled:** 22 Aug 2026

03 §5.2 specifies four OI factors, of which `addressability` (0 or 1) was
missing from the first build. It is now in `config/taxonomy.yaml` per area, with
a required rationale, and multiplies into the index in `src/quantify.py`.

Binary rather than soft is how the no-monetary-incentives constraint becomes a
visible part of the method instead of a line of prose: a zeroed area still
appears in the output, greyed, with its reason. All twelve areas currently score
1 — none is fixable only by a discount. OA-07 was the closest call and is
explicitly **not** gated: paying a refund already owed is a process fix, not an
incentive.

### B8 · Opportunity index computed on a named reference source
**Settled:** 22 Aug 2026

03 §5.1 forbids mixing sources in a denominator, so the index is undefined
unless one source is named. **AJIO PDP Q&A is the reference** — pre-purchase by
construction, which is the property the whole index depends on. Every other
source is reported as a robustness check; where the ranking flips between
sources, that flip is itself a finding about which population is being heard.

Recorded in `taxonomy.yaml opportunity_index`, and `src/quantify.py` flags
`is_reference_cell` on every row.

---

## Template

```
### Dn · <title>
**Status:** OPEN | SETTLED <date>
**Blocks:** <what cannot proceed>
**Decision:** <what was decided>
**Reason:** <why — the part an evaluator will ask about>
**Enforced by:** <the code or config that makes it stick>
```
