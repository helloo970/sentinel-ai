<#
.SYNOPSIS
  Installs the optional voice packages and downloads the local models (no admin needed).

.DESCRIPTION
  - pip install -e ".[voice]" in the backend venv
  - openWakeWord: model "hey_jarvis"
  - Piper: English voice (default: en_US-lessac-medium) from huggingface.co/rhasspy
  - faster-whisper downloads its model itself on first start (default: small, ~470 MB)

.EXAMPLE
  .\scripts\download-voice-models.ps1                    # American English voice
  .\scripts\download-voice-models.ps1 -Preset british    # calm British voice
  .\scripts\download-voice-models.ps1 -Voice en_US-amy-medium
#>
[CmdletBinding()]
param(
    [ValidateSet("american", "british")][string]$Preset = "american",
    [string]$Voice = ""
)
# Similar style to an AI butler, but free Piper voices - not a real film/actor voice
$presets = @{ "american" = "en_US-lessac-medium"; "british" = "en_GB-alan-medium" }
if (-not $Voice) { $Voice = $presets[$Preset] }
$ErrorActionPreference = "Stop"

# External programs: stderr hints are not an error, only the exit code counts
function Invoke-Native([string]$What, [scriptblock]$Block) {
    $previous = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try { & $Block } finally { $ErrorActionPreference = $previous }
    if ($LASTEXITCODE -ne 0) { throw "$What failed (exit code $LASTEXITCODE)" }
}

$repo = Split-Path -Parent $PSScriptRoot
$python = Join-Path $repo "backend\.venv\Scripts\python.exe"
if (-not (Test-Path $python)) { throw "Backend venv missing: $python" }

Write-Host "1/3 Installing voice packages ..."
Push-Location (Join-Path $repo "backend")
Invoke-Native "pip install" { & $python -m pip install -e ".[voice]" }
Pop-Location

Write-Host "2/3 Wake word model (hey_jarvis) ..."
Invoke-Native "Wake word download" { & $python -c "import openwakeword.utils as u; u.download_models(['hey_jarvis_v0.1'])" }

Write-Host "3/3 Piper voice $Voice ..."
if ($Voice -notmatch '^(?<lang>[a-z]{2})_(?<region>[A-Z]{2})-(?<name>[a-z_]+)-(?<quality>x_low|low|medium|high)$') {
    throw "Invalid voice name: $Voice"
}
$target = Join-Path $env:LOCALAPPDATA "SentinelAI\voices"
New-Item -ItemType Directory -Force -Path $target | Out-Null
$base = "https://huggingface.co/rhasspy/piper-voices/resolve/main/$($Matches.lang)/$($Matches.lang)_$($Matches.region)/$($Matches.name)/$($Matches.quality)/$Voice"
foreach ($suffix in @(".onnx", ".onnx.json")) {
    Invoke-WebRequest -Uri "$base$suffix" -OutFile (Join-Path $target "$Voice$suffix") -UseBasicParsing
}

$onnx = Join-Path $target "$Voice.onnx"
Write-Host ""
Write-Host "Done. Add to the .env:"
Write-Host "  SENTINEL_VOICE_ENABLED=true"
Write-Host "  SENTINEL_PIPER_VOICE=$($onnx -replace '\\','/')"
