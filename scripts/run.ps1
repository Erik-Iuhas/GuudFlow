# Windows entry point for the VS Code tasks (and for a PowerShell prompt).
#
#   powershell -ExecutionPolicy Bypass -File scripts/run.ps1 <command> [args]
#
# GUUDFLOW_WINDOWS_MODE picks how GuudFlow runs on this Windows machine:
#   wsl     (default) forward to scripts/run.sh inside WSL. Use this: agent sandboxes need Linux.
#   native  run the PowerShell scripts on Windows itself (setup, dev, stop). Agents then run
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

switch ($Command) {
    "prepare" {
        # Native Windows: uv, then setup.ps1; agents run in host mode (no sandbox here).
        & "$PSCommandPath" install-uv
        $env:Path = "$env:USERPROFILE\.local\bin;$env:Path"
        & "$PSScriptRoot\setup.ps1" @Rest
        if ((Test-Path ".env") -and -not (Select-String -Path ".env" -Pattern '^\s*GUUDFLOW_SANDBOX=' -Quiet)) {
            Add-Content ".env" "`nGUUDFLOW_SANDBOX=host"
            Write-Host "wrote GUUDFLOW_SANDBOX=host to .env (no sandboxes on native Windows)"
        }
        if (-not (Test-Path "studio\dist\index.html")) {
            $arch = Get-ChildItem -Filter "studio-dist*.tar.gz" -ErrorAction SilentlyContinue | Select-Object -First 1
            if ($arch) { New-Item -ItemType Directory -Force studio\dist | Out-Null; tar -xzf $arch.FullName -C studio; Write-Host "Studio installed from $($arch.Name)" }
            else { Write-Host "No Studio build yet: build it (npm) or put studio-dist.tar.gz in this folder and run Prepare again." }
        }
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
    default {
        Write-Error ("'$Command' has no native Windows version. Use WSL (GUUDFLOW_WINDOWS_MODE=wsl), " +
                     "or run it from a WSL terminal: bash scripts/run.sh $Command")
        exit 2
    }
}
