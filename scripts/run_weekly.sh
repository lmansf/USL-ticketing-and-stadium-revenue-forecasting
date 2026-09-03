#!/usr/bin/env bash
# Weekly pipeline run. For cron, launchd, or a systemd timer.
#
# Crontab entry for Tuesday 06:00:
#   0 6 * * 2 /path/to/repo/scripts/run_weekly.sh
#
# cron runs with a minimal environment and an unexpected working directory, which
# is the same trap as Task Scheduler's "Start in" field. This script cds to the
# repo root itself rather than trusting the caller.
#
# See docs/mvp/05-mvp-schedule.md

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG_DIR="${REPO_ROOT}/logs"
STAMP="$(date +%Y-%m-%d_%H%M%S)"
LOG_FILE="${LOG_DIR}/weekly_${STAMP}.log"

mkdir -p "${LOG_DIR}"
cd "${REPO_ROOT}"

# Activate the virtual environment. Adjust if yours is not at .venv.
if [ -f "${REPO_ROOT}/.venv/bin/activate" ]; then
    # shellcheck disable=SC1091
    source "${REPO_ROOT}/.venv/bin/activate"
else
    echo "warning: no virtualenv at .venv - using the system python" >&2
fi

# Exit with the pipeline's code, not tee's, so the scheduler's result is
# meaningful. A run that exits zero having scraped nothing is still a problem;
# the freshness check is what turns that into a non-zero exit.
set +e
python -m usl.run weekly 2>&1 | tee "${LOG_FILE}"
code="${PIPESTATUS[0]}"
set -e

exit "${code}"
