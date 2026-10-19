#!/usr/bin/env bash
# Pull small HTML samples for ugallery + phaidon art editions from scraper-01.
# Requires Twingate (tg-arty) and SSH key. Does not copy full crawls.
#
#   scripts/pull_ugallery_phaidon_samples.sh
#   scripts/pull_ugallery_phaidon_samples.sh developer@192.168.244.11
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
HOST="${1:-developer@192.168.244.11}"
SSH=(ssh -i "${HOME}/.ssh/developer_rsa" -o ConnectTimeout=20 "$HOST")
UG_DATE="${UG_DATE:-2026-10-12}"
PH_DATE="${PH_DATE:-2026-10-08}"
REMOTE_DATA="/home/developer/html-downloader/html-downloader/data"

echo "Pulling ugallery samples ($UG_DATE)..."
"$ROOT/scripts/pull_samples.sh" "$HOST" "$REMOTE_DATA/ugallery/$UG_DATE" ugallery 80

echo "Pulling phaidon art-edition samples ($PH_DATE)..."
mkdir -p "$ROOT/samples/phaidon"
"${SSH[@]}" bash -s -- "$REMOTE_DATA/phaidon/$PH_DATE" <<'REMOTE' | tar xzf - -C "$ROOT/samples/phaidon"
set -euo pipefail
cd "$1"
[[ -d html ]] && cd html
# Prefer pages that carry the art-edition block; fall back to a random sample.
mapfile -t hits < <(grep -l -m 1 'art-product-details' ./*.html 2>/dev/null | head -40 || true)
if ((${#hits[@]} == 0)); then
  mapfile -t hits < <(find . -name '*.html' -type f | shuf -n 40)
fi
printf '%s\n' "${hits[@]}" | tar czf - -T -
REMOTE

echo "ugallery: $(find "$ROOT/samples/ugallery" -name '*.html' 2>/dev/null | wc -l) files"
echo "phaidon:  $(find "$ROOT/samples/phaidon" -name '*.html' 2>/dev/null | wc -l) files"
