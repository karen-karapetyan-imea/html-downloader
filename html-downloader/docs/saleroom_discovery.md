# The Saleroom Algolia Discovery

Technical notes for the-saleroom.com URL discovery used by `html_downloader` auction crawls.

## Why Algolia

XML lot sitemaps (`lots_sitemapindex` / `sitemap-lots*.xml.gz`) return **HTTP 405** from many clients and proxies in this environment. HTML `/for-sale/` browse is gated by AWS WAF. Discovery therefore uses the public InstantSearch index already embedded on the homepage.

## Algolia (primary — only active path)

| Field | Value |
|-------|-------|
| App ID | `2125HT9M59` |
| Search API key | `b1f48e75f522cc67ff7751f1abd75458` (search-only; **browse returns 403**) |
| Index | `lots_sr_en` |
| Endpoint | `POST …/1/indexes/lots_sr_en/query` |

**Master category filters** (art + collectables family):

| Code | Category |
|------|----------|
| `FIA` | Fine Art |
| `DEA` | Decorative Art |
| `AA` | Asian Art |
| `ETA` | Ethnographica & Tribal Art |
| `COL` | Collectables |
| `GRE` | Greek / Roman / Egyptian antiquities |

Pagination: `hitsPerPage=100`, walk pages until empty. If one master filter exceeds the soft page ceiling (~50k), sub-split by `countryName`, then by `auctioneerName` when a country is still too large.

Lot URL template (from page config):

```text
/en-gb/auction-catalogues/{auctioneerRef}/catalogue-id-{auctionRef}/lot-{objectID}
```

Canonical host: `https://www.the-saleroom.com`. Locale forced to `en-gb`.

## State / job files

| Path | Role |
|------|------|
| `state/auctions/saleroom_algolia_browse_state.json` | Partition page checkpoints |
| `state/auctions/saleroom_algolia_browse_state_lots.jsonl` | Durable lot URL cache |
| `data/auctions/saleroom/{YYYY-MM}/urls.txt` | Crawl list for the month |
| `data/auctions/saleroom/{YYYY-MM}/art_urls.txt` | Same set (Algolia is art-scoped) |

Registry placeholder index: `algolia:lots_sr_en` (no XML fetch).

## Coverage notes

- Live/searchable inventory only — not `archivelot` / Price Guide history.
- Deduplicate on lot UUID (`objectID`).
- Discover does **not** require `--proxy-file` (Algolia is reached direct).
- Download **does** require `--proxy-file` (AWS WAF on lot HTML). Block keywords include `awswaf`, `human verification`, etc. Proxies that fail the challenge are recorded as `block_detected` / `body_challenge` in `results.jsonl` (HTML not kept).
- The public `/en-gb/for-sale/fine-art` HTML snapshot (~33k) is **not** the same number as Algolia `masterCategoryCode:FIA` `nbHits` (timing, facets, and InstantSearch filters differ). Both are live-only; neither is Price Guide.

## Price Guide / historical (probe: **no-go**)

Probe artifacts: [`output/saleroom_probe/probe_report.json`](../output/saleroom_probe/probe_report.json).

| Finding | Result |
|---------|--------|
| Public Algolia historical / Price Guide index | **None** (only live `lots_sr_en`) |
| Price Guide HTML (`/en-gb/price-guide/...`) | Loads with Googlebot UA; shows **subscription paywall** |
| Art category HTML hit counts (gated UI) | Fine Art ~74k, Decorative ~15k, Asian ~9k, Collectables ~63k, Antiquities ~26k |
| `robots.txt` `*/archivelot*` | **Disallow** — do not crawl |
| Phase C (`saleroom_priceguide` module) | **Not implemented** until an approved authenticated API/index exists |

Do **not** treat homepage “~4M items/year” or “21M+ Price Guide” as the live crawl target. Live art corpus = Algolia masters above.

## Commands

```bash
# Discover (proxy optional for Saleroom)
python -m html_downloader auction discover \
  --auction-house saleroom \
  --incremental --update-state

# Download (proxy required; prefer chunks for large urls.txt)
./scripts/download_url_chunks.sh saleroom

# Art subset (same URLs as full Algolia set)
python -m html_downloader auction download \
  --auction-house saleroom --proxy-file proxy.txt \
  --urls data/auctions/saleroom/YYYY-MM/art_urls.txt --skip-existing

# Price Guide re-probe (readonly)
python output/saleroom_probe/probe.py
```
