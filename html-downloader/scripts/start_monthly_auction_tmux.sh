#!/usr/bin/env bash
# Start monthly auction crawl tmux sessions.
#
# Sessions: crawl-auction-invaluable
# Attach:   tmux attach -t crawl-auction-invaluable
# List:     tmux ls
# Stop one: tmux kill-session -t crawl-auction-invaluable
# Stop all: ./scripts/stop_monthly_auction_tmux.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
PYTHON="${PROJECT_ROOT}/.venv/bin/python"
AUCTION_HOUSES=(invaluable)

if ! command -v tmux >/dev/null 2>&1; then
  echo "error: tmux is not installed" >&2
  exit 1
fi

if [[ ! -x "${PYTHON}" ]]; then
  echo "error: missing venv python at ${PYTHON}" >&2
  echo "create it with: cd ${PROJECT_ROOT} && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt" >&2
  exit 1
fi

mkdir -p "${PROJECT_ROOT}/logs"

for house in "${AUCTION_HOUSES[@]}"; do
  session="crawl-auction-${house}"
  if tmux has-session -t "${session}" 2>/dev/null; then
    echo "session already running: ${session} (skip)"
    continue
  fi
  tmux new-session -d -s "${session}" \
    "cd '${PROJECT_ROOT}' && exec /bin/bash --noprofile --norc '${SCRIPT_DIR}/run_monthly_auction.sh' '${house}'"
  echo "started ${session}"
done

sleep 1

echo "auction sessions:"
if ! tmux ls 2>/dev/null | grep -E '^crawl-auction-'; then
  echo "error: no crawl-auction-* sessions are alive (tmux server may have exited)" >&2
  echo "check latest logs under ${PROJECT_ROOT}/logs/" >&2
  exit 1
fi
tmux ls 2>/dev/null | grep -E '^crawl-auction-' || true
