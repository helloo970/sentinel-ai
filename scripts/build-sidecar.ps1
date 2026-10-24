<#
.SYNOPSIS
  Builds the backend with PyInstaller as a Tauri sidecar, then the installer.

.EXAMPLE
  .\scripts\build-sidecar.ps1           # sidecar only
  .\scripts\build-sidecar.ps1 -Bundle   # sidecar + NSIS installer
#>
[CmdletBinding()]
param([switch]$Bundle)
$ErrorActionPreference = "Stop"

# External programs: messages on stderr (e.g. pip hints) are not an error,
# only the exit code matters.
function Invoke-Native([string]$What, [scriptblock]$Block) {
    $previous = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try { & $Block } finally { $ErrorActionPreference = $previous }
    if ($LASTEXITCODE -ne 0) { throw "$What failed (exit code $LASTEXITCODE)" }
}

$repo = Split-Path -Parent $PSScriptRoot
$backend = Join-Path $repo "backend"
$python = Join-Path $backend ".venv\Scripts\python.exe"
$binaries = Join-Path $repo "app\src-tauri\binaries"

Push-Location $backend
try {
    Invoke-Native "pip install" { & $python -m pip install -q -e ".[package]" }
    Invoke-Native "PyInstaller" { & $python -m PyInstaller --noconfirm --clean sentinel-backend.spec }
} finally {
    Pop-Location
}

# Tauri expects the target triple in the file name
$triple = (& rustc -vV | Select-String "host:").ToString().Split(" ")[1]
New-Item -ItemType Directory -Force -Path $binaries | Out-Null
Copy-Item (Join-Path $backend "dist\sentinel-backend.exe") (Join-Path $binaries "sentinel-backend-$triple.exe") -Force
Write-Host "Sidecar: $binaries\sentinel-backend-$triple.exe"

if ($Bundle) {
    Push-Location (Join-Path $repo "app")
    try {
        Invoke-Native "Tauri build" { npm run tauri build -- --config src-tauri/tauri.bundle.conf.json }
    } finally {
        Pop-Location
    }
    $installer = Get-ChildItem (Join-Path $repo "app\src-tauri\target\release\bundle\nsis\*-setup.exe") |
        Sort-Object LastWriteTime -Descending | Select-Object -First 1
    Write-Host "Installer: $($installer.FullName)"
}
