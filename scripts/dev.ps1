# GuudFlow dev launcher - kills existing servers then starts both fresh.
# Usage: powershell -ExecutionPolicy Bypass -File scripts\dev.ps1

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

Write-Host "Stopping existing servers..." -ForegroundColor Yellow
Kill-Port 8000
Kill-Port 3000
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
Write-Host "Starting backend  -> http://localhost:8000" -ForegroundColor Cyan
Start-Process pwsh -ArgumentList @(
    "-NoExit", "-Command",
    "Set-Location '$Repo'; & '$activate'; uvicorn api.main:app --reload --reload-dir api --reload-dir core --reload-dir agents --reload-dir worker --port 8000"
)

# Frontend
$studioPath = Join-Path $Repo "studio"
if (-not (Test-Path (Join-Path $studioPath "node_modules"))) {
    Write-Host "Installing studio deps..." -ForegroundColor Yellow
    Push-Location $studioPath
    npm install --silent --no-audit --no-fund
    Pop-Location
}

Write-Host "Starting frontend -> http://localhost:3000" -ForegroundColor Cyan
Start-Process pwsh -ArgumentList @(
    "-NoExit", "-Command",
    "Set-Location '$studioPath'; npm run dev"
)

Write-Host ""
Write-Host "Both servers launching in separate windows." -ForegroundColor Green
Write-Host "  Backend:  http://localhost:8000"
Write-Host "  Frontend: http://localhost:3000"
