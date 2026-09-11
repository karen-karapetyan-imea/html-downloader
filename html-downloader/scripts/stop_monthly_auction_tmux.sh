#!/usr/bin/env bash
# Stop monthly auction crawl tmux sessions (if present).

set -euo pipefail

AUCTION_HOUSES=(invaluable liveauctioneers)

if ! command -v tmux >/dev/null 2>&1; then
  echo "error: tmux is not installed" >&2
  exit 1
fi

for house in "${AUCTION_HOUSES[@]}"; do
  session="crawl-auction-${house}"
  if tmux has-session -t "${session}" 2>/dev/null; then
    tmux kill-session -t "${session}"
    echo "stopped ${session}"
  else
    echo "not running: ${session}"
  fi
done
