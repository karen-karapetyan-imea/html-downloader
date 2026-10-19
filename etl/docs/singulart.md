# Singulart ETL — blocked on empty crawls

Singulart is **not** registered in `etl_core.sources` yet.

## Status on scraper-01 (2026-10)

Crawl date folders exist under `~/html-downloader/html-downloader/data/singulart/`:

- `2026-09-01`
- `2026-10-14`

Both have **0 HTML files** (`html/` empty or missing pages). Earlier `probe_structure.py` runs sampled 0 pages.

## Next steps (crawler, then ETL)

1. Inspect weekly download logs for singulart failures (`logs/weekly-singulart-*.log`).
2. Re-run discover + download until `data/singulart/<date>/html/` has artwork/artist pages.
3. Probe with `scripts/probe_structure.py --platform singulart --data …`.
4. Add `singulart_etl/parse.py` + mappers using URL patterns already in discover / probe:

   - artwork: `/artworks/<slug>-<id>`
   - artist: `/artist/<slug>-<id>`

Until HTML exists, sync for singulart would produce empty Parquet.
