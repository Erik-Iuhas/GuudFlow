# Windows entry point for the VS Code tasks (and for a PowerShell prompt).
#
#   powershell -ExecutionPolicy Bypass -File scripts/run.ps1 <command> [args]
#
# GUUDFLOW_WINDOWS_MODE picks how GuudFlow runs on this Windows machine:
#   wsl     (default) forward to scripts/run.sh inside WSL. Use this: agent sandboxes need Linux.
#   native  run every task on Windows itself (prepare, python-env, setup, dev, stop, doctor, build, check,
#           api, studio-*). Studio comes from prebuilt\ first (studio-ensure). Agents then run
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

# The one Python step (prepare, setup, dev, api, check): a working venv with requirements.txt.
# Does nothing when the venv is already good. Behind a proxy: HTTPS_PROXY as is, GUUDFLOW_CA_BUNDLE
# (company certificate), GUUDFLOW_PIP_INDEX (PyPI mirror), GUUDFLOW_PYTHON (an installed 3.11/3.12),
# or the python-wheels artifact zip in prebuilt\ for no network at all.
function Py-Minor($exe, [string[]]$pre = @()) {
    $v = & $exe @pre -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>&1
    if ($LASTEXITCODE -eq 0) { return "$v".Trim() } else { return "" }
}
function Find-Python {
    if ($env:GUUDFLOW_PYTHON) { return $env:GUUDFLOW_PYTHON }
    foreach ($want in "3.11", "3.12") {
        if (Get-Command py -ErrorAction SilentlyContinue) {
            $exe = & py "-$want" -c "import sys; print(sys.executable)" 2>&1
            if ($LASTEXITCODE -eq 0) { return "$exe".Trim() }
        }
        foreach ($name in "python3", "python") {
            $c = Get-Command $name -ErrorAction SilentlyContinue
            if ($c -and $c.Source -notlike "*WindowsApps*" -and (Py-Minor $c.Source) -eq $want) { return $c.Source }
        }
    }
    return ""
}
function Python-Env {
    $ErrorActionPreference = "Continue"   # native tools write to stderr; judge them by exit code
    if (-not $env:UV_NATIVE_TLS) { $env:UV_NATIVE_TLS = "1" }   # trust the Windows certificate store
    if ($env:GUUDFLOW_CA_BUNDLE) {
        $env:SSL_CERT_FILE = $env:GUUDFLOW_CA_BUNDLE; $env:PIP_CERT = $env:GUUDFLOW_CA_BUNDLE; $env:REQUESTS_CA_BUNDLE = $env:GUUDFLOW_CA_BUNDLE
    }
    if ($env:GUUDFLOW_PIP_INDEX) { $env:UV_DEFAULT_INDEX = $env:GUUDFLOW_PIP_INDEX; $env:PIP_INDEX_URL = $env:GUUDFLOW_PIP_INDEX }
    $wdir = Join-Path $Repo "prebuilt/wheels"; $wstamp = Join-Path $wdir ".unpacked"
    $zip = Get-ChildItem -LiteralPath (Join-Path $Repo "prebuilt") -File -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -like "python-wheels*.zip" } | Sort-Object LastWriteTime -Descending | Select-Object -First 1
    if ($zip -and (-not (Test-Path $wstamp) -or $zip.LastWriteTime -gt (Get-Item -Force $wstamp).LastWriteTime)) {
        Remove-Item -Recurse -Force $wdir -ErrorAction SilentlyContinue
        Expand-Archive -Path $zip.FullName -DestinationPath $wdir -Force
        Set-Content $wstamp ""
        Write-Host "Python env: wheels from prebuilt\$($zip.Name)"
    }
    $offline = @()
    $whl = Get-ChildItem -LiteralPath $wdir -Recurse -Filter "*.whl" -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($whl) { $offline = @("--no-index", "--find-links", $whl.DirectoryName); $env:UV_OFFLINE = "1"; Write-Host "Python env: installing from $($whl.DirectoryName) (no network)" }

    $hash = (Get-FileHash (Join-Path $Repo "requirements.txt") -Algorithm SHA256).Hash
    $reqStamp = Join-Path $Repo "venv/.guudflow-req"
    $minor = if (Test-Path $Py) { Py-Minor $Py } else { "" }
    if ($minor -in @("3.11", "3.12") -and (Test-Path $reqStamp) -and ((Get-Content $reqStamp -Raw).Trim() -eq $hash)) {
        & $Py -c "import uvicorn, fastapi" 2>&1 | Out-Null
        if ($LASTEXITCODE -eq 0) { Write-Host "Python env: venv is ready (Python $minor)"; return $true }
    }
    $uv = (Get-Command uv -ErrorAction SilentlyContinue) -and $env:GUUDFLOW_NO_UV -ne "1"
    $found = Find-Python
    if (-not $found -and -not $uv) {
        Write-Warning "Python env: no Python 3.11/3.12 and no uv. Install uv (scripts\run.ps1 install-uv) or Python 3.11 (python.org), or set GUUDFLOW_PYTHON."
        return $false
    }
    if ((Test-Path "venv") -and $minor -notin @("3.11", "3.12")) {
        Write-Host "Python env: venv is broken or not Python 3.11/3.12: rebuilding"
        Remove-Item -Recurse -Force "venv"
    }
    if (-not (Test-Path "venv")) {
        $want = if ($found) { $found } else { "3.11" }
        Write-Host "Python env: creating venv (Python $want)"
        if ($uv) { & uv venv --python $want venv | Out-Host } else { & $found -m venv venv | Out-Host }
        if ($LASTEXITCODE -ne 0 -or -not (Test-Path $Py)) {
            Write-Warning ("Python env: could not create the venv. If uv was downloading Python and the proxy blocked it, " +
                           "install Python 3.11 (python.org) and set GUUDFLOW_PYTHON, or set UV_PYTHON_INSTALL_MIRROR.")
            return $false
        }
    }
    Write-Host "Python env: installing requirements.txt"
    if ($uv) { & uv pip install --python $Py @offline -r requirements.txt | Out-Host }
    else { & $Py -m pip install @offline -r requirements.txt | Out-Host }
    if ($LASTEXITCODE -ne 0) {
        Write-Warning ("Python env: package install failed. Behind a proxy: set GUUDFLOW_CA_BUNDLE (company certificate) or " +
                       "GUUDFLOW_PIP_INDEX (a PyPI mirror), or put the python-wheels artifact zip in prebuilt\ (README: Quirks).")
        return $false
    }
    & $Py -c "import uvicorn, fastapi" 2>&1 | Out-Host
    if ($LASTEXITCODE -ne 0) { Write-Warning "Python env: packages installed but uvicorn/fastapi do not import"; return $false }
    Set-Content $reqStamp $hash
    Write-Host "Python env: venv is ready (Python $(Py-Minor $Py))"
    return $true
}
function Need-Venv {
    if (-not (Python-Env)) { throw "No working Python environment (see above; task: Python environment)." }
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
                       "or put a prebuilt Studio archive in prebuilt\ and run: scripts\run.ps1 studio-ensure")
        return $false
    } finally { Pop-Location }
}
function Npm-Build-Studio { if (Studio-Deps) { Push-Location studio; try { & npm run build; if ($LASTEXITCODE -ne 0) { throw "Studio build failed" } } finally { Pop-Location } } else { throw "Studio packages missing" } }
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
    $tmp = $null
    if (-not $file) { $file = "prebuilt\studio-dist.tar.gz" }
    if (-not (Test-Path $file)) { throw "$file not found (put the prebuilt Studio archive in prebuilt\)" }
    if ($file -like "*.zip") {
        $tmp = Join-Path ([System.IO.Path]::GetTempPath()) ("studio-" + [guid]::NewGuid())
        Expand-Archive -Path $file -DestinationPath $tmp -Force
        $inner = Get-ChildItem -Path $tmp -Recurse -Filter "*.tar.gz" | Select-Object -First 1
        if (-not $inner) { Remove-Item -Recurse -Force $tmp; throw "$file has no .tar.gz inside" }
        $file = $inner.FullName
    }
    Remove-Item -Recurse -Force studio/dist.new -ErrorAction SilentlyContinue
    New-Item -ItemType Directory -Force studio/dist.new | Out-Null
    & tar.exe -xzf $file -C studio/dist.new
    $src = if (Test-Path studio/dist.new/dist/index.html) { "studio/dist.new/dist" } else { "studio/dist.new" }
    if (-not (Test-Path "$src/index.html")) { Remove-Item -Recurse -Force studio/dist.new; throw "$file has no Studio build (index.html)" }
    Remove-Item -Recurse -Force studio/dist -ErrorAction SilentlyContinue
    Move-Item $src studio/dist
    Remove-Item -Recurse -Force studio/dist.new -ErrorAction SilentlyContinue
    if ($tmp) { Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue }
    Set-Content "studio/dist/.unpacked" ""   # Studio-Ensure unpacks again only when an archive is newer
    Write-Host "Studio installed in studio/dist"
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
    if (Test-Path "studio\dist\index.html") { Ok "Studio built (studio\dist)" } elseif (Test-Path "studio\node_modules\.bin\vite.cmd") { Ok "Studio packages (dev server)" } else { Bad "no Studio: put studio-dist.tar.gz in prebuilt\ and run studio-ensure" }
    if (Test-Path ".env") {
        Ok ".env exists"
        if (Select-String -Path ".env" -Pattern '^\s*GUUDFLOW_API_TOKEN=.+' -Quiet) { Ok "GUUDFLOW_API_TOKEN set" } else { Bad "GUUDFLOW_API_TOKEN empty in .env" }
        if (Select-String -Path ".env" -Pattern '^\s*GUUDFLOW_SANDBOX=host' -Quiet) { Ok "GUUDFLOW_SANDBOX=host" } else { Warn "set GUUDFLOW_SANDBOX=host in .env (no sandboxes on native Windows)" }
    } else { Bad ".env missing (Setup)" }
    if ($script:DoctorFail) { Write-Host "Fix the [XX] items, then run doctor again."; exit 1 } else { Write-Host "Ready." }
}

# The one Studio step (prepare, setup, dev): an archive in prebuilt\ wins and npm is never run,
# then an existing studio\dist, then npm packages ($build also builds studio\dist).
function Studio-Ensure([bool]$build = $false) {
    # Any .tar.gz / .tgz / .zip in prebuilt\ counts (browsers rename downloads).
    $arch = Get-ChildItem -LiteralPath (Join-Path $Repo "prebuilt") -File -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -match '\.(tar\.gz|tgz|zip)$' -and $_.Name -notlike "python-wheels*" } | Sort-Object LastWriteTime -Descending | Select-Object -First 1
    if ($arch) {
        if (-not (Test-Path "studio/dist/.unpacked") -or $arch.LastWriteTime -gt (Get-Item -Force "studio/dist/.unpacked").LastWriteTime) { Studio-Unpack $arch.FullName }
        Write-Host "Studio: prebuilt from prebuilt\$($arch.Name) (npm skipped)"; return $true
    }
    $vite = (Test-Path "studio/node_modules/.bin/vite.cmd") -or (Test-Path "studio/node_modules/.bin/vite")
    if ((Test-Path "studio/dist/index.html") -and -not $vite) { Write-Host "Studio: using the existing build in studio\dist (npm skipped)"; return $true }
    if ($vite -and -not $build) { Write-Host "Studio: npm packages already installed"; return $true }
    if ($env:GUUDFLOW_SKIP_NPM -ne "1" -and (Get-Command npm -ErrorAction SilentlyContinue)) {
        try { if ($build) { Npm-Build-Studio } elseif (-not (Studio-Deps)) { throw "npm failed" }; return $true } catch { Write-Warning "$_" }
    }
    if (Test-Path "studio/dist/index.html") { Write-Host "Studio: npm failed; keeping the existing studio\dist"; return $true }
    Write-Warning "Studio: not available. Put studio-dist.tar.gz (or the GitHub artifact zip) in $Repo\prebuilt\ and run this again."
    return $false
}

switch ($Command) {
    "prepare" {
        # Native Windows: uv, setup, opencode, Studio (npm build or a prebuilt archive), .env, doctor.
        & powershell -NoProfile -ExecutionPolicy Bypass -File "$PSCommandPath" install-uv
        $env:GUUDFLOW_SKIP_NPM = "1"   # Studio is handled below
        & "$PSScriptRoot\setup.ps1" @Rest
        $env:GUUDFLOW_SKIP_NPM = $null
        if (-not (Test-Path ".tools\opencode\node_modules")) { try { Install-Opencode } catch { Write-Warning "$_" } }
        # The shared Studio step: prebuilt\ first, then npm, then a download into prebuilt\.
        if (-not (Studio-Ensure $true)) {
            try { & powershell -NoProfile -ExecutionPolicy Bypass -File "$PSCommandPath" studio-fetch }
            catch { Write-Warning "No Studio yet: put studio-dist.tar.gz in $Repo\prebuilt\ and run Prepare again." }
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
    "python-env" { if (-not (Python-Env)) { exit 1 } }
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
    "studio-ensure"    { if (-not (Studio-Ensure ($Rest -contains "--build"))) { exit 1 } }
    "studio-deps"      { if (-not (Studio-Deps)) { exit 1 } }
    "studio-unpack"    { Studio-Unpack ($Rest | Select-Object -First 1) }
    "studio-fetch"     {
        $tag = if ($Rest) { $Rest[0] } else { "studio-latest" }
        $slug = $env:GUUDFLOW_REPO
        if (-not $slug) { $slug = ((git remote get-url origin) -replace '\.git$', '' -replace '^.*[:/]([^/]+/[^/]+)$', '$1') }
        $url = "https://github.com/$slug/releases/download/$tag/studio-dist.tar.gz"
        Write-Host "Downloading $url"
        New-Item -ItemType Directory -Force prebuilt | Out-Null
        Invoke-WebRequest -Uri $url -OutFile "prebuilt\studio-dist.tar.gz" -UseBasicParsing
        Studio-Unpack "prebuilt\studio-dist.tar.gz"
    }
    "studio-pack"      {
        if (-not (Test-Path studio\dist\index.html)) { throw "no studio\dist: build it first (scripts\run.ps1 build studio)" }
        $out = if ($Rest) { $Rest[0] } else { "studio-dist.tar.gz" }
        & tar.exe -czf $out -C studio dist; Write-Host "wrote $out"
    }
    "build" {
        $what = if ($Rest) { $Rest[0] } else { "native" }
        switch ($what) {
            "studio"   { if (-not (Studio-Ensure $true)) { exit 1 } }
            "opencode" { Install-Opencode }
            "native"   { if (-not (Studio-Ensure $true)) { exit 1 }; Install-Opencode }
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
