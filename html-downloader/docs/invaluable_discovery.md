# Invaluable Sitemap / URL Discovery

Technical notes for Invaluable.com URL discovery used by `html_downloader` auction crawls.

## robots.txt

- **URL:** `https://www.invaluable.com/robots.txt`
- **Sitemap:** `https://www.invaluable.com/sitemap_inv_com-index.xml`
- **Crawl-delay:** `10` for `User-agent: *`
- **Do not use as discovery sources:** `/search?keyword=`, `*searchLots.cfm` / `*searchlots.cfm`, account/bidding paths

## Sitemap index

Seed: `https://www.invaluable.com/sitemap_inv_com-index.xml` (flat `sitemapindex`).

| Child | Role | Approx size | Historical lots? |
|-------|------|-------------|------------------|
| `sitemap_inv_com-lot-YYYY-M-ptN.xml` | Current-month lots | ~50k × N ≈ 250k | No (rolling window) |
| `sitemap_inv_com-catalog.xml` | Sale/catalog events | ~700 | Mostly current/upcoming |
| `sitemap_artist_sold_{1..5}.xml` | Artist sold-page seeds | ~70–75k seeds | Optional HTML expansion |
| `sitemap_inv_com-auctionhouse.xml` | Auction houses | ~3.2k | House hubs |
| `sitemap_inv_com-artists.xml` | Artist profiles | tens of thousands | Profiles / sold links |
| `sitemap_inv_com-{super,}category.xml`, `subcategory` | Category tree | ~500+ | Category hubs |
| `past_search_sitemap.xml` | Historical lot maps | Often 404 | Ideal if revived |

HTML sitemap `https://www.invaluable.com/sitemap` is a navigation hub only (no lot URLs).

## URL patterns

```text
lots:        /auction-lot/{slug}-{lotNumber}-c-{hexId}
             /lot/{lotRef}  (short form; deduped by id)
catalogs:    /catalog/{10-char-id}
houses:      /auction-house/{slug}-{id}
             /auction-houses/{id}
artists:     /artist/{slug}-{id}/
             /artist/{slug}-{id}/sold-at-auction-prices/?page=N  (seed, not crawl target)
categories:  /{slug}/pc-{ID}/  /{slug}/cc-{ID}/  /{slug}/sc-{ID}/
upcoming:    /auctions/  (SSR list; ~50/page)
```

Canonical host: `https://www.invaluable.com` (prefer over `invaluable.co.uk` / related domains).

## Discovery sources

### PRIMARY

1. Lot XML parts (current public lots + `lastmod`)
2. Catalog XML (sale events)
3. **Algolia `archive_prod` browse** (full historical archive — all categories, sold + unsold/passed). Year-partitioned cursors; no Cloudflare. Default **on** (`--expand-algolia`).

### SECONDARY

4. Auction-house / artists / category XML hubs (`--include-hubs`)
5. `/auctions/` listing pagination (upcoming catalogs)
6. Optional house-page walk (`--expand-houses`) for extra catalog/lot links
7. Optional artist-sold HTML expansion (`--expand-artist-sold`, default **off**)

### FALLBACK

8. Soft-probe `past_search_sitemap.xml` (auto-follow if nested maps appear)
9. Price Archive UI — do not POST `searchLots.cfm` (robots Disallow)

## Algolia archive browse

- **Index:** `archive_prod` (public search-only key embedded in site JS)
- **Filters:** year only — `dateTimeUTCUnix >= start AND dateTimeUTCUnix < end` (no Fine Art / `priceResult` filters)
- **URL build:** `/auction-lot/{slug}-{lotNumber}-c-{lotRef}` from hit fields; dedupe by `lotRef`
- **State:** `state/auctions/invaluable_algolia_browse_state.json` (per-year cursor)
- **Lot cache:** `state/auctions/invaluable_algolia_browse_state_lots.jsonl` (so done years still emit on later runs)
- **Flags:** `--algolia-from-year` (default 1989), `--algolia-to-year`, `--algolia-workers` (2), `--algolia-delay` (0.4), `--algolia-force`

Algolia browse does not use proxies. Proxies remain required for XML / hub HTML fetches.

## Coverage risks

- Lot XML is current-month only; historical coverage comes from Algolia (default) or optional artist-sold.
- Full `archive_prod` is multi-million URLs — expect large `urls.txt` / memory for the in-memory merge set.
- WAF/403 can still block **HTML download** even when discovery succeeds.
- Catalog “Price Results” tabs often lack lot hrefs in SSR HTML.
- Deleted auctions may 404 on download (expected).
- Dedup by entity id; strip tracking query params on lot/catalog/hubs.

## CLI (summary)

```bash
# Default: XML lots/catalogs + hubs + Algolia archive (artist-sold off)
python -m html_downloader auction discover \
  --auction-house invaluable --proxy-file proxy.txt \
  --incremental --update-state

# Smoke: one Algolia year only
python -m html_downloader auction discover \
  --auction-house invaluable --proxy-file proxy.txt \
  --algolia-from-year 2024 --algolia-to-year 2024 \
  --no-expand-auctions-list --update-state

# XML + hubs only (no Algolia, no artist-sold)
python -m html_downloader auction discover \
  --auction-house invaluable --proxy-file proxy.txt \
  --no-expand-algolia --update-state

# Opt-in artist-sold (slow / WAF-heavy)
python -m html_downloader auction discover \
  --auction-house invaluable --proxy-file proxy.txt \
  --expand-artist-sold --max-artist-sold 20

# Download HTML for the monthly job urls.txt
python -m html_downloader auction download \
  --auction-house invaluable --proxy-file proxy.txt --skip-existing
```
