# Weekly pipeline run. Registered in Windows Task Scheduler for Tuesday morning.
#
# Register with:
#   Program:   powershell.exe
#   Arguments: -NoProfile -ExecutionPolicy Bypass -File "C:\path\to\repo\scripts\run_weekly.ps1"
#   Start in:  C:\path\to\repo
#
# The "Start in" field is the one people miss. Without it the working directory is
# C:\Windows\System32 and relative paths resolve somewhere unexpected.
#
# See docs/mvp/05-mvp-schedule.md

$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $PSScriptRoot
$LogDir   = Join-Path $RepoRoot "logs"
$Stamp    = Get-Date -Format "yyyy-MM-dd_HHmmss"
$LogFile  = Join-Path $LogDir "weekly_$Stamp.log"

New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

Set-Location $RepoRoot

# Activate the virtual environment. Adjust if yours is not at .venv.
$Activate = Join-Path $RepoRoot ".venv\Scripts\Activate.ps1"
if (Test-Path $Activate) {
    & $Activate
} else {
    Write-Warning "no virtualenv at $Activate - using the system python"
}

# Tee so the run is visible if you are watching, and captured if you are not.
python -m usl.run weekly 2>&1 | Tee-Object -FilePath $LogFile

$Code = $LASTEXITCODE

# Exit with the pipeline's code so Task Scheduler's Last Run Result means
# something. A run that exits zero having scraped nothing is still a problem -
# that is what the freshness check is for, and it exits non-zero when it fails.
exit $Code
