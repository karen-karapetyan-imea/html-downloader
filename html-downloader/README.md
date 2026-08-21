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

