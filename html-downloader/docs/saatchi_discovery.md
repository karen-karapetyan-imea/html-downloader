# Saatchi Art search discovery

Technical notes for saatchiart.com URL discovery used by `html_downloader` marketplace crawls.

## Why search expand

Public artwork/profile sitemaps only list ~**350k** entity URLs (25 child maps). Saatchi’s about page and SSR `/all` browse report ~**1.18M** artworks. Sitemap coverage is a hard ceiling without a search/API expander.

## Phase 0 gate

Probe artifacts: [`output/saatchi_probe/`](../output/saatchi_probe/).

| Check | Result |
|-------|--------|
| Public Algolia app id / search key | **None** (dropped; no usable InstantSearch leftovers) |
| Constructor.io | **Pass** — public key in `_app` / `browse` JS |
| SSR cross-check `/all` | ~1,179,770 total results |

Gate file: [`output/saatchi_probe/PHASE0_GATE.json`](../output/saatchi_probe/PHASE0_GATE.json).

## Constructor (primary)

| Field | Value |
|-------|-------|
| Key | `key_cn3mctZ73MD3U2jM` (search-only; same trust level as auction InstantSearch keys) |
| Host | `https://ac.cnstrc.com` |
| Section | `Products` |
| Endpoint | `GET /browse/{filter_name}/{filter_value}` |
| Extra filters | `filters[name]=value` query params |
| Page size | 100 |
| Hard window | **10,000** results per query (API `total_num_results` and page walk both stop at 10k) |

**Do not trust `total_num_results` for planning.** Use facet option `count` values to decide splits.

### Partition ladder

```text
artwork_category → country → subject → size_bin → us_price_bin → city
```

Seed categories: painting, photography, sculpture, drawing, printmaking, collage, digital, mixed media, installation.

Hit → URL: `data.url` (e.g. `/art/Painting-Title/{artistId}/{artworkId}/view`), validated by `saatchi_entity_from_url`.

## State / job files

| Path | Role |
|------|------|
| `state/saatchi_search_browse_state.json` | Partition page checkpoints |
| `state/saatchi_search_browse_state_lots.jsonl` | Durable artwork URL cache (source of truth) |
| `state/saatchi_lastmod.json` | Sitemap lastmod incremental state |
| `data/saatchi/{YYYY-MM-DD}/sitemap_all.txt` | Full URL list (sitemap ∪ search) |
| `data/saatchi/{YYYY-MM-DD}/urls.txt` | Crawl list (incremental-aware) |

Discover streams the JSONL cache into job files after the sitemap write so million-scale corpora never sit fully in RAM.

## Commands

```bash
# Full discover: sitemap + Constructor expand (default)
python -m html_downloader discover \
  --marketplace saatchi \
  --proxy-file proxy.txt \
  --incremental --update-state

# Sitemap only
python -m html_downloader discover \
  --marketplace saatchi \
  --proxy-file proxy.txt \
  --no-expand-search

# Reset Constructor state + JSONL and re-walk
python -m html_downloader discover \
  --marketplace saatchi \
  --proxy-file proxy.txt \
  --search-force \
  --expand-search
```

Flags: `--expand-search` / `--no-expand-search` (default on for Saatchi), `--search-force`, `--search-delay`, `--search-workers` (reserved; walks are sequential).

## Coverage notes

- Target: ≥1M unique artwork URLs in the JSONL cache (SSR `/all` ~1.18M).
- Sitemap (~350k) merges with search; Constructor fills non-sitemap / broader inventory.
- Leaves still above 10k after the full ladder may truncate at the Constructor window — logged as warnings.
- Discover does **not** require `--proxy-file` for Constructor (direct to `ac.cnstrc.com`; env proxies ignored).
- Sitemap fetch **does** require `--proxy-file` (stealth/Chrome TLS; bare httpx gets HTTP 403).
- If sitemap fails while `--expand-search` is on, discover logs a warning and continues Constructor-only.
- Download still uses the normal marketplace proxy path.
