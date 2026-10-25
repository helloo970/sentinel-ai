# Installs the freshly built SENTINEL installer: stops the app, installs silently, starts it again.
# No admin rights needed (per-user install). Run: .\scripts\install-latest.ps1
param(
    [string]$Installer = (Join-Path $PSScriptRoot "..\app\src-tauri\target\release\bundle\nsis\SENTINEL AI_0.1.0_x64-setup.exe")
)
$ErrorActionPreference = "Stop"

if (-not (Test-Path $Installer)) {
    Write-Host "Installer not found: $Installer" -ForegroundColor Red
    Write-Host "Build it first: .\scripts\build-sidecar.ps1 -Bundle"
    exit 1
}

Write-Host "1/3 Stopping SENTINEL ..."
Get-Process sentinel-app, sentinel-backend -ErrorAction SilentlyContinue | Stop-Process -Force
Start-Sleep -Seconds 2

Write-Host "2/3 Installing ..."
$proc = Start-Process -FilePath $Installer -ArgumentList "/S" -PassThru -Wait
if ($proc.ExitCode -ne 0) {
    Write-Host "Installer failed (exit code $($proc.ExitCode))." -ForegroundColor Red
    exit 1
}

Write-Host "3/3 Starting SENTINEL ..."
$app = Join-Path $env:LOCALAPPDATA "SENTINEL AI\sentinel-app.exe"
if (Test-Path $app) {
    Start-Process -FilePath $app
    Write-Host "Done. Say: show me the camera" -ForegroundColor Green
} else {
    Write-Host "Installed, but the app was not found at $app - start it from the Start menu."
}
