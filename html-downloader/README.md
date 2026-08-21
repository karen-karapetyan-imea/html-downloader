# html-downloader

Standalone sitemap discovery and stealth HTML download for **Saatchi Art**, **Artsper**, and **Artsy**. No parsing, no database.

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
source .venv/bin/activate
pip install -r requirements-dev.txt
```

Copy `proxy.txt` (`host:port:user:pass` per line). Download always requires at least one proxy.

## Discover URLs

```bash
python -m html_downloader discover --marketplace saatchi
python -m html_downloader discover --marketplace artsper --incremental --update-state
python -m html_downloader discover --marketplace artsy --proxy-file proxy.txt --incremental
```

Writes `data/{marketplace}/{YYYY-MM-DD}/urls.txt` (what to download) and `sitemap_all.txt` (full entity list). `--incremental` diffs against `state/{marketplace}_lastmod.json` and prior `results.jsonl` files.

## Download HTML

```bash
python -m html_downloader download --marketplace saatchi --proxy-file proxy.txt --skip-existing
```

Flags: `--date YYYY-MM-DD`, `--workers`, `--rps`, `--skip-existing`, `--urls` (override job `urls.txt`).

## Weekly runs

`scripts/run_weekly.sh` runs discover + download for **saatchi**, **artsper**, and **artsy**, then sleeps until **7 days from that cycle’s start** before repeating. One marketplace failure is logged; the loop continues.

```bash
cd html-downloader
chmod +x scripts/run_weekly.sh
mkdir -p logs
nohup ./scripts/run_weekly.sh >> logs/nohup.out 2>&1 &
```

Logs also go to `logs/weekly-YYYYMMDD-HHMMSS.log`. The process stops on reboot or kill; restart the command above to resume.

## Tests

```bash
pytest
```
