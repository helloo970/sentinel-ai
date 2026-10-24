<#
.SYNOPSIS
  Installs Sysmon with the SENTINEL configuration and gives the current user
  read access to event logs. Run it yourself ONCE as administrator.

.DESCRIPTION
  1. Downloads Sysmon from Microsoft Sysinternals and verifies the Microsoft signature.
  2. Installs or updates Sysmon with scripts\sysmon-config.xml.
  3. Adds the user to the "Event Log Readers" group (S-1-5-32-573).
  After that SENTINEL reads the events as a normal user (sign out and in required).

.EXAMPLE
  # Open PowerShell as administrator, then:
  Set-ExecutionPolicy -Scope Process Bypass
  .\scripts\setup-sysmon.ps1 -User $env:USERNAME
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$User
)
$ErrorActionPreference = "Stop"

$principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw "Please start PowerShell as administrator."
}

$config = Join-Path $PSScriptRoot "sysmon-config.xml"
if (-not (Test-Path $config)) { throw "Configuration missing: $config" }

$work = Join-Path $env:TEMP "sentinel-sysmon"
New-Item -ItemType Directory -Force -Path $work | Out-Null
$zip = Join-Path $work "Sysmon.zip"

Write-Host "1/3 Downloading Sysmon from Microsoft Sysinternals ..."
Invoke-WebRequest -Uri "https://download.sysinternals.com/files/Sysmon.zip" -OutFile $zip -UseBasicParsing
Expand-Archive -Path $zip -DestinationPath $work -Force
$exe = Join-Path $work "Sysmon64.exe"

$signature = Get-AuthenticodeSignature $exe
if ($signature.Status -ne "Valid" -or $signature.SignerCertificate.Subject -notmatch "O=Microsoft Corporation") {
    throw "Signature check failed: $($signature.Status) $($signature.SignerCertificate.Subject)"
}
Write-Host "    Signature valid (Microsoft Corporation)."

Write-Host "2/3 Installing/updating Sysmon ..."
$service = Get-Service -Name Sysmon64 -ErrorAction SilentlyContinue
if ($service) {
    & $exe -c $config
} else {
    & $exe -accepteula -i $config
}
if ($LASTEXITCODE -ne 0) { throw "Sysmon reported error $LASTEXITCODE" }

Write-Host "3/3 Adding $User to group 'Event Log Readers' ..."
$group = Get-LocalGroup -SID "S-1-5-32-573"
$member = Get-LocalGroupMember -Group $group -ErrorAction SilentlyContinue | Where-Object { $_.Name -like "*\$User" }
if (-not $member) {
    Add-LocalGroupMember -Group $group -Member $User
    Write-Host "    added."
} else {
    Write-Host "    already a member."
}

Remove-Item -Recurse -Force $work
Write-Host ""
Write-Host "Done. Please sign out and back in once. Verify (no admin needed):"
Write-Host "  wevtutil qe Microsoft-Windows-Sysmon/Operational /c:1 /rd:true /f:text"
