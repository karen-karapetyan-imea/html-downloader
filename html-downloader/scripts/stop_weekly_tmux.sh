#!/usr/bin/env bash
# Stop the three weekly crawl tmux sessions (if present).

set -euo pipefail

MARKETPLACES=(saatchi artsper artsy)

if ! command -v tmux >/dev/null 2>&1; then
  echo "error: tmux is not installed" >&2
  exit 1
fi

for marketplace in "${MARKETPLACES[@]}"; do
  session="crawl-${marketplace}"
  if tmux has-session -t "${session}" 2>/dev/null; then
    tmux kill-session -t "${session}"
    echo "stopped ${session}"
  else
    echo "not running: ${session}"
  fi
done
