# LiveAuctioneers Sitemap / URL Discovery

Technical notes for LiveAuctioneers.com URL discovery used by `html_downloader` auction crawls.

## robots.txt

- **URL:** `https://www.liveauctioneers.com/robots.txt`
- **Sitemaps:**
  - `https://www.liveauctioneers.com/sitemap-index.xml.gz` (general site — not used)
  - `https://www.liveauctioneers.com/price-result-sitemap-index.xml.gz` (**primary**)
- **Do not use:** `/search?` (disallowed; API behind AWS WAF)

## Sitemap index

Seed: `https://www.liveauctioneers.com/price-result-sitemap-index.xml.gz` (gzipped `sitemapindex`).

| Child | Role | Approx size |
|-------|------|-------------|
| `price-result-sitemap-*.xml.gz` | SEO sold-lot pages | ~25k children × up to ~50k `/price-result/{slug}/` each |

Discovery expands pending child sitemaps until **`--min-urls`** unique
`/price-result/` URLs are collected (default **100000**), or **`--max-sitemaps`**
children have been attempted (default **2000**). Early alphabetical shards are
often tiny (tens–thousands of lots), so a fixed small sitemap count under-fills.

Resume: `state/auctions/liveauctioneers_sitemap_progress.json`.

## URL patterns

```text
price-result (crawl target):  /price-result/{slug}
canonical item (parse later): /item/{itemId}_{slug}
```

Canonical host: `https://www.liveauctioneers.com`.

## Imperva / User-Agent

LiveAuctioneers serves full SSR HTML (with `window.__data`) to SEO crawler User-Agents
(Googlebot / bingbot). A normal Chrome UA receives an Imperva JS-challenge stub.

- **Discover:** httpx + rotating bot UAs + optional proxies
- **Download:** `impersonate=none` + Googlebot headers; soft-block if body lacks `window.__data`

## Coverage

- Historical sold lots only (price-result archive). No Algolia equivalent.
- Full archive requires many runs with progress resume after each min_urls batch.
- Lot field extraction stays out of html-downloader (HTML download only).

## Commands

```bash
python -m html_downloader auction discover \
  --auction-house liveauctioneers --proxy-file proxy.txt \
  --min-urls 100000 --incremental --update-state

# Smoke / smaller batch
python -m html_downloader auction discover \
  --auction-house liveauctioneers --proxy-file proxy.txt \
  --min-urls 5000 --max-sitemaps 100

python -m html_downloader auction download \
  --auction-house liveauctioneers --proxy-file proxy.txt --skip-existing
```
