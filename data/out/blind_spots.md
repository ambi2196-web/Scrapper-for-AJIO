# Blind spots

_Generated 2026-08-24T21:03:44+05:30 by S9. Read alongside `opportunity_index.csv`._

This register exists because the boundary of the method is itself a finding.
An area listed here is not an area that does not matter — it is an area the
public record cannot adjudicate, and therefore one where a number would be a
guess wearing a decimal point.

## 1. Gated by detectability

These areas carry `oi = null` in the index. The gate is applied before
aggregation, so no value was computed and none can leak into a slide.

| Area | Name | Grade | Why public text cannot adjudicate it |
|---|---|---|---|
| OA-09 | Cannot choose between similar options | weak | Occasional "couldn't decide between", mostly in Reddit and YouTube comments rather than reviews. GATED — no number is produced. A review is written after an event; not-deciding is not an event.
 |
| OA-10 | Forgot / never came back | none | Almost no one narrates their own forgetting. GATED — this is the silent non-event at the centre of the whole problem, and the single most important row on the blind-spot register. Interviews and product analytics only.
 |

## 2. In the taxonomy, absent from the corpus

_Every adjudicable area in the taxonomy is represented in the corpus._

## 3. Sources that cannot carry a rate

Reddit, YouTube, the complaint aggregators and X are marked
`denominator_eligible: false`. They supply mechanism and verbatims. Their
selection bias is structural rather than sizeable — people arrive at a
complaint site because they have a complaint — so a proportion computed
from them would describe the venue, not the brand. S7 refuses them in code.

## 4. Other gates triggered in this run

| Reason | Rows |
|---|---|
| not adjudicable from public text | 8 |
