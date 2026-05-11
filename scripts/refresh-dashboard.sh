#!/usr/bin/env bash
# Cron entry point — rebuilds the Leveraged ETF Radar dashboard
# (including the live 9Sig panel) and uploads to S3.
#
# Designed for the Mac Mini cron environment: explicit PATH, no shell
# inheritance assumed. Sources ~/.zshenv so GOOGLE_AI_API_KEY etc. are
# picked up if present (Gemini summary degrades gracefully if missing).

set -euo pipefail

PROJECT_DIR="${PROJECT_DIR:-/Users/newsc2/Projects/leveraged-etf-radar}"
LOG_FILE="${PROJECT_DIR}/logs/dashboard-refresh.log"
PYTHON="${PROJECT_DIR}/.venv/bin/python"

export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"
[ -f "${HOME}/.zshenv" ] && . "${HOME}/.zshenv"

mkdir -p "$(dirname "${LOG_FILE}")"

{
  echo
  echo "=== $(date '+%Y-%m-%d %H:%M:%S %Z') — refresh start ==="
  cd "${PROJECT_DIR}"
  "${PYTHON}" export_static.py --upload --no-summary
  echo "=== $(date '+%Y-%m-%d %H:%M:%S %Z') — refresh done ==="
} >> "${LOG_FILE}" 2>&1
