# Drouot Discovery

Technical notes for drouot.com URL discovery used by `html_downloader` auction crawls.

## Sources

| Priority | Source | Notes |
|----------|--------|-------|
| PRIMARY | `https://drouot.com/sitemap-en-lot.xml` | Sitemap index → `sitemap-en-lot{1..N}.xml` |
| PRIMARY | `https://drouot.com/sitemap-en-sale.xml` | Sitemap index → `sitemap-en-sale1.xml` |
| FALLBACK | `GET /en/s/__data.json?query=*&page=N` | First-party search (not Algolia). Robots **Disallow** `/en/s/` — use only when sitemap lot count &lt; search `totalItems` |

Probe (2026-09-18): EN lot sitemap ≈ **201,820** URLs; search `query=*` `totalItems` ≈ **202,994**. No public Algolia credentials in frontend JS (SvelteKit).

## robots.txt

- Lot/sale HTML under `/en/l/...` and `/en/v/...` are allowed for `User-agent: *` (Disallow `/l/*` does not match `/en/l/`).
- Search `/en/s?` / `/en/s/` is Disallow — expand is fallback-only via `--expand-algolia` (shared CLI flag).
- Many AI bots are fully Disallow `/`.

## URL templates

```text
https://drouot.com/en/l/{lotId}-{slug}
https://drouot.com/en/v/{saleId}-{slug}
```

Canonical host: `https://drouot.com`. Locale forced to `en`.

## State / job files

| Path | Role |
|------|------|
| `state/auctions/drouot_sitemap_progress.json` | Child sitemap resume |
| `state/auctions/drouot_sitemap_progress_lots.jsonl` | Durable lot + sale URL cache |
| `state/auctions/drouot_algolia_browse_state.json` | Search page checkpoints (when backfill runs) |
| `data/auctions/drouot/{YYYY-MM}/urls.txt` | Crawl list for the month |

## Commands

```bash
# Discover (proxy optional for Drouot)
python -m html_downloader auction discover \
  --auction-house drouot \
  --incremental --update-state

# Skip search backfill
python -m html_downloader auction discover \
  --auction-house drouot --no-expand-algolia

# Download (proxy required)
./scripts/download_url_chunks.sh drouot

# Monthly loop
./scripts/run_monthly_auction.sh drouot
```

## Coverage notes

- EN locale only (fr/de/es/it/zh sitemaps out of scope for v1).
- Deduplicate on numeric lot/sale id.
- Discover does not require `--proxy-file`.
- Download requires `--proxy-file`; Cloudflare challenge keywords are recorded as blocks.
- Search pagination is **1-based** (`page=0` ≡ `page=1`).
