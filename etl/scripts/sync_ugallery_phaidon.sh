#!/usr/bin/env bash
# Sync ugallery + phaidon artworks/artists.
#
# On scraper-01 (after git pull), run in place:
#   ./scripts/sync_ugallery_phaidon.sh
#
# From a laptop, deploy then sync remotely:
#   ./scripts/sync_ugallery_phaidon.sh --deploy developer@192.168.244.11
set -euo pipefail

ETL="$(cd "$(dirname "$0")/.." && pwd)"
DATA="${ETL_DATA_ROOT:-}"
HOST=""

if [[ "${1:-}" == "--deploy" ]]; then
  HOST="${2:-developer@192.168.244.11}"
  shift 2 || shift 1 || true
elif [[ "${1:-}" == -* ]]; then
  echo "usage: $0 [--deploy [user@host]]" >&2
  exit 2
elif [[ -n "${1:-}" ]]; then
  # Back-compat: bare host still means deploy+remote sync
  HOST="$1"
fi

if [[ -z "$DATA" ]]; then
  if [[ -d "$ETL/../html-downloader/data" ]]; then
    DATA="$(cd "$ETL/../html-downloader/data" && pwd)"
  else
    DATA="/home/developer/html-downloader/html-downloader/data"
  fi
fi

run_sync() {
  local etl=$1 data=$2
  cd "$etl"
  echo "etl=$etl"
  echo "data=$data"
  echo "platforms registered:"
  uv run python -c "from etl_core.sources import PLATFORMS; print(', '.join(PLATFORMS))"
  echo "=== artworks sync ugallery + phaidon ==="
  uv run python -m artworks sync --data-root "$data" --platform ugallery --platform phaidon
  echo "=== artists sync ugallery + phaidon ==="
  uv run python -m artists sync --data-root "$data" --platform ugallery --platform phaidon
  echo "=== current row counts / non-empty errors ==="
  uv run python <<'PY'
from pathlib import Path
import pyarrow.parquet as pq

for p in sorted(Path("output").rglob("_errors.parquet")):
    t = pq.read_table(p)
    if t.num_rows:
        print(p, t.num_rows, "error rows")
        print(t.slice(0, min(5, t.num_rows)).to_pydict())
for platform in ("ugallery", "phaidon"):
    for dataset in ("artworks", "artists"):
        cur = list(Path(f"output/{dataset}/current/platform_name={platform}").glob("*.parquet"))
        if not cur:
            print(f"{dataset}/{platform}: no current parquet")
            continue
        t = pq.read_table(cur[0])
        print(f"{dataset}/{platform}: {t.num_rows} rows")
PY
}

if [[ -n "$HOST" ]]; then
  SSH=(ssh -i "${HOME}/.ssh/developer_rsa" -o ConnectTimeout=20 "$HOST")
  REMOTE_ETL="${REMOTE_ETL:-/home/developer/html-downloader/etl}"
  REMOTE_DATA="${DATA}"
  echo "Deploying $ETL -> $HOST:$REMOTE_ETL"
  rsync -az --delete \
    --exclude '.venv' --exclude 'output' --exclude '__pycache__' --exclude '.ruff_cache' --exclude 'samples' \
    -e "ssh -i ${HOME}/.ssh/developer_rsa -o ConnectTimeout=20" \
    "$ETL/" "$HOST:$REMOTE_ETL/"
  echo "Syncing on $HOST..."
  "${SSH[@]}" "ETL_DATA_ROOT=$REMOTE_DATA $REMOTE_ETL/scripts/sync_ugallery_phaidon.sh"
else
  run_sync "$ETL" "$DATA"
fi
