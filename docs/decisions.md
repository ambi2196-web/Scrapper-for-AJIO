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

### D3-OUTCOME · PDP Q&A and reviews CUT — the source does not exist
**Determined:** 22 Aug 2026 · **Triggered by the D3 pre-commitment**

PDP Q&A was the priority source and the designated reference source for the
opportunity index. It is not collectable, for two independent reasons — either
alone is sufficient.

**1 · There is nothing there.** Eight randomly sampled AJIO PDPs, drawn from the
sitemap, all return `enableReview: OFF` and `showReviewProdBtn: OFF` in the
preloaded state, `ratings.avgRating` empty, and zero occurrences of
`aggregateRating` anywhere in the served HTML. **AJIO does not currently expose
customer reviews or Q&A on its product pages.** This is not a scraping
difficulty; the surface does not exist.

**2 · The path it would come from is disallowed.** `robots.txt` (fetched 22 Aug
2026) allows `/p/` product pages but disallows `/api/` and `/api/*`. Any review
payload on a client-rendered PDP arrives over `/api/`. The Playwright fallback
in 04 §2.3 does not rescue this: rendering the page still causes our agent to
fetch the disallowed `/api/` paths, so it routes around robots.txt rather than
honouring it. That is precisely the workaround D3 pre-committed to refusing.

**This is a finding, not a failure**, and it belongs on the blind-spot register:
the single richest hypothesised source of pre-purchase hesitation for this
retailer is one the retailer does not publish. An evaluator asking "why no PDP
Q&A?" gets a checked, dated answer rather than a shrug.

**Consequence — the opportunity index loses its reference source.** 03 §5.2
names AJIO PDP Q&A specifically, because it is pre-purchase *by construction*
rather than by classification, and §5.1 forbids mixing sources in a denominator.
With PDP gone, **no pre-purchase-by-construction surface remains**: every
surviving source is retrospective, so `temporal_stance` must be inferred from
wording everywhere. The reference source must be re-designated and the weakening
disclosed. See the open question below.

**Sitemap note:** the product sitemaps ARE allowed and advertised in robots.txt
(76 files, ~40,000 URLs each, all on `/p/`). They remain a valid sampling frame
if any AJIO product-level surface becomes collectable later.

---

### D4 · Comparison set
**Settled:** 22 Aug 2026 · **Decision:** per-surface, because the brands are not
available on both.

| Surface | Focal | Pool |
|---|---|---|
| Play | AJIO | Myntra + Urbanic |
| App Store | AJIO | Myntra + Nykaa Fashion |

**Nykaa Fashion is not collectable on Play.** The id in 03 §2
(`com.fsn.nykaa.nykaafashion`) returns 404, and a Play search surfaces the
listing "Nykaa Fashion - Shopping App" with a **null appId** — indexed but not
separately installable in India, most likely folded into `com.fsn.nykaa`.

`com.fsn.nykaa` is Nykaa **Beauty** and was rejected as a substitute. Sizing and
fit frictions barely exist for beauty, so pooling it would import a different
friction profile and would flatter AJIO on exactly the areas the engine is
built to measure.

**Urbanic** (`com.urbanic`, 87,393 ratings) replaces it on Play. It is
fashion-pure and closest to AJIO in positioning — preferred over higher-volume
but differently-positioned marketplaces (Meesho, Flipkart, Amazon), whose
value/general-marketplace framing brings a different customer segment.

Its low velocity is handled by the common window rather than a count cap: it
contributes roughly 330 reviews in 90 days. That is a small denominator whose
Wilson interval will be wide and may be suppressed by `min_cell_n` — the honest
outcome rather than a hidden one.

**All ids verified live 22 Aug 2026** — Play via title and rating count,
App Store via the iTunes lookup API. AJIO `com.ril.ajio` / `1113425372`,
Myntra `com.myntra.android` / `907394059`, Nykaa Fashion `1439872423`.

**AMENDED 22 Aug 2026 — Urbanic is empty; Meesho declined; the Play pool is
Myntra alone.**

Urbanic returned **8 English reviews in the 90-day window**, spanning 54 days.
The earlier ~330 estimate extrapolated from its 3-year average and was wrong:
Urbanic's volume was front-loaded and its review flow has since collapsed (5 in
August, 1 in July, 2 in June). Play's ordering was verified strictly
newest-first, so this is the app, not the collector.

At n=8 the Wilson interval spans most of [0,1]. The window-parity gate excludes
it from the pool automatically (54d vs 89d), and it is retained only for
verbatims and severity.

Meesho was offered as a replacement and **declined**. So on Play the "pooled
competitor proportion" is a single brand, Myntra. That is a real weakening of
03 §5.3, which calls the differential the engine's most credible output, and it
must be stated rather than implied: the Play comparison is AJIO vs Myntra, not
AJIO vs a category.

The App Store still carries two comparators — but only Nykaa Fashion is
window-compliant there, since Apple's ~500-row pagination ceiling means Myntra's
iOS feed reaches back just 3.2 days against AJIO's 88.2.

Net: **AJIO vs Myntra on Play, AJIO vs Nykaa Fashion on the App Store.** Two
single-comparator differentials on structurally different surfaces. Where they
agree, that agreement is worth more than either alone; where they disagree, the
disagreement is a finding about the surfaces.

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

### D6 · Reddit and YouTube cut
**Settled:** 22 Aug 2026

Both cut before collection. `sources.yaml` sets `enabled: false` for each.

**What is lost, stated plainly:** these were the only two sources carrying
closure and intent language — the places where people narrate the part of the
decision where they *didn't* buy. Losing them means **OA-09 (cannot choose
between similar options) loses its main evidence surface**, which reinforces
rather than changes its `weak` detectability grade: it was already gated and
will remain so.

The engine keeps Play (3 brands), the App Store (3 brands), PDP Q&A and the
complaint aggregators. That combination still supports the reference source,
the differential and the full S6 validation — which 04 §8 names as everything
the deck actually needs.

Neither source was denominator-eligible, so no prevalence figure changes.

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

### B10 · Common time window replaces equal per-brand counts
**Settled:** 22 Aug 2026 · **Departs from 04 §2.1, deliberately**

04 §2.1 mandates equal per-brand caps so that proportions are not "measured on
differently-deep slices of the review timeline". Measured on 22 Aug 2026, an
equal 4,000-review cap produces:

| Brand | Reviews | Window |
|---|---|---|
| AJIO | 4,000 | 40 days |
| Myntra | 4,000 | 7 days |
| Urbanic | 4,000 | 1,094 days |

Review velocity differs by roughly 100×, so **equal counts produce wildly
unequal windows** — precisely the contamination the rule exists to prevent.
Comparing AJIO's last 40 days against Urbanic's last three years spans different
app versions, pricing regimes and festive seasons.

Equal counts is the wrong instrument for the spec's own goal. What must match
across brands is the **period**. Unequal n is harmless: a proportion's
denominator is its own n, and both the Wilson interval and the two-proportion
z-test handle unequal n natively.

**The rule is now:** collect everything inside a common 90-day window, then
randomly downsample within it to bound the classification budget. The
downsample must be random — taking the newest n would silently reintroduce
unequal windows.

90 days was chosen so a single sale event cannot dominate, while keeping the
Myntra pull tractable (~51k reviews).

**T10 is restated accordingly:** it now asserts window parity within 3 days
rather than count parity within 10%. `check_window_parity()` also fails loudly
if any brand hit the scrape safety ceiling, because that truncates its window.

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
