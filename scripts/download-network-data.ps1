<#
.SYNOPSIS
  Downloads the offline data for the attack map and the device vendors (no admin needed).

.DESCRIPTION
  - IP to country: "IP to Country Lite" from DB-IP (CC BY 4.0, https://db-ip.com)
  - MAC vendors: IEEE OUI list (fallback: Wireshark manuf, same data)
  Both end up in %LOCALAPPDATA%\SentinelAI\geo\. SENTINEL then looks everything up
  locally and sends no addresses to third-party services. Reloading once a month is enough.

.EXAMPLE
  .\scripts\download-network-data.ps1
#>
[CmdletBinding()]
param()
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

$target = Join-Path $env:LOCALAPPDATA "SentinelAI\geo"
New-Item -ItemType Directory -Force -Path $target | Out-Null

function Expand-Gzip([string]$Source, [string]$Destination) {
    $in = [System.IO.File]::OpenRead($Source)
    try {
        $gzip = New-Object System.IO.Compression.GZipStream($in, [System.IO.Compression.CompressionMode]::Decompress)
        $out = [System.IO.File]::Create($Destination)
        try { $gzip.CopyTo($out) } finally { $out.Dispose(); $gzip.Dispose() }
    } finally {
        $in.Dispose()
    }
}

Write-Host "1/2 IP to country (DB-IP Lite) ..."
$geoFile = Join-Path $target "dbip-country-lite.csv"
$tmp = Join-Path $target "dbip.csv.gz"
$loaded = $false
foreach ($offset in 0, -1) {
    $month = (Get-Date).AddMonths($offset).ToString("yyyy-MM")
    try {
        Invoke-WebRequest -Uri "https://download.db-ip.com/free/dbip-country-lite-$month.csv.gz" -OutFile $tmp -UseBasicParsing
        Expand-Gzip $tmp $geoFile
        $loaded = $true
        Write-Host "    As of $month"
        break
    } catch {
        Write-Host "    $month not available yet, trying the previous month ..."
    }
}
Remove-Item $tmp -ErrorAction SilentlyContinue
if (-not $loaded) { throw "Could not download the DB-IP data" }

Write-Host "2/2 MAC vendors ..."
try {
    Invoke-WebRequest -Uri "https://standards-oui.ieee.org/oui/oui.csv" -OutFile (Join-Path $target "oui.csv") -UseBasicParsing
    Write-Host "    Source: IEEE"
} catch {
    # IEEE blocks some scripted downloads - Wireshark publishes the same list
    Invoke-WebRequest -Uri "https://www.wireshark.org/download/automated/data/manuf" -OutFile (Join-Path $target "manuf.txt") -UseBasicParsing
    Write-Host "    Source: Wireshark (manuf)"
}

Write-Host ""
Write-Host "Done. Restart SENTINEL so the data is used."
