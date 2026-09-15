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

Discovery expands pending child sitemaps until **`--min-urls`** unique **new**
`/price-result/` URLs are collected this run (default **1000000**), or **`--max-sitemaps`**
children have been attempted (default **5000**). Dense shard basenames (e.g. `*-0.xml.gz`)
are preferred before sparse early alphabetical shards. If the durable JSONL is far
behind `lots_found` on already-`done` children (upgrade gap), those children are
re-queued so the cache can catch up — same idea as Invaluable’s Algolia JSONL archive.

Resume: `state/auctions/liveauctioneers_sitemap_progress.json`.

## Durable lot cache (source of truth)

Like Invaluable Algolia, each finished child appends new rows to:

`state/auctions/liveauctioneers_sitemap_progress_lots.jsonl`

Row shape: `{"entity_id", "url", "lastmod"}`. Discover returns only **this-run new**
entries; the auction service streams the full JSONL into the monthly job’s
`sitemap_all.txt` / `urls.txt`. Full archive coverage requires many runs with progress
resume — the job always sees the cumulative cache, not just the latest batch.

Reset progress + cache: `--sitemap-force`.

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
  --min-urls 1000000 --incremental --update-state

# Smoke / smaller batch
python -m html_downloader auction discover \
  --auction-house liveauctioneers --proxy-file proxy.txt \
  --min-urls 5000 --max-sitemaps 100

# Rebuild progress + JSONL from scratch
python -m html_downloader auction discover \
  --auction-house liveauctioneers --proxy-file proxy.txt \
  --sitemap-force --min-urls 5000 --max-sitemaps 100

# Chunked download (preferred once urls.txt is large)
./scripts/download_url_chunks.sh liveauctioneers

python -m html_downloader auction download \
  --auction-house liveauctioneers --proxy-file proxy.txt --skip-existing
```
