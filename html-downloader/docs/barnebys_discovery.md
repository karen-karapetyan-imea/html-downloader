# Barnebys Sitemap / Search Discovery

Technical notes for [Barnebys.com](https://www.barnebys.com/) URL discovery used by
`html_downloader` auction crawls.

## robots.txt

- **URL:** `https://www.barnebys.com/robots.txt`
- Header notice: crawling is prohibited without express written permission.
- `User-agent: *` → `Disallow: /` (plus date-pattern rules).
- Major search bots additionally Disallow `/realized-prices/lot` (locale variants).
- Product/legal review advised before large-scale production crawls.

## Discovery sources

### 1. Sitemap index (PRIMARY — live lots)

Seed: `https://www.barnebys.com/sitemap.xml`

| Child | Role | Approx size (2026-09 probe) |
|-------|------|-----------------------------|
| `sitemap/lots-0.xml.gz` … `lots-7.xml.gz` | Live lot URLs | ~50k × 7 + ~36k ≈ **386k** |
| `sitemap/keywords.xml.gz` | Keyword landing pages | ~3.5k (not crawled) |
| `sitemap/pages.xml.gz` | Static / blog / house hubs | ~12k (not crawled) |

Lot children list **only** `/auctions/lot/{slug}-{token}-{id}` URLs (no `lastmod`).

### 2. Search API + pricebank HTML (SECONDARY — art + past)

Public Algolia `applicationId` / search-only key are **not** in frontend JS.
InstantSearch talks to Barnebys indexes named `search` (live) and
`search-pricebank` (~73M past results in SSR), but anonymous clients cannot
pass `indexName` to a public Algolia host.

Instead, discover uses:

| Source | Index / surface | How |
|--------|-----------------|-----|
| `GET /api/search?query=&page=&perPage=` | Live `search` | Art query shards; ~80 unique pages/query |
| SSR `GET /realized-prices/{category}?p=&c=&px-min=&px-max=` | Pricebank | Art categories; hard cap **312** pages/partition |

**Art categories (path slugs):** `arts-and-graphics`, `contemporary-art`,
`asian-art`, `ancient-art`, `sculptures`, `photographs`, `works-of-art`,
`folk-art`, `ethnographic`.

When a realized category hits the 312-page cap, discovery **adaptively** queues
country (`c=`) then price (`px-min`/`px-max`) child partitions.

Live hits expose `uid` (`slug-token-id`) and `category`; art-only mode keeps
hits whose category id is in the art set (or art-like `categoryName`).

## URL patterns

```text
live (sitemap/API):  /auctions/lot/{slug}-{token}-{numericId}
live (alt):          /auctions/lot/{numericId}/{slug}
realized twin:       /realized-prices/lot/{same-suffix-as-live}
```

Canonical host: `https://www.barnebys.com`.

Sitemap discovery **default:** for each live lot, also emit a realized-prices twin
(`entity_type=result_lot`, same numeric id). Search/pricebank expansion emits
both live + realized for each discovered id.

## Bot protection

Azure WAF JS challenge (`.azwaf`, `afd_azwaf_tok`) on some paths. Discovery uses
`curl_cffi` Chrome impersonation and stealth proxies (`uses_stealth_proxy=True`).
Download block detector also matches `azwaf` / `azure waf` / `afd_azwaf`.

## CLI

```bash
# Discover: sitemap + art search/pricebank (expand_algolia default on)
.venv/bin/python -m html_downloader auction discover \
  --auction-house barnebys --proxy-file proxy.txt \
  --max-sitemaps 100 --min-urls 0 \
  --incremental --update-state

# Artworks-focused state file (same search expander, separate resume state)
.venv/bin/python -m html_downloader auction discover \
  --auction-house barnebys --proxy-file proxy.txt \
  --algolia-artworks-only \
  --incremental --update-state

# Sitemap only (skip search/pricebank)
.venv/bin/python -m html_downloader auction discover \
  --auction-house barnebys --proxy-file proxy.txt \
  --no-expand-algolia \
  --min-urls 0 --max-sitemaps 100

# Download
.venv/bin/python -m html_downloader auction download \
  --auction-house barnebys --proxy-file proxy.txt --skip-existing

# Monthly loop
./scripts/run_monthly_auction.sh barnebys
```

Conservative first download: `--rps 1`–`2`, `--workers 4`–`8`.

## State files

| Path | Role |
|------|------|
| `state/auctions/barnebys.json` | lastmod / entity checkpoint |
| `state/auctions/barnebys_sitemap_progress.json` | child sitemap resume |
| `state/auctions/barnebys_sitemap_progress_lots.jsonl` | sitemap URL archive |
| `state/auctions/barnebys_algolia_browse_state.json` | search/pricebank resume |
| `state/auctions/barnebys_algolia_browse_state_lots.jsonl` | search/pricebank archive |
| `state/auctions/barnebys_algolia_artworks_browse_state*.json(l)` | artworks-only resume |

## Coverage limits (important)

- Sitemap ≈ **386k** current live lots (+ realized twins).
- Pricebank SSR reports ~**73M** past lots, but each filter partition is capped
  at **312 × ~29 ≈ 9k** URLs. Full archive parity with Invaluable Algolia browse
  is **not** available without a public pricebank API / Algolia key.
- Adaptive country × price splits increase art past-lot coverage substantially
  beyond a single category crawl; expect multi-hour resumed runs.
- `/api/search` ignores `indexName=search-pricebank` (always live index).
- Realized twins / search-built live URLs may 404 for expired listings.
- WAF / rate limits — keep discovery concurrency at 1.

## OUT OF SCOPE

- Locale TLDs (`.co.uk`, `.de`, `.se`, …)
- `/api/auction_houses/` (robots Disallow)
- Magazine / Barnepedia / keyword landings
