#!/usr/bin/env bash
# Start three tmux sessions so saatchi, artsper, and artsy run in parallel.
#
# Sessions: crawl-saatchi, crawl-artsper, crawl-artsy
# Attach:   tmux attach -t crawl-saatchi
# List:     tmux ls
# Stop one: tmux kill-session -t crawl-saatchi
# Stop all: ./scripts/stop_weekly_tmux.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
MARKETPLACES=(saatchi artsper artsy)

if ! command -v tmux >/dev/null 2>&1; then
  echo "error: tmux is not installed" >&2
  exit 1
fi

mkdir -p "${PROJECT_ROOT}/logs"

for marketplace in "${MARKETPLACES[@]}"; do
  session="crawl-${marketplace}"
  if tmux has-session -t "${session}" 2>/dev/null; then
    echo "session already running: ${session} (skip)"
    continue
  fi
  tmux new-session -d -s "${session}" \
    "cd '${PROJECT_ROOT}' && exec '${SCRIPT_DIR}/run_weekly.sh' '${marketplace}'"
  echo "started ${session}"
done

echo "all sessions:"
tmux ls | grep -E '^crawl-(saatchi|artsper|artsy):' || true
