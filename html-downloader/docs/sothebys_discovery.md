# Sotheby's URL Discovery

Technical notes for sothebys.com URL discovery used by `html_downloader` auction crawls.

## robots.txt

- **URL:** `https://www.sothebys.com/robots.txt`
- **Sitemap:** `https://www.sothebys.com/sitemap.xml`
- **Crawl-delay:** 15 s for `*` (1 s for Bingbot)
- **Disallowed (never used):** `/pdf/`, `/*/PDFs/`, `/*/auctions/ecatalogue/lot.pdf`, `/bsp-api/*`, `/en/docs/*`, `/fr/docs/*`, `/styleguide/*`

## Why not sitemaps

`sitemap.xml` is a flat index of ~190 monthly children (`sitemap-YYYYMM.xml`, plus `sitemap-latest.xml`). They list only articles, videos, artists, private sales and digital catalogues: **no lot or auction URLs**. Discovery therefore uses three other sources, run in this order:

| Source | Coverage | Module |
|--------|----------|--------|
| Algolia search index | Current platform, ~mid-2019 onward (~3.5k auctions) | `sothebys_algolia.py` |
| `/en/results` HTML listing | Legacy sales late 2004 → mid-2019 (~4k sales, ~650k lots) | `sothebys_legacy.py` |
| Site-search Algolia index | Everything the site search shows, 1999 onward (~1.92M lots + ~7.5k auctions) | `sothebys_search.py` |

Every pass preloads the keys of the other sources' caches and writes only keys none of them hold, so the three JSONL caches are disjoint (a URL lives in exactly one) and are streamed into `urls.txt` without re-deduplication.

## Algolia

The site's own search runs on Algolia app `KAR1UEUPJD`.

```text
POST https://clientapi.prod.sothelabs.com/graphql
query AlgoliaSearchKeyQuery($filters: [KeyValuePair!]) {
  algoliaSearchKey(requestedFilters: $filters) { key }
}
variables: {"filters": []}
```

An anonymous call with empty `filters` returns a **global** secured key (the site passes `auctionId` to scope it; we do not). The key embeds visibility filters (no drafts, secret auctions, hidden lots). The client fetches it lazily and refreshes it once on 401/403.

| Index | Role | Size | Key filters |
|-------|------|------|-------------|
| `prod_auctions` | Auction list | 3,484 (`isTestRecord:false`, Sep 2026) | `slugYear`, numeric `auctionDates.startDate` |
| `prod_lots` | Lots | ~980k total, **~528k real** (estimate); exact per-auction sum **469,229** | `auctionId`, `isTestLot`, numeric `sessionId` / `lotNr` / `subLotNr` / `lowEstimate`, facets `withdrawn` / `lotState` / `departments` / `objectTypes` |

- About 45% of `prod_lots` is test data. Production always applies `isTestLot:false`; `auction audit --test-lot-sample N` reports total / test / real counts per sampled auction.
- Index-wide `nbHits` for filtered queries is an **estimate** (`exhaustive.nbHits: false`); the same art query returned 202k and 152k minutes apart. Per-auction queries are exact. Every auction's lots belong to a listed auction (no orphan `auctionId`s), so the exact per-auction sum is the real total.
- Algolia returns at most **1,000 hits per query** (`hitsPerPage` and `paginationLimitedTo`, confirmed live). Anything that can exceed it is split recursively (see *Exhaustive partitioning*). The largest `lotNr` seen is 8,694, so large auctions do exist.
- Each lot hit carries `slug`, the lot URL path (`/en/buy/auction/{year}/{auction}/{lot}`). About 3% of hits have `slug: null` and are skipped.
- `objectID` is a UUID string, and `closingTime`, `auctionDate` and `url` are strings too. Algolia can only range-filter numeric attributes, so none of them can partition a query. `objectID` is therefore **not** used as a fallback split; an auction that the numeric and facet fields cannot split below 1,000 is reported as `unsplittable` instead.

## URL patterns

```text
lots:         /en/buy/auction/{year}/{auctionSlug}/{lotSlug}        → ("lot",  "{year}/{auctionSlug}/{lotSlug}")
auctions:     /en/buy/auction/{year}/{auctionSlug}                  → ("sale", "{year}/{auctionSlug}")
legacy lots:  /en/auctions/ecatalogue/{year}/{saleSlug}/lot.{n}.html → ("lot",  "legacy/{year}/{saleSlug}/{n}")
legacy sales: /en/auctions/{year}/{saleSlug}.html                    → ("sale", "legacy/{year}/{saleSlug}")
```

The `legacy/` prefix keeps legacy ids disjoint from current-platform ids. Legacy lot numbers may carry a letter suffix (`lot.60a.html`).

Canonical host `https://www.sothebys.com`. The `fr`, `de`, `it`, `zh-hans`, `zh-hant` and bare-path variants normalize to `/en/`. Slugs are lowercased and percent-encoded, and query strings are dropped. Entity ids come from the URL (not the Algolia UUID) so incremental runs can match prior `results.jsonl` rows. If Sotheby's renames a slug, that lot is re-downloaded once.

## Discovery modes

Both modes: list auctions per `slugYear` (2015 → next year), then query `prod_lots` once per pending auction (2 workers, 0.4 s jittered delay by default).

### Full archive (default)

Every real lot plus auction landing pages. State: `sothebys_algolia_browse_state.json`.

### Art-only (`--algolia-artworks-only`)

Adds `(departments:"X" OR objectTypes:"X" OR …)` for each art facet name. Defaults: 24 art departments (Contemporary Art, Impressionist & Modern Art, Old Master Paintings / Drawings, Prints, Photographs, …) plus object types Painting, Work on Paper, Print, Photograph, Sculpture, Mixed Media. A complete run (Sep 2026) found **125,239 art lots in 1,681 auctions** (123,319 with a usable URL). No auction landing pages.

Override with `--algolia-supercategories NAME` (repeatable). Each name is matched against both `departments` and `objectTypes`.

### Checkpointing

- A `Closed` auction whose partitions are all complete is marked `done` and never re-queried (unless `--algolia-force`).
- `Opened` / `Published` auctions are marked `open` and re-queried every run, so lots are picked up as they are published.
- An auction with an incomplete partition is marked `incomplete` and re-queried next run; it is never marked `done`.
- Failed auctions are retried next run, resuming at the first unfinished partition.
- Each auction entry records `url`, `expected` (the `nbHits` of the auction's root query, `auctionId` + `isTestLot:false` + any art filter), `expected_exact`, `retrieved`, `urls` (unique URLs), `partitions` (leaf count) and `incomplete`.
- The first backfill is ~3.5k lot queries plus ~12 auction-list queries (~17 min at defaults). Monthly runs after that make roughly 100 queries.

## Legacy archive (`--legacy-archive`, default on)

`/en/results?p=N` lists all ~7,880 past sales (15 per page, ~526 pages, newest first). Pages from ~260 onward link to legacy sale pages (`/en/auctions/{year}/{slug}.html`, 2005 → mid-2019); current-platform links are ignored because Algolia covers them.

1. **Listing walk (once):** every results page is fetched in ascending order (new sales pushing items to later pages then cause duplicates, never gaps). Each legacy sale is recorded as `pending`. The `__listing__` key is set to `done` only when every page succeeded; otherwise the walk is repeated next run.
2. **Sale expansion:** a sale page shows `AuctionsModule-lotsCount` (`"88 lots"`) and 12 lot links per `?p=N` page (no page-size parameter exists). If the first and last pages show a gap-free numeric run whose length equals the count, lot URLs are generated from the range (2 requests). Otherwise every page is fetched (about 1 request per 12 lots, ~8% of the later download cost).
3. **Statuses:** `done` (expanded), `missing` (sale page 404; ~11% of sampled sales, mostly wine), `failed` (fetch error, retried next run). The archive is immutable, so `done` / `missing` sales are never fetched again unless `--legacy-force` is set.

Art-only mode passes results-page department filters (`f2=<id>`, OR-combined, resolved from labels at runtime). The default art facets map to `LEGACY_ART_DEPARTMENTS` (the Algolia art departments plus legacy painting departments such as Italian / Spanish / Dutch & Belgian Paintings). Filtering is per sale, not per lot.

Cost: ~526 listing requests plus ~65k sale-page requests (sample mean 220 lots per sale). At the defaults (2 workers, 0.5 s delay) the first backfill takes roughly 10 hours and is resumable per sale; `--legacy-workers 4` roughly halves that.

## Site-search index (`--site-search`, default on)

`/en/search` embeds a public key for a second Algolia app (`ALGOLIA_SEARCH_APP_ID` / `ALGOLIA_SEARCH_API_KEY` / `ALGOLIA_SEARCH_INDEX`, currently `O28SY4Q7WU` / `bsp_dotcom_prod_en`). The values are read from the page at startup (fetched with the legacy `curl_cffi` client), fall back to built-in defaults, and the key is re-read from the page on 401/403.

The index holds every searchable record (`type:Lot` ~1.92M, `type:Auction` ~7.5k, plus articles, Buy Now, etc.). Lot hits carry the canonical page `url`, both current-platform (`/en/buy/auction/…`) and legacy (`/en/auctions/ecatalogue/…/lot.N.html`), and `endDate` in epoch **milliseconds**. It reaches what the other sources cannot: 1999–2004 sales (older than `/en/results`), legacy lots whose sale page is gone or unlisted, and current-platform lots missing from `prod_lots`.

Enumeration:

1. **Windows:** one per calendar month from 1970-01 to 24 months ahead, plus `no-end-date` (records without `endDate`, or with a negative one: `NOT endDate:0 TO 2^45`). A window is the checkpoint unit. Pre-1999 months are almost empty (one 1998 lot as of Sep 2026) and settle after one query each.
2. **Completeness:** filtered `nbHits` on this index is an estimate, so a short page (fewer than 1,000 hits) is what proves a partition complete. See *Exhaustive partitioning* for the full rule.
3. **Splitting:** month windows split on `endDate` → `type` → `departments` → `lowEstimate` → `locations` → `auctionType` → `available` → `category`; `no-end-date` starts at `type`. A single millisecond with more than 1,000 lots (a whole sale closing at once, ~176 such values) falls through to the later fields.
4. **Statuses:** complete months that ended more than 90 days ago become `done`; recent/upcoming months and `no-end-date` stay `open` and are re-queried every run; a month with an incomplete partition is `incomplete` and re-queried; failures resume next run.

Live size (Sep 2026): 1,956,199 records, of which `type:Lot` 1,922,940 and `type:Auction` 7,547; the last full crawl retrieved 1,930,366 lot + auction hits. The index is therefore essentially fully enumerated already.

## Exhaustive partitioning

`algolia_partition.py` is the shared engine for every Algolia enumeration (auction list, per-auction lots, site-search months). A window starts as one root partition, `base AND clauses`, and each query is classified:

| Query result | Classification |
|--------------|----------------|
| Page is full (`hits >= 1000`) or an exact `nbHits` exceeds 1,000 | **Overflow**: split on the next dimension |
| Exact `nbHits` equals the number of hits | Complete leaf |
| Exact `nbHits` differs from the number of hits | Retried once, then an incomplete leaf (`count_mismatch`) |
| Estimated `nbHits`, short page | Complete leaf (a short page cannot be truncated) |
| Overflow, but no dimension can split it | Incomplete leaf (`unsplittable`); its hits are still kept |

Dimensions, tried in order starting from the partition's current one:

- **`NumericRange(attr)`:** half-open clauses `attr >= lo AND attr < hi`, planned from facet counts into chunks of ~800 hits and falling back to bisection. The first split on an attribute also adds a `NOT attr:lo TO hi` remainder child, so records missing the attribute are never lost. A range that cannot shrink (one value) moves on to the next dimension.
- **`FacetValues(attr)`:** one child per facet value (up to 50), plus a remainder child that excludes all of them. A facet with a single value does not split.

Platform lots split on `sessionId` → `lotNr` → `subLotNr` → `lowEstimate` → `withdrawn` → `lotState`; the auction list splits on `auctionDates.startDate`. Every split and leaf is deterministic, so the same window always produces the same partition keys.

### Partition log and resume

Each source keeps `*_partitions.jsonl` next to its state file (`sothebys_algolia_browse_state_partitions.jsonl`, `sothebys_site_search_state_partitions.jsonl`, and so on). A window run is an **epoch**: `begin`, then `split` / `leaf` records, then `end` with the final status.

- Leaf URLs are appended to the cache **before** the `leaf` record is written, so a crash re-queries at most one leaf and never loses URLs.
- If a window's latest epoch has no `end` record (the process was interrupted or the window failed), the next run resumes it: completed leaves are skipped and split plans are replayed.
- If the latest epoch ended (including `incomplete`), the next run starts a new epoch and re-queries the whole window. An incomplete window gets a full re-check rather than a patched one.
- The log is compacted to the latest epoch per window at the end of each run. `--algolia-force` / `--site-search-force` reset it.
- State files without the new fields (written before partitioning) still load; their windows appear as "pre-audit" in the audit until they are re-queried.

Art-only mode filters `type:Lot AND (departments:"X" OR …)` with the legacy art department list plus the Algolia art names (the site-search index shares the department vocabulary). Full mode takes `(type:Lot OR type:Auction)`.

Cost: the full index is ~5k queries (March 2019, 7.6k records: 18 queries; November 2003, 16k records: 34 queries), roughly 20–40 minutes at 4 workers and the Algolia delay. Monthly runs re-query only ~28 open months.

## State files

| Path | Purpose |
|------|---------|
| `state/auctions/sothebys_algolia_browse_state.json` | Full mode: per-auction `done` / `open` / `incomplete` / `failed` plus audit fields |
| `state/auctions/sothebys_algolia_browse_state_partitions.jsonl` | Full mode: partition log (resume + per-partition audit) |
| `state/auctions/sothebys_algolia_browse_state_lots.jsonl` | Full mode: durable lot + sale URL cache |
| `state/auctions/sothebys_algolia_artworks_browse_state.json` | Art-only: per-auction checkpoint |
| `state/auctions/sothebys_algolia_artworks_browse_state_lots.jsonl` | Art-only: durable art lot URL cache |
| `state/auctions/sothebys_legacy_state.json` | Full mode legacy: `__listing__` + per-sale `pending` / `done` / `missing` / `failed` |
| `state/auctions/sothebys_legacy_state_lots.jsonl` | Full mode legacy: lot + sale URL cache |
| `state/auctions/sothebys_legacy_artworks_state.json` (+ `_lots.jsonl`) | Art-only legacy checkpoint and cache |
| `state/auctions/sothebys_site_search_state.json` | Full mode site search: per-month `done` / `open` / `incomplete` / `failed` plus audit fields |
| `state/auctions/sothebys_site_search_state_partitions.jsonl` | Full mode site search: partition log |
| `state/auctions/sothebys_site_search_state_lots.jsonl` | Full mode site search: URLs no other source holds |
| `state/auctions/sothebys_site_search_artworks_state.json` (+ `_lots.jsonl`) | Art-only site-search checkpoint and cache |
| `state/auctions/sothebys_discovery_audit.json` (`_artworks` in art mode) | Latest `auction audit` report |
| `state/auctions/sothebys.json` | Lastmod state for this run's in-memory entries |
| `data/auctions/sothebys/{YYYY-MM}/urls.txt` | Crawl list for the month (streamed from JSONL) |

The two modes never share a cache. Within a mode the Algolia, legacy and site-search caches are separate, disjoint files, all streamed into `urls.txt`. Art mode keeps its own partition logs next to its state files.

## Discovery audit (`auction audit`)

```bash
python -m html_downloader auction audit --auction-house sothebys            # live counts
python -m html_downloader auction audit --auction-house sothebys --offline  # caches + state only
python -m html_downloader auction audit --auction-house sothebys --recount --test-lot-sample 40
```

Writes `state/auctions/sothebys_discovery_audit.json` (or `--output PATH`) and prints a summary. It never writes to the caches.

| Section | Contents |
|---------|----------|
| `caches` | Unique keys per cache, per entity type, pairwise / triple overlap, union vs sum, and the **disjointness check** (the sum of cache sizes must equal the union) |
| `urls_txt` | Line count of the month's `urls.txt` and whether it matches the cache union |
| `platform`, `site_search` | Window statuses, `expected` / `retrieved` / unique URLs, partition and incomplete-leaf counts, windows short of their expected count, pre-audit windows |
| `legacy` | Sale statuses, lots shown on sale pages vs lots found in any cache, short sales |
| `index_totals` | Live `nbHits` for `prod_lots` (total / test / real), `prod_auctions`, site-search total / `Lot` / `Auction` |
| `test_lots` | `--test-lot-sample N`: exact total / test / real counts for N auctions (largest plus a spread) |
| `recount` | `--recount`: re-enumerates the platform and site-search indexes read-only and reports the **raw** A∩B, A∩C, B∩C, A∩B∩C and union (before cross-source dedupe), plus keys found in the index but missing from every cache |

Cache sets are held as 64-bit hashes (~8 bytes per key, ~16 MB for 2M URLs). The recount costs roughly one full discovery run in queries.

Verified audit (2026-09-29, `--recount --test-lot-sample 40`, ~23 min):

- Caches are disjoint: 1,933,862 unique URLs, equal to the `urls.txt` line count.
- Platform recount: 3,488 auctions, 469,229 hits retrieved = 469,229 exact expected, 0 incomplete windows, 0 keys missing from the caches.
- Site-search recount: 706 windows, 1,930,395 hits, 1,929,760 unique keys, 0 incomplete windows, 0 keys missing from the caches.
- Raw overlap before dedupe: platform ∩ site search 464,164, legacy ∩ site search 647,891, platform ∩ legacy 0, all three 0; raw union 1,933,862 = cache union.
- Test lots: 0 of 12,580 lots in 40 sampled real auctions (largest 1,116 lots); the ~449k test lots belong to test auctions, so `isTestLot:false` hides no real lots.
- Legacy: 49 sales are short by 3,169 lots in total. Those lots are in no index, since the site-search recount found nothing beyond the caches.

## Coverage ceiling (why not 4M+)

Every public index was measured directly:

| Source | Real records |
|--------|--------------|
| Site-search index | 1,956,202 records (1,922,940 lots + 7,551 auctions + non-lot types) |
| `prod_lots` | 469,229 real lots (exact per-auction sum); ~452k more are test data |
| Legacy `/en/results` | ~650k lots in ~3.5k expanded sales |

The site-search index already contains almost all platform and legacy lots, so the three sources overlap rather than add up. Cache composition before the verification run: platform 463,476 lots + 3,484 sales; legacy 645,648 lots + 3,545 sales; site search 811,236 legacy-style lots + 6,092 platform-style lots + 377 sales. A full read-only re-enumeration of both Algolia indexes produced exactly the cached union (1,933,862), so discovery is at the ceiling of what the public indexes expose. A 4M figure would require test lots, non-lot records or fabricated URLs, none of which are real lot pages.

## Download

Lot pages are server-rendered Next.js (250–480 KB). `__NEXT_DATA__` carries the Apollo cache, including estimates and price realised (`premiums.finalPriceV2`). Sampled pages returned HTTP 200 through `curl_cffi` with no WAF markers, and the generic block-keyword list has no false positives in their first 20 KB, so the defaults are kept.

## Coverage risks

- **Nothing before late 1999.** The site-search index starts at `endDate` 1999-12 (plus a single 1998 lot); `prod_auctions` has no `slugYear` before 2015; `/en/results` starts in late 2004. Querying from 1970 finds nothing more. Expected total is ~1.9–2M unique lots (the size of the site-search index plus the few Algolia/legacy lots it lacks). Christie's is larger because it publishes lots back to 1998 in sitemaps.
- An `unsplittable` or `count_mismatch` partition keeps its window `incomplete`. Check the audit's `incomplete_partition_samples`; a new numeric field in the index would be the fix, not `objectID`.
- The site-search key is geo-/visibility-restricted like the platform key (e.g. `restrictedInCountries`), so a few lots are hidden from any single region. Buy Now and Private Sale records do not map to lot URLs and are skipped.
- Legacy discovery depends on HTML markup (`data-page-count`, `AuctionsModule-lotsCount`, lot link shape). A markup change shows up as sales with 0 lots; watch `hits` in the legacy state file.
- Legacy lots have no reliable date, so `lastmod` is empty and legacy lots are never "updated", only new.
- Facet vocabulary can drift. Watch `hits` in the art state file and adjust `--algolia-supercategories` when needed.
- The secured key's embedded restrictions can change. Key refresh handles rotation, but a stricter key could hide lots.
- `Crawl-delay: 15` means ~200k art lot pages need proxy fan-out and conservative download rates.

## CLI (summary)

```bash
# Full archive: Algolia + legacy archive + site-search index (default)
python -m html_downloader auction discover \
  --auction-house sothebys --incremental --update-state --legacy-workers 4

# Art only: Algolia art facets + legacy / site-search art departments (separate state)
python -m html_downloader auction discover \
  --auction-house sothebys --algolia-artworks-only --incremental --update-state

# Algolia only (skip the legacy archive and the site-search index)
python -m html_downloader auction discover \
  --auction-house sothebys --no-legacy-archive --no-site-search --incremental --update-state

# Re-enumerate the whole site-search index
python -m html_downloader auction discover --auction-house sothebys --site-search-force

# Audit: overlap, disjointness, per-window completeness, live index totals
python -m html_downloader auction audit --auction-house sothebys --recount

# Smoke: 20 auctions + 20 legacy sales + 20 site-search months, no job files
python -m html_downloader auction discover \
  --auction-house sothebys --max-sales 20 --dry-run

# Chunked download
./scripts/download_url_chunks.sh sothebys

# Monthly loop (full default; SOTHEBYS_ART_ONLY=1, SOTHEBYS_NO_LEGACY=1,
# SOTHEBYS_NO_SITE_SEARCH=1, LEGACY_WORKERS=4)
./scripts/run_monthly_auction.sh sothebys
```
