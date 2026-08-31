#requires -Version 5.1
<#
.SYNOPSIS
  Remove the tray's statusLine wiring from ~/.claude/settings.json.
#>
$ErrorActionPreference = 'Stop'

$settings = Join-Path $env:USERPROFILE '.claude\settings.json'
if (-not (Test-Path $settings)) { throw "settings.json not found at $settings" }

$backup = $settings + '.bak-' + (Get-Date -Format 'yyyyMMdd-HHmmss')
Copy-Item -LiteralPath $settings -Destination $backup
Write-Host "Backed up -> $backup"

$json = Get-Content -LiteralPath $settings -Raw -Encoding UTF8 | ConvertFrom-Json
if ($json.PSObject.Properties.Name -contains 'statusLine') {
    $json.PSObject.Properties.Remove('statusLine')
    $out = $json | ConvertTo-Json -Depth 32
    Set-Content -LiteralPath $settings -Value $out -Encoding UTF8
    Write-Host "Removed statusLine from settings.json."
} else {
    Write-Host "No statusLine field present."
}
