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
PYTHON="${PROJECT_ROOT}/.venv/bin/python"
MARKETPLACES=(saatchi artsper artsy)

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

for marketplace in "${MARKETPLACES[@]}"; do
  session="crawl-${marketplace}"
  if tmux has-session -t "${session}" 2>/dev/null; then
    echo "session already running: ${session} (skip)"
    continue
  fi
  # Login-less bash so a broken relocatable activate / shell rc cannot break PATH.
  tmux new-session -d -s "${session}" \
    "cd '${PROJECT_ROOT}' && exec /bin/bash --noprofile --norc '${SCRIPT_DIR}/run_weekly.sh' '${marketplace}'"
  echo "started ${session}"
done

# Give panes a moment to fail-fast (missing deps, bad args) before listing.
sleep 1

echo "all sessions:"
if ! tmux ls 2>/dev/null | grep -E '^crawl-(saatchi|artsper|artsy):'; then
  echo "error: no crawl-* sessions are alive (tmux server may have exited)" >&2
  echo "check latest logs under ${PROJECT_ROOT}/logs/" >&2
  exit 1
fi
