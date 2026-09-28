# Christie's Sitemap / URL Discovery

Technical notes for christies.com URL discovery used by `html_downloader` auction crawls.

## robots.txt

- **URL:** `https://www.christies.com/robots.txt`
- **Sitemap:** `https://www.christies.com/sitemap/sitemap_index.xml`
- **Crawl-delay:** none
- **Disallowed (never used):** `*/search`, `*/searchresults.aspx`, `*/AjaxPages`, `*/lotimages`, `*/mychristies`, `*/livebidding`, `*/client-registrations`

## Sitemap index

Seed: `https://www.christies.com/sitemap/sitemap_index.xml` (flat `sitemapindex`, ~101 children, all child `<lastmod>` regenerated monthly).

| Child | Role | Approx size | Used |
|-------|------|-------------|------|
| `lot_past_{1..87}_sitemap.xml` | Past lots (EN + some zh / zh-cn dupes) | ~49k locs → ~40k unique EN lots each (~3.5M total) | Full mode |
| `lot_upcoming_1_sitemap.xml` | Upcoming lots | ~4.9k locs → ~3.8k lots | Full mode |
| `auction_past_1_sitemap.xml` | Past auctions (EN + zh dupes) | ~19.9k EN auctions | Full mode + art-only sale list |
| `auction_upcoming_1_sitemap.xml` | Upcoming auctions | ~70 EN | Full mode |
| stories / image / video / catalogue / departments / general / mslp / exhibitions | Editorial | — | Skipped |

Past-lot children are ordered by lot id, **newest first**. New lots push older ones into the next file, so file contents shift every month. A child is therefore marked `done` only for the index `<lastmod>` (`epoch`) it was walked at. Runs resume within a month, and the next month re-walks every file. The JSONL cache deduplicates by lot id, so re-walks only add new lots.

## URL patterns

```text
lots:      /en/lot/lot-{objectId}                    → ("lot", objectId)
auctions:  /en/auction/auction-{saleNumber}-{room}   → ("sale", "{saleNumber}-{room}")
```

Canonical host `https://www.christies.com`; the `zh`, `zh-cn` and bare-path variants normalize to `/en/`, and query strings (e.g. `ldp_breadcrumb`) are dropped. Online-only sales use `/en/sso?ObjectID=…` in the API, but every lot also has a canonical `/en/lot/lot-{object_id}` page; always use that.

## Discovery modes

### Full archive (default)

Sequential walk of the lot and auction children listed above. It uses `curl_cffi` (Chrome impersonation), tries a direct request first and rotates through `--proxy-file` proxies on failure. About 90 files (~1.1 GB XML) per month.

### Art-only (`--algolia-artworks-only`)

Public JSON API (no key), one query stream per **past** sale from `auction_past_1_sitemap.xml`:

```text
GET https://www.christies.com/api/discoverywebsite/auctionpages/lotsearch
    ?language=en&salenumber={N}&saleroomcode={ROOM}&page={P}&pagesize=84
    &filterids=CoaCategories{Paintings}|CoaCategories{Drawings+%26+Watercolors}|...
```

- Works with `salenumber` + `saleroomcode` alone, including 2000s-era `CSK`/`CKS` sales. Page size is capped at 84.
- `|` ORs facet values server-side (verified: OR total == sum of single-facet totals).
- Facet values keep the site's encoding. Defaults (harvested from sale-page facets):
  `Paintings`, `Drawings+%26+Watercolors`, `Prints+%26+Multiples`, `Photographs`, `Sculptures%2c+Statues+%26+Figures`.
  Other values seen: `Furniture+%26+Lighting`, `Books+%26+Manuscripts`, `Watches`, `Ancient+Art+%26+Antiquities`, `Memorabilia`, `Clocks`, `All+other+categories+of+objects`.
- Override with `--algolia-supercategories NAME` (repeatable). Human labels such as `"Prints & Multiples"` are encoded automatically.
- Past sales are immutable: a `done` sale is never re-queried (unless `--algolia-force`). A sale with 0 art hits is `done`, not failed. Monthly runs therefore only query new sales.
- The first backfill makes ~20–40k requests (2 workers, 0.4 s jittered delay by default).

## State files

| Path | Purpose |
|------|---------|
| `state/auctions/christies_sitemap_progress.json` | Full mode: child sitemap status + `epoch` (index lastmod) |
| `state/auctions/christies_sitemap_progress_lots.jsonl` | Full mode: durable lot + sale URL cache (unique by key) |
| `state/auctions/christies_algolia_artworks_browse_state.json` | Art-only: per-sale `done` / `failed` checkpoint |
| `state/auctions/christies_algolia_artworks_browse_state_lots.jsonl` | Art-only: durable art lot URL cache |
| `state/auctions/christies.json` | Lastmod state for this run's in-memory entries |
| `data/auctions/christies/{YYYY-MM}/urls.txt` | Crawl list for the month (streamed from JSONL) |

The two modes never share a cache: running art-only does not touch the full-archive cache, and the reverse holds too.

## Download

Lot HTML is server-rendered (~113 KB, `window.chrComponents.lotHeader_*` has price realised, estimates and sale info). Akamai fronts the site.

Block detection for Christie's **replaces** the generic keyword list with Akamai deny-page markers (`access denied`, `errors.edgesuite.net`, `you don't have permission to access`). The generic list cannot be used here: every real lot page has an AWS WAF `challenge.js` URL and the word "blocked" in its first 16 KB, so every page would count as blocked. Status 403/429/503 still counts as a block.

## Coverage risks

- Legacy online sales whose sale page reports `sale_id=-1` return 0 hits from `lotsearch`. Art-only mode misses them; full mode still covers their lots through the sitemaps.
- The Item Category vocabulary can drift. Watch `hits` in the art state file and adjust `--algolia-supercategories` when needed.
- Akamai may challenge at volume: the sitemap walk is sequential with a 1 s inter-file sleep, and lotsearch uses jittered delays with 403/429 backoff.
- Upcoming-lot pages have no results yet. Art-only mode skips upcoming sales; they are picked up once they appear in `auction_past`.

## CLI (summary)

```bash
# Recommended monthly: art lots only (separate artworks state)
python -m html_downloader auction discover \
  --auction-house christies --proxy-file proxy.txt \
  --algolia-artworks-only --incremental --update-state

# Full archive (all categories, ~3.5M lots) from sitemaps
python -m html_downloader auction discover \
  --auction-house christies --proxy-file proxy.txt \
  --incremental --update-state

# Smoke: two child sitemaps only
python -m html_downloader auction discover \
  --auction-house christies --max-sitemaps 2 --dry-run

# Smoke: art-only on 20 sales, custom categories only
# (--algolia-supercategories alone enables art-only mode; adding
#  --algolia-artworks-only would also append the default facets)
python -m html_downloader auction discover \
  --auction-house christies --max-sales 20 \
  --algolia-supercategories "Paintings" --algolia-supercategories "Prints & Multiples"

# Chunked download
./scripts/download_url_chunks.sh christies

# Monthly loop (art-only default; CHRISTIES_FULL=1 for full archive)
./scripts/run_monthly_auction.sh christies
```
