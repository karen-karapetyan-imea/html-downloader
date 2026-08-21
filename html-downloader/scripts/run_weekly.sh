#!/usr/bin/env bash
# Long-running weekly loop: discover + download for saatchi, artsper, artsy.
# Cadence is anchored to each cycle's start time (7 days), not finish time.
#
# Requires: .venv installed and proxy.txt present in the project root.
# Start once (survives until reboot / kill):
#   nohup ./scripts/run_weekly.sh >> logs/nohup.out 2>&1 &

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
WEEK_SECS=$((7 * 24 * 60 * 60))
MARKETPLACES=(saatchi artsper artsy)

cd "${PROJECT_ROOT}"

if [[ ! -f "${PROJECT_ROOT}/.venv/bin/activate" ]]; then
  echo "error: missing venv at ${PROJECT_ROOT}/.venv" >&2
  exit 1
fi
# shellcheck source=/dev/null
source "${PROJECT_ROOT}/.venv/bin/activate"

if [[ ! -f "${PROJECT_ROOT}/proxy.txt" ]]; then
  echo "error: missing proxy.txt at ${PROJECT_ROOT}/proxy.txt" >&2
  exit 1
fi

mkdir -p "${PROJECT_ROOT}/logs"
LOG_FILE="${PROJECT_ROOT}/logs/weekly-$(date -u +%Y%m%d-%H%M%S).log"

exec > >(tee -a "${LOG_FILE}") 2>&1

echo "=== weekly scraper started $(date -u -Iseconds) ==="
echo "project=${PROJECT_ROOT}"
echo "log=${LOG_FILE}"

run_marketplace() {
  local marketplace="$1"
  local discover_args=(
    discover
    --marketplace "${marketplace}"
    --incremental
    --update-state
  )

  if [[ "${marketplace}" == "artsy" ]]; then
    discover_args+=(--proxy-file proxy.txt)
  fi

  echo "--- ${marketplace}: discover $(date -u -Iseconds) ---"
  python -m html_downloader "${discover_args[@]}"

  echo "--- ${marketplace}: download $(date -u -Iseconds) ---"
  python -m html_downloader download \
    --marketplace "${marketplace}" \
    --proxy-file proxy.txt \
    --skip-existing
}

while true; do
  start=$(date +%s)
  echo "=== cycle start $(date -u -Iseconds) (epoch=${start}) ==="

  for marketplace in "${MARKETPLACES[@]}"; do
    if run_marketplace "${marketplace}"; then
      :
    else
      rc=$?
      echo "error: ${marketplace} failed (exit=${rc}); continuing" >&2
    fi
  done

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
