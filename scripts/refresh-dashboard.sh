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

# NYSE full-day closings. Refresh annually — extra entries are harmless,
# missing entries just trigger one extra refresh on an empty market.
NYSE_HOLIDAYS=(
  # 2026
  "2026-01-01"  # New Year's Day
  "2026-01-19"  # MLK Day
  "2026-02-16"  # Presidents' Day
  "2026-04-03"  # Good Friday
  "2026-05-25"  # Memorial Day
  "2026-06-19"  # Juneteenth
  "2026-07-03"  # Independence Day (observed)
  "2026-09-07"  # Labor Day
  "2026-11-26"  # Thanksgiving
  "2026-12-25"  # Christmas
  # 2027
  "2027-01-01"  # New Year's Day
  "2027-01-18"  # MLK Day
  "2027-02-15"  # Presidents' Day
  "2027-03-26"  # Good Friday
  "2027-05-31"  # Memorial Day
  "2027-07-05"  # Independence Day (observed)
  "2027-09-06"  # Labor Day
  "2027-11-25"  # Thanksgiving
  "2027-12-24"  # Christmas (observed)
)

TODAY=$(date +%Y-%m-%d)
for holiday in "${NYSE_HOLIDAYS[@]}"; do
  if [ "${holiday}" = "${TODAY}" ]; then
    echo "$(date '+%Y-%m-%d %H:%M:%S %Z') — NYSE closed (${TODAY}); skipping refresh" >> "${LOG_FILE}"
    exit 0
  fi
done

{
  echo
  echo "=== $(date '+%Y-%m-%d %H:%M:%S %Z') — refresh start ==="
  cd "${PROJECT_DIR}"

  # Pull latest before building. This is what eliminates the race where
  # an MBP commit + push could be clobbered by a Mac Mini cron build of
  # the previous SHA. If the pull fails (offline, GitHub hiccup), we
  # log it and continue with whatever code is already checked out.
  if ! git pull --quiet origin main; then
    echo "WARNING: git pull failed; building with HEAD=$(git rev-parse --short HEAD)"
  fi

  "${PYTHON}" export_static.py --upload --no-summary
  echo "=== $(date '+%Y-%m-%d %H:%M:%S %Z') — refresh done ==="
} >> "${LOG_FILE}" 2>&1
