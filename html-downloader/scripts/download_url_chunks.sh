#!/usr/bin/env bash
# Chunked auction HTML download to avoid loading huge urls.txt into RAM.
#
# Usage:
#   ./scripts/download_url_chunks.sh invaluable
#   ./scripts/download_url_chunks.sh invaluable 2026-09
#
# Env:
#   CHUNK_LINES=1000000   # lines per chunk (default 1M)
#   WORKERS=8             # download workers
#   MONTH=YYYY-MM         # override job month (or pass as $2)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
PYTHON="${PROJECT_ROOT}/.venv/bin/python"

auction_house="${1:-}"
job_month="${2:-${MONTH:-}}"
chunk_lines="${CHUNK_LINES:-1000000}"
workers="${WORKERS:-8}"

case "${auction_house}" in
  invaluable|liveauctioneers|artcurial|barnebys|saleroom|drouot|christies) ;;
  *)
    echo "usage: $0 invaluable|liveauctioneers|artcurial|barnebys|saleroom|drouot|christies [YYYY-MM]" >&2
    exit 2
    ;;
esac

cd "${PROJECT_ROOT}"

if [[ ! -x "${PYTHON}" ]]; then
  echo "error: missing venv python at ${PYTHON}" >&2
  exit 1
fi

if [[ ! -f "${PROJECT_ROOT}/proxy.txt" ]]; then
  echo "error: missing proxy.txt at ${PROJECT_ROOT}/proxy.txt" >&2
  exit 1
fi

if [[ -z "${job_month}" ]]; then
  job_month="$("${PYTHON}" -c 'from html_downloader.auctions.paths import job_month; print(job_month())')"
fi

job_dir="${PROJECT_ROOT}/data/auctions/${auction_house}/${job_month}"
urls_file="${job_dir}/urls.txt"
chunk_dir="${job_dir}/url_chunks"

if [[ ! -f "${urls_file}" ]]; then
  echo "error: missing ${urls_file} — run auction discover first" >&2
  exit 1
fi

mkdir -p "${chunk_dir}"
# Refresh chunks from current urls.txt
rm -f "${chunk_dir}"/urls_*
split -l "${chunk_lines}" -d -a 3 "${urls_file}" "${chunk_dir}/urls_"

shopt -s nullglob
chunks=("${chunk_dir}"/urls_*)
if ((${#chunks[@]} == 0)); then
  echo "error: split produced no chunks from ${urls_file}" >&2
  exit 1
fi

echo "chunked download house=${auction_house} month=${job_month} chunks=${#chunks[@]} lines_per_chunk=${chunk_lines} workers=${workers}"

for chunk in "${chunks[@]}"; do
  echo "=== chunk ${chunk} $(date -u -Iseconds) ==="
  "${PYTHON}" -m html_downloader auction download \
    --auction-house "${auction_house}" \
    --month "${job_month}" \
    --proxy-file proxy.txt \
    --urls "${chunk}" \
    --workers "${workers}" \
    --skip-existing
done

echo "chunked download complete house=${auction_house} month=${job_month}"
