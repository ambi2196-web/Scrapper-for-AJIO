# Capturing the AJIO PDP review / Q&A endpoint

The highest-value and highest-risk source in the build. Budget three hours; if it
is not working by then, switch to the Playwright fallback (step 5) and move on.
The source matters far more than the method of getting it.

**Do not guess the endpoint from any spec, including this file.** AJIO's product
page is a client-rendered app and its payload paths change. A guessed endpoint
returns a 404 — or worse, a 200 with a different shape, which parses into
plausible-looking rows that are silently wrong.

---

## 1 · Capture the review / Q&A request

1. Open a real AJIO product page in Chrome.
2. DevTools → **Network** → filter **Fetch/XHR** → tick **Preserve log**.
3. Reload, then scroll to the reviews section and click through to page 2 of the
   Q&A. Pagination is what reveals the page/size parameters.
4. Find the request whose response contains review or question text. Sort by
   response size — it is usually among the largest JSON responses.
5. Right-click it → **Copy → Copy as cURL**.

Record from that request:

| | |
|---|---|
| Path template | e.g. `/api/.../{product_code}/reviews?page={page}&size={size}` |
| Product code | where it appears in the path or query |
| Pagination params | names and 0- vs 1-indexing |
| Required headers | `Accept`, any `x-*` app headers, locale |
| Cookie dependence | **test this** — see step 3 |

---

## 2 · Find the payload shape

In the response preview, note which key holds the array — `questions`,
`reviews`, `content`, `data`, `results`. `src/collect/pdp.py::_extract_rows`
already probes those names and logs `unrecognised_payload` with the top-level
keys when none match, so a shape change surfaces as a log line rather than as
zero rows.

Note the per-item field names for: text, id, date, rating, helpful count, and —
for Q&A — the seller's answer. Map them in `_to_envelope`.

---

## 3 · Test it unauthenticated

```bash
curl -s -H "Accept: application/json" \
     -H "User-Agent: ajio-engine/0.1 (research; contact via repo issues)" \
     "<THE URL>" | head -c 800
```

**If this works without cookies, you are done** — the source is public and
unauthenticated, which is exactly what decision D3 commits to.

If it only works with a session cookie, **do not replay the cookie.** That is
logged-in scraping and it is outside the D3 boundary. Go to step 5.

---

## 4 · Wire it up

`config/sources.yaml`:

```yaml
  pdp_qa:
    mode: "xhr"
    endpoint: "https://www.ajio.com/api/.../{product_code}/questions?page={page}&size={size}"
    headers:
      Accept: "application/json"
      # any x-* headers the capture showed as required
```

Then the sampling frame — the part that is a methodology decision rather than a
scraping detail.

**Do not scrape whatever is on the homepage or the top of a category listing.**
Homepage placement correlates with promotion, promotion correlates with price and
stock behaviour, and stock behaviour is one of the things being measured — so a
homepage sample would build the finding into the sampling frame. Top-sorted
listings have the same problem: popular items have systematically different stock
behaviour from the tail.

Stratify across the **rating distribution** and the **review-count
distribution** within each category in scope (D1). Two ways to populate it:

- **Preferred:** capture the category *listing* endpoint too, crawl it recording
  each product's rating and review count, then bucket and sample.
- **Interim:** hand-assemble `config/pdp_products.yaml` with `product_code`,
  `category`, `rating_band` and `review_count_band` per entry, and point
  `_build_frame` at it.

Hand-assembly is acceptable **provided the frame is recorded** — it is an
appendix table either way, and `logs/pdp_sampling_frame.jsonl` is written on
every run.

---

## 5 · Fallback: Playwright

```yaml
  pdp_qa:
    mode: "playwright"
```

```bash
pip install playwright && playwright install chromium
```

Renders the page, waits for `networkidle`, scrolls to trigger lazy loading, then
parses the DOM. Five to ten times slower — acceptable for a few hundred products
— and immune to endpoint drift.

Fill `_parse_dom` with selectors from a live page. Take them from DevTools →
Elements, and prefer stable-looking attributes over generated class names.

**Switch to this without hesitation once the time-box expires.** A slow collector
that works beats a fast one that does not exist.

---

## 6 · Verify before trusting it

```bash
python -m src.cli collect pdp_qa --cap 50
python -m src.cli verify-raw
head -3 data/raw/pdp_qa/ajio/*.jsonl
```

Check by eye:

- `raw_text` is real question text, not boilerplate or a template string
- `source_id` is stable — re-run and confirm `written: 0, skipped_duplicate: 50`
- `posted_at` parses, or is honestly null
- the seller's answer is in `meta.answer_text`, **not** in `raw_text` — an answer
  is the brand speaking, not a shopper, and letting it into the utterance stream
  would pollute every denominator

---

## Why this source earns three hours

Every other surface in this engine is retrospective. A review is written by
someone who already bought; their account of what made them hesitate is a
reconstruction, and reconstructions are systematically tidier than the decision
they describe.

A question on a product page is a person hesitating in public, in the present
tense, before deciding. It is the only public text that is pre-purchase **by
construction** rather than by classification — which means `temporal_stance` is
corroborated by the surface itself rather than inferred from wording.

That is why it is the reference source for the opportunity index, and why, if
time collapses, the instruction is: **take Q&A, skip PDP reviews.**
