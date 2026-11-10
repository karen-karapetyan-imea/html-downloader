#!/usr/bin/env bash
# Long-running weekly loop for a single marketplace: full discover + download + ETL.
# Cadence is anchored to each cycle's start time (7 days), not finish time.
#
# Usage:
#   ./scripts/run_weekly.sh saatchi|artsper|artsy|artmajeur|singulart|firstdibs|artfinder|fineartamerica|phaidon|ugallery|mutualart
#
# Prefer starting all three in parallel via:
#   ./scripts/start_weekly_tmux.sh
#
# After a successful crawl, marketplaces with an ETL parser upload to Mongo and
# keep only the two newest fully uploaded HTML folders. Requires MONGODB_URI.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
REPO_ROOT="$(cd "${PROJECT_ROOT}/.." && pwd)"
ETL_SCRIPT="${REPO_ROOT}/etl/scripts/run_etl.sh"
WEEK_SECS=$((7 * 24 * 60 * 60))
PYTHON="${PROJECT_ROOT}/.venv/bin/python"

marketplace="${1:-}"
case "${marketplace}" in
  saatchi|artsper|artsy|artmajeur|singulart|firstdibs|artfinder|fineartamerica|phaidon|ugallery|mutualart) ;;
  *)
    echo "usage: $0 saatchi|artsper|artsy|artmajeur|singulart|firstdibs|artfinder|fineartamerica|phaidon|ugallery|mutualart" >&2
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
LOG_FILE="${PROJECT_ROOT}/logs/weekly-${marketplace}-$(date -u +%Y%m%d-%H%M%S).log"

# Marketplaces that have artworks + artists ETL parsers (singulart / firstdibs crawl only).
etl_marketplace() {
  case "${marketplace}" in
    saatchi|artsper|artsy|artmajeur|artfinder|fineartamerica|phaidon|ugallery|mutualart) return 0 ;;
    *) return 1 ;;
  esac
}

run_etl() {
  if ! etl_marketplace; then
    echo "--- ${marketplace}: no ETL parser; skipping upload ---"
    return 0
  fi
  if [[ ! -x "${ETL_SCRIPT}" ]]; then
    echo "error: missing ETL script at ${ETL_SCRIPT}" >&2
    return 1
  fi
  echo "--- ${marketplace}: ETL artworks $(date -u -Iseconds) ---"
  "${ETL_SCRIPT}" artworks --platform "${marketplace}"
  echo "--- ${marketplace}: ETL artists $(date -u -Iseconds) ---"
  "${ETL_SCRIPT}" artists --platform "${marketplace}"
}

run_cycle() {
  local discover_args=(
    discover
    --marketplace "${marketplace}"
    --update-state
  )

  if [[ "${marketplace}" == "saatchi" || "${marketplace}" == "artsy" || "${marketplace}" == "artmajeur" || "${marketplace}" == "mutualart" ]]; then
    discover_args+=(--proxy-file proxy.txt)
  fi

  echo "--- ${marketplace}: discover (full) $(date -u -Iseconds) ---"
  "${PYTHON}" -m html_downloader "${discover_args[@]}"

  echo "--- ${marketplace}: download $(date -u -Iseconds) ---"
  "${PYTHON}" -m html_downloader download \
    --marketplace "${marketplace}" \
    --proxy-file proxy.txt \
    --skip-existing

  run_etl
}

main() {
  echo "=== weekly scraper (${marketplace}) started $(date -u -Iseconds) ==="
  echo "project=${PROJECT_ROOT}"
  echo "python=${PYTHON}"
  echo "log=${LOG_FILE}"

  while true; do
    start=$(date +%s)
    echo "=== cycle start $(date -u -Iseconds) (epoch=${start}) ==="

    if run_cycle; then
      :
    else
      rc=$?
      echo "error: ${marketplace} cycle failed (exit=${rc}); will wait for next week" >&2
    fi

    now=$(date +%s)
    remaining=$((start + WEEK_SECS - now))
    echo "=== cycle done $(date -u -Iseconds); elapsed=$((now - start))s ==="

    if (( remaining > 0 )); then
      echo "sleeping ${remaining}s until next cycle ($(date -u -d "@$((start + WEEK_SECS))" -Iseconds 2>/dev/null || date -u -r "$((start + WEEK_SECS))" -Iseconds 2>/dev/null || echo "start+${WEEK_SECS}s"))"
      sleep "${remaining}"
    else
      echo "cycle overran one week by $((-remaining))s; starting next cycle immediately"
    fi
  done
}

# Keep pane output and a log file without process-substitution (unreliable under tmux).
main 2>&1 | tee -a "${LOG_FILE}"
