# How to label the gold sheet

Worked examples using real rows from `data/gold/gold_sheet_TEMPLATE.csv`.
Read alongside `data/gold/CODEBOOK.md`, which has the full code list.

**Why this is your hour and not mine.** These 100 labels are the measuring
stick. Human-vs-model κ is what decides whether any number reaches a slide, and
if I produce the labels then a model is checking a model — the statistic
measures nothing. That is the same reason lane C runs on a different vendor.

---

## The mechanics

Open `data/gold/gold_sheet_TEMPLATE.csv` in Excel or Google Sheets. Fill four
columns, leave everything else alone, save as **`data/gold/human_labels.csv`**,
then:

```bash
python -m src.cli validate check-gold
```

That refuses any value outside the frozen taxonomy, so a mis-typed code is
caught immediately rather than at κ time.

| Column | Values |
|---|---|
| `opportunity_area` | `OA-01`…`OA-12`, or `none` |
| `temporal_stance` | `pre_purchase`, `at_purchase`, `post_purchase`, `unclear` |
| `severity` | `1`, `2`, `3` — blank when the area is `none` |
| `hesitation_marker` | `true` / `false` |
| `labeller_note` | free text — use it whenever you hesitated |

---

## The three rules that decide most rows

**1 · `none` is a correct answer.** The taxonomy is closed and covers twelve
specific frictions. Plenty of real complaints fall outside it — wrong item
shipped, rude agent, app bug. Those are `none`. Do not stretch an utterance to
fit a code; a stretched label is worse than no label, because it moves a count.

**2 · `unclear` is a correct answer.** Only mark a stance the text actually
supports. This is the field the whole engine rests on, so a guess here is
expensive.

**3 · Read the definition, not the label name.** OA-04 is not "bad quality" —
it is quality *versus imagery*. The name is a handle; the definition is the
rule.

---

## Worked examples

### 1. "I will order five dress but I received only one one dress"

| | |
|---|---|
| `opportunity_area` | **`none`** |
| `temporal_stance` | `post_purchase` |
| `severity` | *(blank)* |
| `hesitation_marker` | `false` |

Ordering five and receiving one is a fulfilment error. Check the twelve: not
sizing, not quality-vs-imagery, not returns, not refund, not delivery *timing*
(it arrived), not checkout. **Incomplete orders are not in the taxonomy.** So
`none` — and this is the commonest reason for `none`, not because the complaint
is trivial but because the taxonomy does not cover it.

Stance is `post_purchase`: they received something.

### 2. "Horrible return policy and terrible customer experience"

| | |
|---|---|
| `opportunity_area` | **`OA-06`** |
| `temporal_stance` | `post_purchase` |
| `severity` | `2` |
| `hesitation_marker` | `false` |

Return process friction, straightforwardly. Stance is the judgement call:
"terrible customer experience" implies they went through it, so
`post_purchase` — but `unclear` is defensible, because they never say they
returned anything. **Put that in `labeller_note`.** Rows you hesitated on are
the most informative ones when κ comes back low.

Severity 2, not 3: strong language, but no statement that they abandoned or
will not return. Reserve 3 for "never ordering again".

### 3. "What rubbish thing is this"

| | |
|---|---|
| `opportunity_area` | **`none`** |
| `temporal_stance` | `unclear` |
| `severity` | *(blank)* |
| `hesitation_marker` | `false` |

Pure frustration with no identifiable friction and no purchase position. Both
fields take their "correct non-answer" value. Rows like this are why the
denominator is bigger than the sum of the areas.

### 4. "My 2000 rs stucked in Ajio wallet."

| | |
|---|---|
| `opportunity_area` | **`OA-07`** |
| `temporal_stance` | `post_purchase` |
| `severity` | `2` |
| `hesitation_marker` | `false` |

Money owed and not accessible — refund delay or dispute. The amount is
concrete and unresolved, which is severity 2: it changed what they could do.
It becomes 3 only if they say they are done with the service.

### 5. "Worst cheap products." — a genuinely borderline row

| | |
|---|---|
| `opportunity_area` | **`none`** *(defensible: `OA-04`)* |
| `temporal_stance` | `post_purchase` |
| `severity` | *(blank)* |
| `hesitation_marker` | `false` |

OA-04's definition is "does not match the product photography". This utterance
is a bare quality verdict with no reference to what was shown or expected.
Under rule 3 that is `none`.

**But calling it OA-04 is defensible**, on the reading that "worst" implies a
gap against expectation. Either is fine — what matters is that you **decide
once and apply it to all 100 the same way**. Inconsistency here shows up as low
κ on `opportunity_area`, and the fix would be a prompt change and a full re-run,
so it is worth being deliberate. Note your choice in `labeller_note`.

### 6. "Never opens when i click on sony headphones."

| | |
|---|---|
| `opportunity_area` | **`none`** |
| `temporal_stance` | `pre_purchase` |
| `severity` | *(blank)* |
| `hesitation_marker` | `false` |

An app defect, not a shopping friction — and specifically **not** OA-12, which
is checkout and payment failure, not a product page failing to open.

Stance is `pre_purchase`: they are browsing an item they have not bought. This
is one of the few app-store rows that genuinely sits pre-purchase, which is
exactly why the stance field is worth reading carefully rather than defaulting
to `post_purchase` on an app review.

### 7. "Surprisingly, even after waiting for 2 days, the order was not delivered."

| | |
|---|---|
| `opportunity_area` | **`OA-11`** |
| `temporal_stance` | `post_purchase` |
| `severity` | `1` |
| `hesitation_marker` | `false` |

Delivery timing, clearly. Severity 1: they are surprised and still waiting, but
nothing says the delay changed what they did. Severity 2 would need something
like "so I cancelled".

### 8. "I hope no other customer has to face a similar experience"

| | |
|---|---|
| `opportunity_area` | **`none`** |
| `temporal_stance` | `post_purchase` |
| `severity` | *(blank)* |
| `hesitation_marker` | `false` |

A sentence fragment referring to an experience described elsewhere in the
review. Segmentation split it off, so on its own it names no friction. Label
what is **in front of you**, not what you infer the parent review said —
the classifier sees only this text too, and κ compares like with like.

---

## `hesitation_marker`

`true` only where the speaker is visibly weighing, deferring, or abandoning a
**purchase decision** in the text itself. Being angry is not hesitation.

- "Thinking about buying but not sure if sizes run small" → `true`
- "Added to wishlist, waiting for the sale" → `true`
- "lena chahiye ya nahi" → `true`
- "Worst app, terrible quality" → `false`

The last gold set had `false` on all 100 rows. If that happens again, κ on this
field is undefined — a constant column agrees with everything by chance — so
read for it rather than defaulting.

---

## Severity, in one line each

| | |
|---|---|
| `1` | Annoyed, carried on anyway |
| `2` | It changed what they did — cancelled, returned, bought elsewhere |
| `3` | They gave up on the service, or say they will not come back |

---

## If you are unsure

Use `labeller_note` and move on. Do not agonise: the disagreement between your
label and the model's is the *signal* S6 is built to measure, and a row you
found genuinely ambiguous is more useful flagged than silently forced into a
code.
