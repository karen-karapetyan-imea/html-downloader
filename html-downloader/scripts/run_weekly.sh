#!/usr/bin/env bash
# Long-running weekly loop for a single marketplace: discover + download.
# Cadence is anchored to each cycle's start time (7 days), not finish time.
#
# Usage:
#   ./scripts/run_weekly.sh saatchi|artsper|artsy|artmajeur
#
# Prefer starting all three in parallel via:
#   ./scripts/start_weekly_tmux.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
WEEK_SECS=$((7 * 24 * 60 * 60))
PYTHON="${PROJECT_ROOT}/.venv/bin/python"

marketplace="${1:-}"
case "${marketplace}" in
  saatchi|artsper|artsy|artmajeur) ;;
  *)
    echo "usage: $0 saatchi|artsper|artsy|artmajeur" >&2
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

run_cycle() {
  local discover_args=(
    discover
    --marketplace "${marketplace}"
    --incremental
    --update-state
  )

  if [[ "${marketplace}" == "artsy" || "${marketplace}" == "artmajeur" ]]; then
    discover_args+=(--proxy-file proxy.txt)
  fi

  echo "--- ${marketplace}: discover $(date -u -Iseconds) ---"
  "${PYTHON}" -m html_downloader "${discover_args[@]}"

  echo "--- ${marketplace}: download $(date -u -Iseconds) ---"
  "${PYTHON}" -m html_downloader download \
    --marketplace "${marketplace}" \
    --proxy-file proxy.txt \
    --skip-existing
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
