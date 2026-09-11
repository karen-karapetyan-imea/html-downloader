#!/usr/bin/env bash
# Long-running monthly loop for a single auction house: discover + download.
# Cadence is anchored to each cycle's start time (calendar month), not finish time.
#
# Usage:
#   ./scripts/run_monthly_auction.sh invaluable
#   ./scripts/run_monthly_auction.sh liveauctioneers
#   ./scripts/run_monthly_auction.sh artcurial
#
# Optional:
#   AUCTION_RUN_DAY=1   # fixed day-of-month for next runs (1–31, clamped)
#   MIN_URLS=100000     # liveauctioneers only
#   MAX_SITEMAPS=2000   # liveauctioneers only
#   MAX_SALES=          # artcurial only (default: all pending sales)
#
# Prefer starting via:
#   ./scripts/start_monthly_auction_tmux.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
PYTHON="${PROJECT_ROOT}/.venv/bin/python"

auction_house="${1:-}"
case "${auction_house}" in
  invaluable|liveauctioneers|artcurial) ;;
  *)
    echo "usage: $0 invaluable|liveauctioneers|artcurial" >&2
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
      "${PYTHON}" -m html_downloader auction discover \
        --auction-house invaluable \
        --proxy-file proxy.txt \
        --concurrency 1 \
        --expand-artist-sold \
        --artist-sold-concurrency "${ARTIST_SOLD_CONCURRENCY:-4}" \
        --incremental \
        --update-state
      ;;
    liveauctioneers)
      "${PYTHON}" -m html_downloader auction discover \
        --auction-house liveauctioneers \
        --proxy-file proxy.txt \
        --concurrency 1 \
        --min-urls "${MIN_URLS:-100000}" \
        --max-sitemaps "${MAX_SITEMAPS:-2000}" \
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
  esac
}

run_cycle() {
  echo "--- ${auction_house}: auction discover $(date -u -Iseconds) ---"
  run_discover

  echo "--- ${auction_house}: auction download $(date -u -Iseconds) ---"
  "${PYTHON}" -m html_downloader auction download \
    --auction-house "${auction_house}" \
    --proxy-file proxy.txt \
    --skip-existing
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
