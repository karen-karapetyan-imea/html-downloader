# html-downloader

Standalone sitemap discovery and stealth HTML download for **Saatchi Art**, **Artsper**, **Artsy**, **ArtMajeur**, **Singulart**, **1stDibs**, **Artfinder**, **Fine Art America**, **Phaidon**, **UGallery**, and **MutualArt**. No parsing, no database.

Crawls write to dated job folders:

```
data/{marketplace}/{YYYY-MM-DD}/
  html/{sha1}.html
  urls.txt
  sitemap_all.txt
  results.jsonl
  diff.json
  manifest.json
```

`crawl_date` is the job start date in UTC. A run that crosses midnight stays in one folder.

## Setup

```bash
cd html-downloader
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
```

Weekly scripts call `.venv/bin/python` directly (do not rely on `source .venv/bin/activate`). If you moved the project directory, recreate the venv with the commands above.

Copy `proxy.txt` (`host:port:user:pass` per line). Download always requires at least one proxy.

## Discover URLs

```bash
python -m html_downloader discover --marketplace saatchi
python -m html_downloader discover --marketplace artsper --incremental --update-state
python -m html_downloader discover --marketplace artsy --proxy-file proxy.txt --incremental
python -m html_downloader discover --marketplace singulart --incremental --update-state
python -m html_downloader discover --marketplace firstdibs --incremental
python -m html_downloader discover --marketplace artfinder --incremental --update-state
python -m html_downloader discover --marketplace fineartamerica --incremental --update-state
python -m html_downloader discover --marketplace phaidon --incremental --update-state
python -m html_downloader discover --marketplace ugallery --incremental --update-state
python -m html_downloader discover --marketplace mutualart --proxy-file proxy.txt --incremental --update-state
```

Writes `data/{marketplace}/{YYYY-MM-DD}/urls.txt` (what to download) and `sitemap_all.txt` (full entity list). `--incremental` diffs against `state/{marketplace}_lastmod.json` and prior `results.jsonl` files. **1stDibs** crawls the art HTML sitemap branches (items, dealers, creators), resumes from `state/sitemap_crawler/`, and writes artwork/dealer/creator URLs. No `lastmod`; incremental mode detects new entities only. **Artfinder** uses the public XML sitemap index (products + artists) with `lastmod` incremental diffs. **Fine Art America** uses artist + popular-products art sitemaps (`/profiles/{slug}`, `/featured/{slug}.html`); no `lastmod`, so incremental detects new entities only. **Artspace** redirects to Phaidon; **Phaidon** discovers default-locale Shopify book products (`/products/{slug}`) with `lastmod` incremental diffs. **UGallery** uses the Shopify XML sitemap (`/products/{slug}` artworks + `/pages/{slug}` artists) with `lastmod` incremental diffs. **MutualArt** crawls the HTML sitemap A–Z indexes (`/Artist/`, `/Organization/`, `/Exhibition/`, `/Auction/`); discover requires `--proxy-file` (ArtistsIndex is CAPTCHA-gated). No `lastmod`; incremental detects new entities only.

```bash
python -m html_downloader discover --marketplace firstdibs --concurrency 10
```

The standalone art sitemap crawler is also available as `python -m sitemap_crawler` (same state directory).

## Download HTML

```bash
python -m html_downloader download --marketplace saatchi --proxy-file proxy.txt --skip-existing
python -m html_downloader download --marketplace singulart --proxy-file proxy.txt --skip-existing
python -m html_downloader download --marketplace firstdibs --proxy-file proxy.txt --skip-existing
python -m html_downloader download --marketplace artfinder --proxy-file proxy.txt --skip-existing
python -m html_downloader download --marketplace fineartamerica --proxy-file proxy.txt --skip-existing
python -m html_downloader download --marketplace phaidon --proxy-file proxy.txt --skip-existing
python -m html_downloader download --marketplace ugallery --proxy-file proxy.txt --skip-existing
python -m html_downloader download --marketplace mutualart --proxy-file proxy.txt --skip-existing
```

Flags: `--date YYYY-MM-DD`, `--workers`, `--rps`, `--skip-existing`, `--urls` (override job `urls.txt`).

## Weekly runs

Each marketplace runs in its own weekly loop (discover + download, then sleep until **7 days from that cycle’s start**). Start all three **in parallel** via tmux:

```bash
cd html-downloader
chmod +x scripts/*.sh
./scripts/start_weekly_tmux.sh
```

Sessions: `crawl-saatchi`, `crawl-artsper`, `crawl-artsy`.

```bash
tmux ls
tmux attach -t crawl-saatchi   # detach: Ctrl-b then d
./scripts/stop_weekly_tmux.sh  # stop all three
```

Single marketplace (e.g. for debugging):

```bash
./scripts/run_weekly.sh saatchi
```

Logs: `logs/weekly-{marketplace}-YYYYMMDD-HHMMSS.log`. Sessions die on reboot; re-run `start_weekly_tmux.sh` to resume.

## Auction crawls (monthly)

Auction houses are separate from marketplace crawls (different CLI, data layout, and state).

Job folders:

```
data/auctions/{auction_house}/{YYYY-MM}/
  html/{sha1}.html
  urls.txt
  sitemap_all.txt
  results.jsonl
  diff.json
  metadata.json
  manifest.json
```

State: `state/auctions/{auction_house}.json` (never mixed with `state/{marketplace}_lastmod.json`).

### Invaluable

The HTML page `https://www.invaluable.com/sitemap` is a navigation hub only.
See [docs/invaluable_discovery.md](docs/invaluable_discovery.md) for the full discovery report.

Discovery walks the XML index `https://www.invaluable.com/sitemap_inv_com-index.xml` and collects:

- **Lots (XML)**: `sitemap_inv_com-lot-YYYY-M-ptN.xml` (~50k `/auction-lot/{slug}-c-{id}` URLs each). The public index currently lists only the latest month’s parts (~250k lots). That alone is **not** the full historical corpus.
- **Catalogs** (sale events): `sitemap_inv_com-catalog.xml` (`/catalog/{id}`, hundreds of pages).
- **Algolia archive (primary historical lots, default on)**: browses `archive_prod` by year (full archive: all categories, sold + unsold). No proxies. Resume: `state/auctions/invaluable_algolia_browse_state.json` (+ `_lots.jsonl` sidecar). Use `--no-expand-algolia` to skip; `--algolia-force` to reset and re-walk.
- **Hubs (XML, soft-fail)**: auction houses, artist profiles, and category tree (`--include-hubs`, default on).
- **Upcoming list**: paginates `/auctions/` SSR pages for additional catalogs (`--expand-auctions-list`, default on).
- **Artist sold expansion (optional, default off)**: `--expand-artist-sold` loads `sitemap_artist_sold_*.xml` and paginates sold pages (WAF-heavy). Resume: `state/auctions/invaluable_artist_sold_progress.json`.
- **House-page expansion (optional)**: `--expand-houses` walks house pages for extra catalog/lot links.
- **`past_search_sitemap.xml`**: listed in the index but currently returns HTTP 404; when/if it returns nested lot maps, discovery will follow them automatically.

Discover requires `--proxy-file` (WAF-gated XML/HTML). Incomplete **lot/catalog** XML sitemap fetches fail the run so state is not overwritten with a partial corpus. Hub XML failures are warnings only. Algolia browse ignores proxies.

```bash
# Default corpus (XML + hubs + Algolia archive)
python -m html_downloader auction discover --auction-house invaluable --proxy-file proxy.txt --incremental --update-state

# Smoke: single Algolia year
python -m html_downloader auction discover --auction-house invaluable --proxy-file proxy.txt --algolia-from-year 2024 --algolia-to-year 2024 --update-state

# XML lots/catalogs/hubs only
python -m html_downloader auction discover --auction-house invaluable --proxy-file proxy.txt --no-expand-algolia --no-expand-auctions-list --update-state

# Opt-in artist-sold smoke
python -m html_downloader auction discover --auction-house invaluable --proxy-file proxy.txt --expand-artist-sold --max-artist-sold 20

python -m html_downloader auction download --auction-house invaluable --proxy-file proxy.txt --skip-existing
```

Monthly loop (calendar months from cycle start; optional `AUCTION_RUN_DAY=1`):

```bash
./scripts/run_monthly_auction.sh invaluable
./scripts/start_monthly_auction_tmux.sh   # session: crawl-auction-invaluable
./scripts/stop_monthly_auction_tmux.sh
```

Logs: `logs/monthly-auction-{house}-YYYYMMDD-HHMMSS.log`.

## Tests

```bash
pytest
```

## 1stDibs art HTML sitemap crawler

Standalone module to crawl the **art HTML sitemap branches** on 1stDibs and extract deduplicated artwork, dealer, and creator URLs. Supports resume, concurrent fetching, and writes `output/artwork_urls.txt`, `dealer_urls.txt`, `creator_urls.txt`, and `all_art_urls.txt`.

```bash
python -m sitemap_crawler --concurrency 10 --delay 0.5
python -m sitemap_crawler --max-pages 100 --reset-state   # subset test
```

See [sitemap_crawler/README.md](sitemap_crawler/README.md) for seeds, filtering, resume, and full-run expectations.

