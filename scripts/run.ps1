# Windows entry point for the VS Code tasks (and for a PowerShell prompt).
#
#   powershell -ExecutionPolicy Bypass -File scripts/run.ps1 <command> [args]
#
# GUUDFLOW_WINDOWS_MODE picks how GuudFlow runs on this Windows machine:
#   wsl     (default) forward to scripts/run.sh inside WSL. Use this: agent sandboxes need Linux.
#   native  run every task on Windows itself (prepare, setup, dev, stop, doctor, build, check,
#           api, studio-*). Agents then run
#           in host mode with no sandbox, so only for trusted work.
# Set it in your environment or as a line in .env (GUUDFLOW_WINDOWS_MODE=native).
param([Parameter(Position = 0)][string]$Command = "help",
      [Parameter(ValueFromRemainingArguments = $true)][string[]]$Rest = @())
$ErrorActionPreference = "Stop"
$Repo = Split-Path -Parent $PSScriptRoot
Set-Location $Repo

$mode = $env:GUUDFLOW_WINDOWS_MODE
if (-not $mode -and (Test-Path ".env")) {
    $line = Select-String -Path ".env" -Pattern '^\s*GUUDFLOW_WINDOWS_MODE\s*=\s*(\S+)' | Select-Object -First 1
    if ($line) { $mode = $line.Matches[0].Groups[1].Value.Trim('"', "'") }
}
if (-not $mode) { $mode = "wsl" }

if ($mode -eq "wsl") {
    if (-not (Get-Command wsl.exe -ErrorAction SilentlyContinue)) {
        Write-Error "WSL is not installed. Install it (wsl --install), or set GUUDFLOW_WINDOWS_MODE=native."
        exit 1
    }
    # --cd takes a Windows path, including \\wsl.localhost\... folders.
    & wsl.exe --cd "$Repo" bash scripts/run.sh $Command @Rest
    exit $LASTEXITCODE
}

# ---- native Windows ----------------------------------------------------------------------
$env:Path = "$env:USERPROFILE\.local\bin;$env:Path"
$Py = Join-Path $Repo "venv\Scripts\python.exe"

function Need-Venv {
    if (-not (Test-Path $Py)) { throw "No venv yet: run 'scripts\run.ps1 setup' (or the Setup task) first." }
}
function Npm-Args { $a = @("--no-audit", "--no-fund", "--fetch-retries=5", "--fetch-retry-maxtimeout=120000")
    if ($env:GUUDFLOW_NPM_REGISTRY) { $a += @("--registry", $env:GUUDFLOW_NPM_REGISTRY) }; return $a }
function Studio-Deps {
    if (-not (Get-Command npm -ErrorAction SilentlyContinue)) { Write-Warning "npm not found (Node 18+)"; return $false }
    Push-Location studio
    try {
        $na = Npm-Args
        & npm ci @na;                          if ($LASTEXITCODE -eq 0) { return $true }
        Write-Warning "exact lockfile versions unavailable; trying versions package.json allows"
        & npm install @na;                     if ($LASTEXITCODE -eq 0) { git checkout -q -- package-lock.json 2>$null; return $true }
        & npm install --legacy-peer-deps @na;  if ($LASTEXITCODE -eq 0) { git checkout -q -- package-lock.json 2>$null; return $true }
        Write-Warning ("Studio npm packages could not be installed. Set GUUDFLOW_NPM_REGISTRY to a mirror, " +
                       "or unpack a prebuilt Studio: scripts\run.ps1 studio-unpack studio-dist.tar.gz")
        return $false
    } finally { Pop-Location }
}
function Build-Studio { if (Studio-Deps) { Push-Location studio; try { & npm run build; if ($LASTEXITCODE -ne 0) { throw "Studio build failed" } } finally { Pop-Location } } else { throw "Studio packages missing" } }
function Install-Opencode {
    $ver = (Select-String -Path "core\harness\server.py" -Pattern 'OPENCODE_VERSION = "([^"]+)"').Matches[0].Groups[1].Value
    New-Item -ItemType Directory -Force ".tools\opencode" | Out-Null
    Push-Location ".tools\opencode"
    try {
        if (-not (Test-Path package.json)) { & npm init -y | Out-Null }
        $na = Npm-Args
        & npm install --save-exact "opencode-ai@$ver" @na
        if ($LASTEXITCODE -ne 0) { throw "opencode install failed" }
        Write-Host "opencode $ver installed in .tools\opencode"
    } finally { Pop-Location }
}
function Studio-Unpack($file) {
    if (-not $file) { $file = "studio-dist.tar.gz" }
    if (-not (Test-Path $file)) { throw "$file not found (put the prebuilt Studio archive in the repo folder)" }
    if ($file -like "*.zip") {
        $tmp = Join-Path $env:TEMP ("studio-" + [guid]::NewGuid())
        Expand-Archive -Path $file -DestinationPath $tmp -Force
        $inner = Get-ChildItem -Path $tmp -Recurse -Filter "*.tar.gz" | Select-Object -First 1
        if (-not $inner) { Remove-Item -Recurse -Force $tmp; throw "$file has no .tar.gz inside" }
        Move-Item -Force $inner.FullName "studio-dist.tar.gz"; Remove-Item -Recurse -Force $tmp
        $file = "studio-dist.tar.gz"
    }
    Remove-Item -Recurse -Force studio\dist.new -ErrorAction SilentlyContinue
    New-Item -ItemType Directory -Force studio\dist.new | Out-Null
    & tar.exe -xzf $file -C studio\dist.new
    $src = if (Test-Path studio\dist.new\dist\index.html) { "studio\dist.new\dist" } else { "studio\dist.new" }
    if (-not (Test-Path "$src\index.html")) { Remove-Item -Recurse -Force studio\dist.new; throw "$file has no Studio build (index.html)" }
    Remove-Item -Recurse -Force studio\dist -ErrorAction SilentlyContinue
    Move-Item $src studio\dist
    Remove-Item -Recurse -Force studio\dist.new -ErrorAction SilentlyContinue
    Write-Host "Studio installed in studio\dist"
}
function Doctor {
    $script:DoctorFail = $false
    function Ok($m) { Write-Host "  [ok] $m" -ForegroundColor Green }
    function Warn($m) { Write-Host "  [!!] $m" -ForegroundColor Yellow }
    function Bad($m) { Write-Host "  [XX] $m" -ForegroundColor Red; $script:DoctorFail = $true }
    Write-Host "Mode: native Windows (agents run in host mode, no sandbox)"
    if (Get-Command uv -ErrorAction SilentlyContinue) { Ok "uv $(uv --version)" } else { Bad "uv not found (scripts\run.ps1 install-uv)" }
    if (Get-Command git -ErrorAction SilentlyContinue) { Ok "git" } else { Bad "git not found" }
    if (Get-Command npm -ErrorAction SilentlyContinue) { Ok "npm $(npm --version)" } else { Warn "npm not found: use a prebuilt Studio" }
    if (Test-Path $Py) { Ok "venv" } else { Bad "no venv (Setup)" }
    if ((Test-Path ".tools\opencode\node_modules\opencode-windows-x64") -or (Test-Path ".tools\opencode\node_modules\.bin\opencode.cmd")) { Ok "opencode installed" } else { Bad "opencode missing (scripts\run.ps1 install-opencode)" }
    if (Test-Path "studio\dist\index.html") { Ok "Studio built (studio\dist)" } elseif (Test-Path "studio\node_modules") { Ok "Studio packages (dev server)" } else { Bad "no Studio: build it or studio-unpack an archive" }
    if (Test-Path ".env") {
        Ok ".env exists"
        if (Select-String -Path ".env" -Pattern '^\s*GUUDFLOW_API_TOKEN=.+' -Quiet) { Ok "GUUDFLOW_API_TOKEN set" } else { Bad "GUUDFLOW_API_TOKEN empty in .env" }
        if (Select-String -Path ".env" -Pattern '^\s*GUUDFLOW_SANDBOX=host' -Quiet) { Ok "GUUDFLOW_SANDBOX=host" } else { Warn "set GUUDFLOW_SANDBOX=host in .env (no sandboxes on native Windows)" }
    } else { Bad ".env missing (Setup)" }
    if ($script:DoctorFail) { Write-Host "Fix the [XX] items, then run doctor again."; exit 1 } else { Write-Host "Ready." }
}

switch ($Command) {
    "prepare" {
        # Native Windows: uv, setup, opencode, Studio (npm build or a prebuilt archive), .env, doctor.
        & powershell -NoProfile -ExecutionPolicy Bypass -File "$PSCommandPath" install-uv
        $env:GUUDFLOW_SKIP_NPM = "1"   # Studio is handled below
        & "$PSScriptRoot\setup.ps1" @Rest
        $env:GUUDFLOW_SKIP_NPM = $null
        if (-not (Test-Path ".tools\opencode\node_modules")) { try { Install-Opencode } catch { Write-Warning "$_" } }
        # A prebuilt archive in the folder wins (tar.gz, or the GitHub artifact zip), then npm, then download.
        $archive = Get-ChildItem -Path ".\*" -Include "studio-dist*.tar.gz", "studio-dist*.zip" -File -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending | Select-Object -First 1
        $built = $false
        if ($archive) { Studio-Unpack $archive.FullName; $built = $true; Write-Host "Studio installed from $($archive.Name) (npm skipped)" }
        else { try { Build-Studio; $built = $true; Write-Host "Studio built from npm packages" } catch { Write-Warning "$_" } }
        if (-not $built -and -not (Test-Path "studio\dist\index.html")) {
            try { & powershell -NoProfile -ExecutionPolicy Bypass -File "$PSCommandPath" studio-fetch }
            catch { Write-Warning "No Studio yet: put studio-dist.tar.gz in this folder and run Prepare again." }
        }
        if (Test-Path ".env") {
            if (-not (Select-String -Path ".env" -Pattern '^\s*GUUDFLOW_SANDBOX=' -Quiet)) {
                Add-Content ".env" "`nGUUDFLOW_SANDBOX=host"
                Write-Host "wrote GUUDFLOW_SANDBOX=host to .env (no sandboxes on native Windows)"
            }
            if (-not (Select-String -Path ".env" -Pattern '^\s*GUUDFLOW_API_TOKEN=.+' -Quiet)) {
                $tok = -join ((1..48) | ForEach-Object { "0123456789abcdef"[(Get-Random -Maximum 16)] })
                $text = (Get-Content ".env") -replace '^\s*GUUDFLOW_API_TOKEN=.*$', "GUUDFLOW_API_TOKEN=$tok"
                if (-not ($text -match '^GUUDFLOW_API_TOKEN=')) { $text += "GUUDFLOW_API_TOKEN=$tok" }
                Set-Content ".env" $text
                Write-Host "generated GUUDFLOW_API_TOKEN in .env (paste it in Studio, top right)"
            }
        }
        Doctor
    }
    "setup"  { & "$PSScriptRoot\setup.ps1" @Rest }
    "dev"    { & "$PSScriptRoot\dev.ps1" @Rest }
    "stop"   {
        foreach ($port in 8000, 3001) {
            Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue |
                ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }
        }
        Write-Host "stopped anything on :8000 and :3001"
    }
    "install-uv" {
        if (Get-Command uv -ErrorAction SilentlyContinue) { Write-Host "uv already installed: $(uv --version)" }
        else { powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex" }
    }
    "doctor"           { Doctor }
    "install-opencode" { Install-Opencode }
    "studio-deps"      { if (-not (Studio-Deps)) { exit 1 } }
    "studio-unpack"    { Studio-Unpack ($Rest | Select-Object -First 1) }
    "studio-fetch"     {
        $tag = if ($Rest) { $Rest[0] } else { "studio-latest" }
        $slug = $env:GUUDFLOW_REPO
        if (-not $slug) { $slug = ((git remote get-url origin) -replace '\.git$', '' -replace '^.*[:/]([^/]+/[^/]+)$', '$1') }
        $url = "https://github.com/$slug/releases/download/$tag/studio-dist.tar.gz"
        Write-Host "Downloading $url"
        Invoke-WebRequest -Uri $url -OutFile "studio-dist.tar.gz" -UseBasicParsing
        Studio-Unpack "studio-dist.tar.gz"
    }
    "studio-pack"      {
        if (-not (Test-Path studio\dist\index.html)) { throw "no studio\dist: build it first (scripts\run.ps1 build studio)" }
        $out = if ($Rest) { $Rest[0] } else { "studio-dist.tar.gz" }
        & tar.exe -czf $out -C studio dist; Write-Host "wrote $out"
    }
    "build" {
        $what = if ($Rest) { $Rest[0] } else { "native" }
        switch ($what) {
            "studio"   { Build-Studio }
            "opencode" { Install-Opencode }
            "native"   { Build-Studio; Install-Opencode }
            "docker"   { & docker compose build }
            default    { throw "build '$what' is not available on native Windows (sandbox images need Linux)" }
        }
    }
    "check" {
        Need-Venv
        $what = if ($Rest) { $Rest[0] } else { "all" }
        if ($what -in @("all", "backend")) { $env:GUUDFLOW_SKIP_DOCKER_TESTS = "1"; & $Py -m pytest -q; if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE } }
        if ($what -in @("all", "studio")) { Push-Location studio; try { & npm run typecheck; & npm test } finally { Pop-Location } }
        if ($what -eq "escape") { throw "the escape suite needs Docker on Linux (use WSL)" }
    }
    "api" {
        Need-Venv
        $env:GUUDFLOW_STUDIO_DIST = "studio\dist"
        & $Py -m uvicorn api.main:app --port 8000
    }
    default {
        Write-Error ("'$Command' has no native Windows version. Use WSL (GUUDFLOW_WINDOWS_MODE=wsl), " +
                     "or run it from a WSL terminal: bash scripts/run.sh $Command")
        exit 2
    }
}
