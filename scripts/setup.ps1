# GuudFlow local setup - Windows PowerShell.
#
# Run from the repo root:
#   powershell -ExecutionPolicy Bypass -File scripts\setup.ps1
#
# Or if your policy already allows scripts:
#   .\scripts\setup.ps1

$ErrorActionPreference = "Stop"

$Repo = Split-Path -Parent $PSScriptRoot
Set-Location $Repo

Write-Host "GuudFlow setup" -ForegroundColor Cyan
Write-Host "===============" -ForegroundColor Cyan
Write-Host "Repo: $Repo"
Write-Host ""

# 1-3. Python 3.11, venv and packages: the shared step in run.ps1 (proxy settings, offline wheels
# in prebuilt\). Nothing below works without it, so stop if it fails.
$env:GUUDFLOW_WINDOWS_MODE = "native"   # this script is the native setup; keep run.ps1 here too
& powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot "run.ps1") python-env
if ($LASTEXITCODE -ne 0) { Write-Host "ERROR: no working Python environment; fix the message above and run setup again" -ForegroundColor Red; exit 1 }
& (Join-Path $Repo "venv\Scripts\Activate.ps1")

# Optional embeddings (heavy - pulls PyTorch). Off by default; opt in
# with $env:GUUDFLOW_WITH_EMBEDDINGS=1.
if ($env:GUUDFLOW_WITH_EMBEDDINGS -eq "1") {
    Write-Host "Installing optional embedding dependencies (this is heavy)..."
    & python -m pip install --quiet -r requirements-optional.txt
}

# 4. Playwright browsers (skippable)
if ($env:GUUDFLOW_SKIP_PLAYWRIGHT -ne "1") {
    Write-Host "Installing Playwright chromium (set GUUDFLOW_SKIP_PLAYWRIGHT=1 to skip)..."
    try {
        & python -m playwright install chromium
    } catch {
        Write-Warning "playwright install failed - re-run later if you need visual_checker"
    }
}

# 5. Studio (optional): the shared step. An archive in prebuilt\ wins and npm is never run.
if ($env:GUUDFLOW_SKIP_NPM -ne "1") {
    & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot "run.ps1") studio-ensure
    if ($LASTEXITCODE -ne 0) { Write-Host "WARN: no Studio yet. Put studio-dist.tar.gz (or the artifact zip) in prebuilt\ and run: scripts\run.ps1 studio-ensure" -ForegroundColor Yellow }
}

# 6. .env
$freshInstall = $false
if (-not (Test-Path ".env")) {
    $freshInstall = $true
    if (Test-Path ".env.example") {
        Copy-Item ".env.example" ".env"
        Write-Host "Created .env from .env.example"
    } else {
        @"
ANTHROPIC_API_KEY=
GUUDFLOW_API_TOKEN=
GUUDFLOW_MODE=dev
"@ | Set-Content -Path ".env" -Encoding UTF8
        Write-Host "Created .env (no .env.example found)"
    }
} else {
    Write-Host ".env already exists - leaving alone"
}

# 7. Seed default personas on first install (idempotent - won't
# overwrite existing personas; safe to skip on re-run).
if ($freshInstall -or $env:GUUDFLOW_FORCE_SEED -eq "1") {
    Write-Host "Seeding default personas..."
    try {
        & python scripts/seed_data.py
    } catch {
        Write-Warning "seed_data.py failed - you can re-run manually"
    }
}

Write-Host ""
Write-Host "Setup complete." -ForegroundColor Green
Write-Host ""
Write-Host "Next steps:"
Write-Host "  1. Edit .env and add your ANTHROPIC_API_KEY"
Write-Host "  2. Activate the venv:    venv\Scripts\Activate.ps1"
Write-Host "  3. Start the backend:    uvicorn api.main:app --reload --port 8000"
Write-Host "  4. Start the frontend:   cd studio; npm run dev"
Write-Host "  5. Open the studio:      http://localhost:3001"
Write-Host ""
Write-Host "Or run the full stack via Docker:"
Write-Host "  docker compose up --build"
