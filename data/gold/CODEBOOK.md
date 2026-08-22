# Codebook for data/gold/gold_sheet_TEMPLATE.csv

Fill four columns. Use these EXACT values - the taxonomy is closed, and a
label outside it cannot be scored against classifier output.

Validate before use:  python -m src.cli validate check-gold

Worked examples on real rows: docs/labelling_guide.md

## opportunity_area  (use the CODE, e.g. `OA-04`)

| Code | Name | Definition |
|---|---|---|
| `OA-01` | Size unavailable in wanted variant | The item is listed but the speaker's size is not purchasable. |
| `OA-02` | Item sold out or delisted after saving | The item was available when saved and is gone, or removed from the list, when the speaker returns. |
| `OA-03` | Size inconsistency across brands — cannot predict fit | The speaker cannot tell what size to order because sizing varies by brand or listing. |
| `OA-04` | Quality vs. imagery mismatch | What arrived, or what the speaker fears will arrive, does not match the product photography. |
| `OA-05` | Fabric / material uncertainty | The speaker cannot establish what the item is made of, or how it will feel or wear. |
| `OA-06` | Return process friction | Initiating or completing a return is hard: pickup failures, window disputes, process opacity. |
| `OA-07` | Refund delay or dispute | Money owed is late, partial, or contested after a return. |
| `OA-08` | Exchange unavailable or hard | The speaker wanted a different size or colour and could not simply swap. |
| `OA-09` | Cannot choose between similar options | The speaker is weighing several comparable items and does not resolve.  **[gated - still label it if it fits]** |
| `OA-10` | Forgot / never came back | The speaker saved an item and did not return to it.  **[gated - still label it if it fits]** |
| `OA-11` | Delivery timing uncertainty | The speaker cannot establish when the item will arrive, or it arrived later than promised. |
| `OA-12` | Checkout / payment failure | The order did not complete: payment declined, cart emptied, checkout errored. |
| `none` | No opportunity area | The utterance carries no friction from the list above. |

`none` is a correct answer. Do not stretch an utterance to fit a code.

## temporal_stance

| Value | Meaning |
|---|---|
| `pre_purchase` | speaker has not yet bought this item; includes all questions asked before ordering and all thinking-about / about-to / still-deciding language |
| `at_purchase` | speaker is in checkout or payment |
| `post_purchase` | speaker has received or is awaiting an order |
| `unclear` | do not guess; unclear is a correct answer |

This is the field the whole engine rests on. `unclear` is a correct answer -
do not guess.

## severity  (1, 2 or 3 - a number, not high/medium/low)

| Level | Meaning |
|---|---|
| `1` | mild friction, speaker proceeded anyway |
| `2` | friction that changed what the speaker did |
| `3` | speaker abandoned, or states they will not use the service again |

Leave blank when opportunity_area is `none`.

## hesitation_marker  (`true` or `false`)

`true` only where the speaker is visibly weighing, deferring or abandoning a
purchase in the text itself - not merely dissatisfied. If every row is
`false`, kappa on this field is undefined, so read the text for it rather
than defaulting.

## When done

Save as `data/gold/human_labels.csv`, then:

```bash
python -m src.cli validate check-gold
```