# html-downloader

Collects art-market data from online marketplaces and auction houses, and turns it into Parquet
datasets.

The repo has two independent parts that meet at the folder of raw HTML on disk:

```text
html-downloader/   crawl: discover URLs, download raw HTML          -> html-downloader/data/
etl/               parse: raw HTML -> artworks / artists / lots     -> etl/output/
```

The crawler never parses and the ETL never fetches. The ETL can be re-run on the stored HTML at any
time, e.g. after a parser fix, without downloading anything again.

## Sources

| Kind | Sources | Crawled | ETL datasets |
| --- | --- | --- | --- |
| Marketplaces | Artfinder, ArtMajeur, Artsper, Artsy, Fine Art America, MutualArt, Phaidon, Saatchi Art, UGallery | weekly | `artworks`, `artists` |
| Marketplaces (crawl only) | 1stDibs, Singulart | weekly | none yet |
| Auction houses | Artcurial, Barnebys, Christie's, Drouot, LiveAuctioneers, Sotheby's | monthly | `lots` |
| Auction houses (partial) | Invaluable, the-saleroom | monthly | `lots` parsers exist; Invaluable's crawl currently stores category pages, not lot pages |

## Layout

| Path | What it is |
| --- | --- |
| [`html-downloader/`](html-downloader/) | Crawler: sitemap/API discovery, proxied HTML download, weekly and monthly tmux loops. See its [README](html-downloader/README.md) and the per-site notes in [`html-downloader/docs/`](html-downloader/docs/). |
| [`etl/etl_core/`](etl/etl_core/) | Shared ETL machinery: parser registry (`sources.py`), snapshot runs, compaction, value normalisation. |
| [`etl/artworks/`](etl/artworks/), [`etl/artists/`](etl/artists/), [`etl/lots/`](etl/lots/) | One package per dataset: schema, per-platform mappers, CLI. |
| `etl/<source>_etl/parse.py` | One parser per site: raw HTML -> a flat dict of what the page says. |
| [`etl/tests/`](etl/tests/) | Tests, with trimmed HTML fixtures in `tests/fixtures/`. |
| [`etl/scripts/`](etl/scripts/) | `run_etl.sh` (the scheduled entry point) and sample-pulling helpers. |
| `etl/artwork_structure.csv` | The target artwork columns per marketplace that the schema follows. |

## Crawler

```bash
cd html-downloader
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
```

Downloads need a `proxy.txt` (`host:port:user:pass` per line), or `--provider decodo` with
`DECODO_API_KEY` for marketplaces. Typical use:

```bash
.venv/bin/python -m html_downloader discover --marketplace saatchi --incremental --update-state
.venv/bin/python -m html_downloader download --marketplace saatchi --proxy-file proxy.txt --skip-existing

.venv/bin/python -m html_downloader auction discover --auction-house christies --proxy-file proxy.txt --incremental --update-state
.venv/bin/python -m html_downloader auction download --auction-house christies --proxy-file proxy.txt --skip-existing
```

Each run writes one job folder:

```text
data/<marketplace>/<YYYY-MM-DD>/        marketplaces, one folder per weekly crawl
data/auctions/<house>/<YYYY-MM>/        auction houses, one folder per month
  html/<sha1>.html                      raw pages
  results.jsonl                         url -> file, status
  manifest.json                         written when the job finishes
```

Scheduling (`scripts/start_weekly_tmux.sh`, `scripts/start_monthly_auction_tmux.sh`), per-site flags
and resume state are covered in [html-downloader/README.md](html-downloader/README.md).

## ETL

Python 3.12+ with [uv](https://docs.astral.sh/uv/):

```bash
cd etl
uv sync
```

### Running

`scripts/run_etl.sh` is the scheduled entry point. It processes every finished crawl folder that has
no up-to-date snapshot yet, then compacts the platforms it touched. With nothing new it exits in
seconds, so it is safe to run hourly from cron:

```bash
scripts/run_etl.sh                    # artworks, artists and lots
scripts/run_etl.sh lots               # one dataset
scripts/run_etl.sh lots --platform christies --dry-run   # list what would be processed
```

| Variable | Default | Meaning |
| --- | --- | --- |
| `ETL_DATA_ROOT` | `../html-downloader/data` | Crawler output. Lots read `<root>/auctions/`. |
| `ETL_OUT_ROOT` | `etl/output` | Datasets go to `<root>/<dataset>/`. |
| `ETL_WORKERS` | CPU count - 1 | Parser processes. |
| `UV` | `uv` on `PATH` | Path to uv (cron has a minimal `PATH`). |

Each dataset takes a lock, so overlapping runs skip rather than collide. Logs go to
`etl/logs/etl-<target>-<date>.log`.

The same steps are available one at a time through the CLIs (`artwork-etl`, `artist-etl`, `lot-etl`):

```bash
uv run lot-etl sync --data-root ../html-downloader/data/auctions --out output/lots
uv run lot-etl run --platform sothebys --data ../html-downloader/data/auctions/sothebys/2026-09
uv run lot-etl compact --platform sothebys --out output/lots
```

### Output

```text
output/<dataset>/
  snapshots/platform_name=<p>/crawl_date=<YYYY-MM-DD>/part-*.parquet   one per crawl, plus _manifest.json, _errors.parquet
  current/platform_name=<p>/part-00000.parquet                         latest row per id across snapshots
```

Read `current/` for the latest state, or `snapshots/` for history, e.g. with DuckDB:

```sql
SELECT * FROM read_parquet('output/lots/current/*/*.parquet', hive_partitioning = true);
```

### Datasets

- **artworks**: one row per artwork listing: title, artist, size (cm and in), price, currency,
  mediums, sold / on-request flags, image, and site-specific detail. Columns follow
  `etl/artwork_structure.csv`; see [`etl/artworks/schema.py`](etl/artworks/schema.py).
- **artists**: one row per artist profile: name, nationality, birth year and the like; see
  [`etl/artists/schema.py`](etl/artists/schema.py).
- **lots**: one row per auction lot, keyed by the house's own lot id; see
  [`etl/lots/schema.py`](etl/lots/schema.py).
  - Sale: estimates, `price_realized` (the hammer where the house shows it), `sale_status`
    (`sold`, `passed`, `unsold`, `withdrawn`, `upcoming`, or null when unknown), sale id, title,
    date, location and department.
  - Work and artist: `height_cm` / `width_cm` / `depth_cm`, `medium`, `year`, `is_signed`,
    `artist_birth_year` / `artist_death_year`, `artist_nationality`. These use the artworks/artists
    column names so both can feed the same features. They come from the house's structured fields
    where it has them, else from the catalogue text ([`etl/lots/catalogue.py`](etl/lots/catalogue.py)).
  - Money is in the source currency, and a price of 0 is stored as null.

### Adding or fixing a source

1. Write `etl/<source>_etl/parse.py` with `parse_page(html[, url]) -> dict`. Return only what the
   page actually has. For lots, use `lots.fields.lot_page(...)` with keys named after `LotRow`.
2. Register it in [`etl/etl_core/sources.py`](etl/etl_core/sources.py) (`SOURCES` for
   marketplaces, `AUCTION_SOURCES` for auction houses).
3. Map it to rows: marketplaces need a mapper in `artworks/mappers/` and `artists/mappers/`.
   Lots share one mapper for every house.
4. Add a trimmed HTML fixture under `etl/tests/fixtures/` and a test next to the existing ones.
5. Before shipping, measure fill rates on a sample of real crawled pages, not only fixtures.

After a schema change, re-run every platform of that dataset. Compaction reads all snapshots of a
platform together, so a platform with a mix of old- and new-schema snapshots fails.

### Tests and lint

```bash
cd etl && uv run pytest -q && uv run ruff check .
cd html-downloader && .venv/bin/pytest
```
