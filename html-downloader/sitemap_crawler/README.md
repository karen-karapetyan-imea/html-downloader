# 1stDibs Art HTML Sitemap Crawler

Crawls the **art-only** human-readable HTML sitemap branches on [1stDibs](https://www.1stdibs.com/) and extracts deduplicated artwork, dealer, and creator URLs.

This is **not** an XML sitemap scraper and does **not** download entity pages.

## Seeds

The crawler starts from (and only follows nested pages under):

- `https://www.1stdibs.com/sitemap/art/items/`
- `https://www.1stdibs.com/sitemap/art/dealers/`
- `https://www.1stdibs.com/sitemap/art/creators/`

## Setup

From the `html-downloader` project root:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Run

```bash
python -m sitemap_crawler
```

### Options

| Flag | Default | Description |
|------|---------|-------------|
| `--delay` | `0.5` | Seconds between request batches |
| `--concurrency` | `10` | Concurrent sitemap page fetches |
| `--timeout` | `30` | Request timeout (seconds) |
| `--output-dir` | `output` | URL list output directory |
| `--state-dir` | `state/sitemap_crawler` | Resume state directory |
| `--reset-state` | off | Delete state and start fresh |
| `--max-pages` | unlimited | Stop after N art sitemap pages (testing) |
| `--log-level` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR` |

Example:

```bash
python -m sitemap_crawler --concurrency 10 --delay 0.5
```

Subset test run:

```bash
python -m sitemap_crawler --max-pages 100 --reset-state
```

## What gets extracted

| Entity | URL pattern | Dedup key |
|--------|-------------|-----------|
| Artwork | `/art/.../id-a_{numeric_id}/` | numeric ID |
| Dealer | `/dealers/{slug}/` (canonical only) | dealer slug |
| Creator | `/creators/{slug}/`, `/creators/{slug}/art/...` | creator slug |

Excluded:

- Dealer `/shop/` subpages
- Creator vertical hubs (`jewelry`, `fashion`, `furniture`)
- Non-English locale paths (`/fr/`, `/de/`, `/it/`, `/es/`)
- Sitemap and search URLs

## Resume

State is saved in `state/sitemap_crawler/`:

- `visited_sitemaps.txt` — sitemap pages already crawled (preserved across upgrades)
- `pending_sitemaps.txt` — art sitemap pages still to crawl
- `artwork_urls.txt`, `dealer_urls.txt`, `creator_urls.txt` — deduplicated entity URLs for resume

State is checkpointed every 50 sitemap pages and on graceful shutdown (Ctrl+C). Re-run the same command to continue.

On first run after upgrading from the full-site crawler, pending is filtered to art branches only and dealers are recovered from legacy `discovered_urls.txt` when entity state files do not yet exist.

## Output

Written to `--output-dir` (default `output/`):

- `artwork_urls.txt` — one artwork URL per line
- `dealer_urls.txt` — one dealer URL per line
- `creator_urls.txt` — one creator URL per line
- `all_art_urls.txt` — union of all three, sorted

No metadata is written to these files.

## Progress

While crawling, statistics are logged periodically:

```
Art sitemap pages visited: 12345
Pending sitemap pages: 5432

Artwork URLs: 168432
Dealer URLs: 3312
Creator URLs: 9738

Total unique URLs: 181482
```

## HTTP stack

- Primary: `curl_cffi` with Chrome TLS impersonation
- Fallback: `httpx` with browser-like headers
- Retries with exponential backoff (3 attempts)
- Does **not** bypass CAPTCHA or authentication challenges

## Expected runtime

The art items branch alone can require crawling on the order of **hundreds of thousands to ~1M** sitemap pages. At `--concurrency 10 --delay 0.5`, expect a long-running job. Increase `--delay` if you see rate limiting or errors.
