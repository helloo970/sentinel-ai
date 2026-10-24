# Adds scripts\bin to the user PATH so `sentinel ...` works in every new terminal.
# User scope only, no admin rights needed. Run once: .\scripts\add-cli-to-path.ps1
$bin = Join-Path $PSScriptRoot "bin"
$current = [Environment]::GetEnvironmentVariable("Path", "User")
$parts = @($current -split ";" | Where-Object { $_ })
if ($parts -contains $bin) {
    Write-Host "Already set up: $bin"
} else {
    [Environment]::SetEnvironmentVariable("Path", (($parts + $bin) -join ";"), "User")
    Write-Host "Done. Open a new terminal, then this works: sentinel abusech login"
}
