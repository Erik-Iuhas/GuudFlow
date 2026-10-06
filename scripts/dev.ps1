# GuudFlow dev launcher - kills existing servers then starts both fresh.
# Usage:
#   powershell -ExecutionPolicy Bypass -File scripts\dev.ps1
#   powershell -ExecutionPolicy Bypass -File scripts\dev.ps1 -NoReload
#
# Pass -NoReload to launch uvicorn WITHOUT --reload. Use this while
# actively testing agent runs so source-code edits don't restart the
# worker mid-iteration and cancel your in-flight agents. The dashboard
# will show the run die with cancelled=True every time uvicorn restarts.

param(
    [switch]$NoReload
)

$ErrorActionPreference = "Stop"
$Repo = Split-Path -Parent $PSScriptRoot
Set-Location $Repo

function Kill-Port($port) {
    $pids = netstat -ano | Select-String ":$port\s" | ForEach-Object {
        ($_ -split '\s+')[-1]
    } | Sort-Object -Unique
    foreach ($p in $pids) {
        if ($p -match '^\d+$' -and $p -ne '0') {
            try { Stop-Process -Id $p -Force -ErrorAction SilentlyContinue } catch {}
        }
    }
}

function Kill-LeftoverUvicorn {
    # uvicorn --reload spawns a watcher + a worker subprocess. If the watcher
    # gets killed by Kill-Port (port 8000) but the worker hasn't bound to the
    # port yet (mid-startup, mid-restart), the worker can linger. Sweep any
    # python.exe whose command line mentions uvicorn + api.main.
    Get-CimInstance Win32_Process -Filter "name like '%python%'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -and $_.CommandLine -match 'uvicorn' -and $_.CommandLine -match 'api\.main' } |
        ForEach-Object {
            try { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue } catch {}
        }
}

Write-Host "Stopping existing servers..." -ForegroundColor Yellow
Kill-Port 8000
Kill-Port 3001
Kill-LeftoverUvicorn
Start-Sleep -Milliseconds 500

# Ensure venv exists
$activate = Join-Path $Repo "venv\Scripts\Activate.ps1"
if (-not (Test-Path $activate)) {
    Write-Host "No venv found - creating one..." -ForegroundColor Yellow
    python -m venv venv
    & $activate
    python -m pip install --quiet --upgrade pip
    python -m pip install --quiet -r requirements.txt
}

# Backend
$reloadArgs = if ($NoReload) {
    Write-Host "Starting backend  -> http://localhost:8000  [NO RELOAD - safe for agent testing]" -ForegroundColor Yellow
    ""
} else {
    Write-Host "Starting backend  -> http://localhost:8000  [reload watches api/core/agents/worker]" -ForegroundColor Cyan
    # NOTE: do NOT pass --reload-include — it OVERRIDES the --reload-dir
    # scoping and makes watchfiles see *.py files anywhere in the project,
    # including data/runs/<id>/workspace/working/**/*.py written by agents.
    # That's how src/constants.py writes were killing the worker mid-run.
    "--reload --reload-dir api --reload-dir core --reload-dir agents --reload-dir worker --reload-exclude *.pyc --reload-exclude __pycache__/*"
}
Start-Process pwsh -ArgumentList @(
    "-NoExit", "-Command",
    "Set-Location '$Repo'; & '$activate'; uvicorn api.main:app $reloadArgs --port 8000"
)

# Frontend
$studioPath = Join-Path $Repo "studio"
if (-not (Test-Path (Join-Path $studioPath "node_modules"))) {
    Write-Host "Installing studio deps..." -ForegroundColor Yellow
    Push-Location $studioPath
    npm install --silent --no-audit --no-fund
    Pop-Location
}

Write-Host "Starting frontend -> http://localhost:3001" -ForegroundColor Cyan
Start-Process pwsh -ArgumentList @(
    "-NoExit", "-Command",
    "Set-Location '$studioPath'; npm run dev"
)

Write-Host ""
Write-Host "Both servers launching in separate windows." -ForegroundColor Green
Write-Host "  Backend:  http://localhost:8000"
Write-Host "  Frontend: http://localhost:3001"
