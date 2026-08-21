# Decision log

Every open decision, its status, and the date it was settled. An evaluator may
ask about any of these, and "we decided X on date Y for reason Z" is an answer
where a shrug is not.

Decisions carried from `03_engine_spec.md` are D1–D5. Decisions made during the
build are B1 onward.

---

## Open — these block work

### D1 · Category scope for PDP sampling
**Status:** OPEN · **Blocks:** PDP collection · **Settle by:** before Phase 2b

Which AJIO categories the product sample is drawn from. Narrow once, and write
down why.

The choice is load-bearing rather than administrative: category determines the
base rate of nearly every friction being measured. Sizing anxiety behaves
differently in footwear than in loose-fit ethnic wear; stock volatility behaves
differently in fast-moving categories than in staples. A category set chosen
after seeing which frictions look interesting would be a result dressed as a
sampling frame.

**Record here:** the categories, the reason, and the date.

---

### D3 · PDP terms-of-service approach
**Status:** OPEN · **Blocks:** PDP collection · **Settle by:** before Phase 2b

The engine's current position, implemented in `src/collect/base.py`:

- Public, unauthenticated pages only
- `robots.txt` checked per origin and honoured; an **unreachable** `robots.txt` is
  treated as a disallow rather than as permission
- Human-rate request timing, 2 s base with jitter
- No logged-in scraping, no session tokens, no CAPTCHA circumvention
- No storage of reviewer usernames or other personal identifiers

**Confirm or amend this, then record the decision and its date.** "We used only
publicly accessible pages at human request rates, and honoured robots.txt" is a
complete answer to the question an evaluator will actually ask.

---

### D5 · `wishlist_proximity` values
**Status:** OPEN · **Blocks:** S5 via invariant I8 — and therefore the entire
classification day

How close each opportunity area sits to the wishlist → purchase decision moment.
It multiplies into the opportunity index, so it determines the ranking the deck
leads with.

This is a judgement, not a measurement, and the honest way to handle a judgement
is to commit it before seeing what it produces. `config/proximity.yaml` records a
`frozen_at` timestamp and a git sha; `src/config.py` compares the file's mtime
against it and S5 refuses to start if the file was touched later. Tuning
proximity after seeing classification output would be a rationalised read, and
the mtime check makes that impossible rather than merely discouraged.

**Settle, send for a pressure-test, then `python -m src.cli freeze-proximity` and
commit.**

---

## Settled

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
