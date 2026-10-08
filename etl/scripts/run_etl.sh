#!/usr/bin/env bash
# Turn newly crawled HTML into the artworks and artists Parquet datasets (`<dataset>-etl sync`).
# Every finished crawl folder under the data root without an up-to-date snapshot is processed,
# then the touched platforms are compacted. With nothing new it exits within seconds.
#
#   scripts/run_etl.sh [all|artworks|artists] [extra sync args...]
#   scripts/run_etl.sh artworks --platform saatchi --dry-run
#   scripts/run_etl.sh all --include-unmanaged      # also crawls that predate manifest.json
#
# Environment:
#   ETL_DATA_ROOT  crawl folders <root>/<platform>/<YYYY-MM-DD>/ (default: <repo>/html-downloader/data)
#   ETL_OUT_ROOT   datasets are written to $ETL_OUT_ROOT/<dataset> (default: <etl>/output)
#   ETL_WORKERS    parser processes (default: CPU count - 1; halve it when artworks and artists overlap)
#   UV             uv binary (default: uv on PATH, else ~/.local/bin/uv; cron has a minimal PATH)
#
# A dataset already being synced is skipped, so overlapping runs are safe; artworks and artists
# can run in parallel (e.g. one tmux session each). Output goes to logs/etl-<target>-<date>.log.
#
# Cron, hourly:
#   15 * * * * /path/to/etl/scripts/run_etl.sh >/dev/null 2>&1

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ETL_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
REPO_ROOT="$(cd "${ETL_ROOT}/.." && pwd)"
DATA_ROOT="${ETL_DATA_ROOT:-${REPO_ROOT}/html-downloader/data}"
OUT_ROOT="${ETL_OUT_ROOT:-${ETL_ROOT}/output}"
LOG_DIR="${ETL_ROOT}/logs"
UV="${UV:-$(command -v uv || echo "${HOME}/.local/bin/uv")}"

target="${1:-all}"
case "${target}" in
  all) datasets=(artworks artists) ;;
  artworks | artists) datasets=("${target}") ;;
  -h | --help)
    sed -n '2,20p' "$0"
    exit 0
    ;;
  *)
    echo "usage: $0 [all|artworks|artists] [extra sync args...]" >&2
    exit 2
    ;;
esac
shift $(($# > 0 ? 1 : 0))

prog_for() {
  case "$1" in
    artworks) echo artwork-etl ;;
    artists) echo artist-etl ;;
  esac
}

main() {
  if [[ ! -x "${UV}" ]]; then
    echo "error: uv not found (looked for ${UV}); set UV=/path/to/uv" >&2
    return 1
  fi
  if [[ ! -d "${DATA_ROOT}" ]]; then
    echo "error: crawl data root not found: ${DATA_ROOT} (set ETL_DATA_ROOT)" >&2
    return 1
  fi
  mkdir -p "${OUT_ROOT}"

  local low_priority=(nice -n 10)
  if command -v ionice >/dev/null 2>&1; then
    # Best-effort lowest rather than idle (-c3): idle can starve the ETL while the crawler writes.
    low_priority+=(ionice -c 2 -n 7)
  fi
  local workers_args=()
  if [[ -n "${ETL_WORKERS:-}" ]]; then
    workers_args=(--workers "${ETL_WORKERS}")
  fi

  cd "${ETL_ROOT}"
  local rc=0 dataset lock
  for dataset in "${datasets[@]}"; do
    # One lock per dataset: a second sync of the same dataset exits, artworks and artists may overlap.
    lock="${OUT_ROOT}/.sync-${dataset}.lock"
    exec 9>"${lock}"
    if ! flock -n 9; then
      echo "$(date -u -Iseconds) another ${dataset} sync holds ${lock}; skipping"
      continue
    fi
    echo "=== ${dataset} sync started $(date -u -Iseconds) data=${DATA_ROOT} out=${OUT_ROOT}/${dataset} ==="
    if "${low_priority[@]}" "${UV}" run --frozen --no-dev "$(prog_for "${dataset}")" sync \
      --data-root "${DATA_ROOT}" \
      --out "${OUT_ROOT}/${dataset}" \
      "${workers_args[@]}" \
      "$@"; then
      echo "=== ${dataset} sync finished $(date -u -Iseconds) ==="
    else
      rc=$?
      echo "=== ${dataset} sync FAILED (exit=${rc}) $(date -u -Iseconds) ===" >&2
    fi
    exec 9>&-
  done
  return "${rc}"
}

mkdir -p "${LOG_DIR}"
# Keep terminal output and a daily log file without process substitution (same as the crawler scripts).
main "$@" 2>&1 | tee -a "${LOG_DIR}/etl-${target}-$(date -u +%Y%m%d).log"
