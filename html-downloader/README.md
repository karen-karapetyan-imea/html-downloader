# html-downloader

Standalone sitemap discovery and stealth HTML download for **Saatchi Art**, **Artsper**, **Artsy**, **ArtMajeur**, **Singulart**, **1stDibs**, and **Artfinder**. No parsing, no database.

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
```

Writes `data/{marketplace}/{YYYY-MM-DD}/urls.txt` (what to download) and `sitemap_all.txt` (full entity list). `--incremental` diffs against `state/{marketplace}_lastmod.json` and prior `results.jsonl` files. **1stDibs** crawls the art HTML sitemap branches (items, dealers, creators), resumes from `state/sitemap_crawler/`, and writes artwork/dealer/creator URLs. No `lastmod`; incremental mode detects new entities only. **Artfinder** uses the public XML sitemap index (products + artists) with `lastmod` incremental diffs.

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

