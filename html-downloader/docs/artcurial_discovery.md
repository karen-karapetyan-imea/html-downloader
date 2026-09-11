# Artcurial Sitemap / URL Discovery

Technical notes for Artcurial.com URL discovery used by `html_downloader` auction crawls.

## robots.txt

- **URL:** `https://www.artcurial.com/robots.txt`
- **Allow:** `/ace` (public JSON API) and lot/sale HTML under `/en/`, `/fr/`
- **Disallow:** `/motorcars` (and locale variants), `/?slider=on`, tracking pixels
- **Named bots** (AhrefsBot, Amazonbot, etc.): `Disallow: /` — do **not** use SEO bot UAs
- **Sitemaps listed:** `sitemap.xml`, `sitemap_index.xml` (currently HTTP 500 — unused)

Discovery honour motorscars by skipping sales whose specialty/parent refs are
`CARS`, `VOIT`, or `AUTOMOBILIA`.

## API discovery (primary)

| Endpoint | Role |
|----------|------|
| `GET /ace/sales/results?page=&size=&sort=validity.beginDate,desc` | Finished sales (2003→now), ~2.6k |
| `GET /ace/sales/{ref}/items?page=&size=200` | All lots of a sale (paginated) |

Envelope shape: `{content:[...], totalElements, totalPages, currentPage, size}`.

No fine-art filter — every publishable lot becomes a crawl URL (maximize corpus).

Resume: `state/auctions/artcurial_sales_progress.json` (completed sale refs marked
`done`, including skipped motorcars sales).

Smoke: `--max-sales N` limits how many pending sales are expanded per run.

## URL patterns

```text
lot (crawl target):  /en/sales/{ref}/lots/{index}-{sub}
entity key:          ("lot", "{ref}:{index}-{sub}")
```

Canonical host: `https://www.artcurial.com`. Locale `/fr/` normalizes to `/en/`.

## Download

Lot pages are Nuxt SSR HTML (title, estimate, sold price present). Download uses
default Chrome TLS impersonation and requires `--proxy-file` (same as other houses).
No Googlebot UA, no `window.__data` soft-block.

## Coverage

- Historical finished sales only (`/ace/sales/results`).
- Full archive is one discover pass (~300k–400k lot URLs expected).
- Lot field extraction stays out of html-downloader (HTML download only).

## Commands

```bash
# Smoke
python -m html_downloader auction discover \
  --auction-house artcurial --max-sales 5 --update-state

# Full archive
python -m html_downloader auction discover \
  --auction-house artcurial --incremental --update-state

python -m html_downloader auction download \
  --auction-house artcurial --proxy-file proxy.txt --skip-existing

./scripts/run_monthly_auction.sh artcurial
```
