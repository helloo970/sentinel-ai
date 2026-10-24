<#
.SYNOPSIS
  Starts SENTINEL in development mode: Tauri starts Vite and the backend itself.
#>
$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
Push-Location (Join-Path $repo "app")
try {
    npm run tauri dev
} finally {
    Pop-Location
}
