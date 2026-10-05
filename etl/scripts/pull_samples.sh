#!/usr/bin/env bash
# Copy a random sample of HTML pages from a remote crawl folder into etl/samples/<platform>/.
#
#   scripts/pull_samples.sh <ssh-host> <remote-crawl-dir> <platform> [count]
#   scripts/pull_samples.sh user@crawler /data/saatchi/2026-09-23 saatchi 50
#
# Crawl folders mix artist and artwork pages; tests only check the artist pages, so pull
# enough files to get a few dozen artist pages.
set -euo pipefail

if [[ $# -lt 3 ]]; then
    sed -n '2,8p' "$0"
    exit 2
fi

host=$1
remote_dir=$2
platform=$3
count=${4:-50}
dest="$(cd "$(dirname "$0")/.." && pwd)/samples/$platform"
mkdir -p "$dest"

ssh "$host" bash -s -- "$remote_dir" "$count" <<'REMOTE' | tar xzf - -C "$dest"
set -euo pipefail
cd "$1"
[[ -d html ]] && cd html
find . -name '*.html' -type f | shuf -n "$2" | tar czf - -T -
REMOTE

echo "$(find "$dest" -name '*.html' | wc -l) files in $dest"
