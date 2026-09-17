#!/usr/bin/env bash
# Long-running monthly loop for a single auction house: discover + download.
# Cadence is anchored to each cycle's start time (calendar month), not finish time.
#
# Usage:
#   ./scripts/run_monthly_auction.sh invaluable
#   ./scripts/run_monthly_auction.sh liveauctioneers
#   ./scripts/run_monthly_auction.sh artcurial
#   ./scripts/run_monthly_auction.sh barnebys
#
# Optional:
#   AUCTION_RUN_DAY=1   # fixed day-of-month for next runs (1–31, clamped)
#   MIN_URLS=1000000    # liveauctioneers only (default 1M)
#   MAX_SITEMAPS=5000   # liveauctioneers only (default 5000)
#   MIN_URLS=0          # barnebys: 0 = all pending lot shards in one discover (default)
#   MAX_SITEMAPS=100    # barnebys (default 100; index has 8 lot shards)
#   MAX_SALES=          # artcurial only (default: all pending sales)
#   CHUNK_LINES=1000000 # invaluable / liveauctioneers / barnebys chunked download
#   WORKERS=8           # invaluable / liveauctioneers / barnebys chunked download workers
#
# Prefer starting via:
#   ./scripts/start_monthly_auction_tmux.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
PYTHON="${PROJECT_ROOT}/.venv/bin/python"

auction_house="${1:-}"
case "${auction_house}" in
  invaluable|liveauctioneers|artcurial|barnebys) ;;
  *)
    echo "usage: $0 invaluable|liveauctioneers|artcurial|barnebys" >&2
    exit 2
    ;;
esac

cd "${PROJECT_ROOT}"

if [[ ! -x "${PYTHON}" ]]; then
  echo "error: missing venv python at ${PYTHON}" >&2
  echo "create it with: python3 -m venv .venv && .venv/bin/pip install -r requirements.txt" >&2
  exit 1
fi

if [[ ! -f "${PROJECT_ROOT}/proxy.txt" ]]; then
  echo "error: missing proxy.txt at ${PROJECT_ROOT}/proxy.txt" >&2
  exit 1
fi

mkdir -p "${PROJECT_ROOT}/logs"
LOG_FILE="${PROJECT_ROOT}/logs/monthly-auction-${auction_house}-$(date -u +%Y%m%d-%H%M%S).log"

run_discover() {
  case "${auction_house}" in
    invaluable)
      # Fine Art lots only — separate Algolia artworks state (does not wipe full archive cache).
      "${PYTHON}" -m html_downloader auction discover \
        --auction-house invaluable \
        --proxy-file proxy.txt \
        --concurrency 1 \
        --algolia-artworks-only \
        --incremental \
        --update-state
      ;;
    liveauctioneers)
      "${PYTHON}" -m html_downloader auction discover \
        --auction-house liveauctioneers \
        --proxy-file proxy.txt \
        --concurrency 1 \
        --min-urls "${MIN_URLS:-1000000}" \
        --max-sitemaps "${MAX_SITEMAPS:-5000}" \
        --incremental \
        --update-state
      ;;
    artcurial)
      discover_args=(
        --auction-house artcurial
        --concurrency "${ARTCURIAL_CONCURRENCY:-4}"
        --incremental
        --update-state
      )
      if [[ -n "${MAX_SALES:-}" ]]; then
        discover_args+=(--max-sales "${MAX_SALES}")
      fi
      "${PYTHON}" -m html_downloader auction discover "${discover_args[@]}"
      ;;
    barnebys)
      "${PYTHON}" -m html_downloader auction discover \
        --auction-house barnebys \
        --proxy-file proxy.txt \
        --concurrency 1 \
        --min-urls "${MIN_URLS:-0}" \
        --max-sitemaps "${MAX_SITEMAPS:-100}" \
        --incremental \
        --update-state
      ;;
  esac
}

run_download() {
  case "${auction_house}" in
    invaluable|liveauctioneers|barnebys)
      # Large url lists — chunk to avoid OOM loading full urls.txt.
      /bin/bash --noprofile --norc "${SCRIPT_DIR}/download_url_chunks.sh" "${auction_house}"
      ;;
    *)
      "${PYTHON}" -m html_downloader auction download \
        --auction-house "${auction_house}" \
        --proxy-file proxy.txt \
        --skip-existing
      ;;
  esac
}

run_cycle() {
  echo "--- ${auction_house}: auction discover $(date -u -Iseconds) ---"
  run_discover

  echo "--- ${auction_house}: auction download $(date -u -Iseconds) ---"
  run_download
}

# Prints: "<seconds> <next_iso>"
next_monthly_sleep() {
  local start_epoch="$1"
  AUCTION_RUN_DAY="${AUCTION_RUN_DAY:-}" "${PYTHON}" -c "
from datetime import datetime, timezone
import os
from html_downloader.auctions.schedule import next_monthly_run, parse_run_day, seconds_until

anchor = datetime.fromtimestamp(${start_epoch}, tz=timezone.utc)
run_day = parse_run_day(os.environ.get('AUCTION_RUN_DAY'))
target = next_monthly_run(anchor, run_day=run_day)
secs = int(seconds_until(target))
iso = target.isoformat().replace('+00:00', 'Z')
print(f'{secs} {iso}')
"
}

main() {
  echo "=== monthly auction scraper (${auction_house}) started $(date -u -Iseconds) ==="
  echo "project=${PROJECT_ROOT}"
  echo "python=${PYTHON}"
  echo "log=${LOG_FILE}"
  echo "AUCTION_RUN_DAY=${AUCTION_RUN_DAY:-"(cycle-start day)"}"

  while true; do
    start=$(date +%s)
    echo "=== cycle start $(date -u -Iseconds) (epoch=${start}) ==="

    if run_cycle; then
      :
    else
      rc=$?
      echo "error: ${auction_house} auction cycle failed (exit=${rc}); will wait for next month" >&2
    fi

    now=$(date +%s)
    echo "=== cycle done $(date -u -Iseconds); elapsed=$((now - start))s ==="

    read -r remaining next_iso < <(next_monthly_sleep "${start}")
    echo "next scheduled run: ${next_iso}"
    if (( remaining > 0 )); then
      echo "sleeping ${remaining}s until next monthly cycle"
      sleep "${remaining}"
    else
      echo "cycle overran one month; starting next cycle immediately"
    fi
  done
}

# Keep pane output and a log file without process-substitution (unreliable under tmux).
main 2>&1 | tee -a "${LOG_FILE}"
