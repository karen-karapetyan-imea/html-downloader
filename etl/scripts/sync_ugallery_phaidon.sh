#!/usr/bin/env bash
# Deploy this ETL tree to scraper-01 and sync ugallery + phaidon artworks/artists.
# Requires Twingate (`tg-arty`) and SSH key.
#
#   scripts/sync_ugallery_phaidon.sh
#   scripts/sync_ugallery_phaidon.sh developer@192.168.244.11
set -euo pipefail

HOST="${1:-developer@192.168.244.11}"
SSH=(ssh -i "${HOME}/.ssh/developer_rsa" -o ConnectTimeout=20 "$HOST")
RSYNC=(rsync -az --delete --exclude '.venv' --exclude 'output' --exclude '__pycache__' --exclude '.ruff_cache' --exclude 'samples')
LOCAL_ETL="$(cd "$(dirname "$0")/.." && pwd)"
REMOTE_ETL="${REMOTE_ETL:-/home/developer/html-downloader/etl}"
DATA="${DATA:-/home/developer/html-downloader/html-downloader/data}"

echo "Deploying $LOCAL_ETL -> $HOST:$REMOTE_ETL"
"${RSYNC[@]}" -e "ssh -i ${HOME}/.ssh/developer_rsa -o ConnectTimeout=20" \
  "$LOCAL_ETL/" "$HOST:$REMOTE_ETL/"

echo "Syncing on $HOST..."
"${SSH[@]}" bash -s -- "$REMOTE_ETL" "$DATA" <<'REMOTE'
set -euo pipefail
ETL=$1
DATA=$2
cd "$ETL"
echo "platforms registered:"
uv run python -c "from etl_core.sources import PLATFORMS; print(', '.join(PLATFORMS))"
echo "=== artworks sync ugallery + phaidon ==="
uv run python -m artworks sync --data-root "$DATA" --platform ugallery --platform phaidon
echo "=== artists sync ugallery + phaidon ==="
uv run python -m artists sync --data-root "$DATA" --platform ugallery --platform phaidon
echo "=== error files (non-empty) ==="
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
REMOTE
