﻿#requires -Version 5.1
<#
.SYNOPSIS
  Add this project's statusLine command to ~/.claude/settings.json so Claude Code
  pipes the authoritative rate_limits JSON into our snapshot file.

.DESCRIPTION
  Backs up the existing settings.json next to itself, then merges in:

      "statusLine": {
        "type": "command",
        "command": "pythonw \"<repo>\claude_status_writer.py\""
      }

  Re-running is safe (idempotent).

.NOTES
  Run from PowerShell:
      cd <クローンしたフォルダ>
      .\install-statusline.ps1
#>

$ErrorActionPreference = 'Stop'

$repo = Split-Path -Parent $MyInvocation.MyCommand.Path
$srcWriter = Join-Path $repo 'claude_status_writer.py'
if (-not (Test-Path $srcWriter)) { throw "claude_status_writer.py not found at $srcWriter" }

$settings = Join-Path $env:USERPROFILE '.claude\settings.json'
if (-not (Test-Path $settings)) { throw "settings.json not found at $settings" }

# Stage the writer in an ASCII-only path. cmd.exe (which Claude Code uses to
# invoke statusLine) cannot reliably parse paths containing non-ASCII bytes on
# Japanese Windows (cp932 codepage), and the cmd /c double-quote rule also
# corrupts commands wrapped in two quoted tokens. Solving both by copying the
# script to %LOCALAPPDATA%\claude-codex-tray and using bare paths (no spaces,
# no quotes needed).
$stageDir = Join-Path $env:LOCALAPPDATA 'claude-codex-tray'
if (-not (Test-Path $stageDir)) { New-Item -ItemType Directory -Path $stageDir | Out-Null }
$stageWriter = Join-Path $stageDir 'claude_status_writer.py'
Copy-Item -LiteralPath $srcWriter -Destination $stageWriter -Force
Write-Host "Staged writer at: $stageWriter"

# Use python.exe (not pythonw.exe) so stdin/stdout are reliably connected when
# Claude Code spawns the statusLine command with redirected pipes.
$py = (Get-Command python -ErrorAction Stop).Source
# Neither path has spaces or non-ASCII chars, so we use bare paths. Adding any
# quote characters here re-introduces the cmd /c double-quote stripping bug.
$command = $py + ' ' + $stageWriter

# Backup
$backup = $settings + '.bak-' + (Get-Date -Format 'yyyyMMdd-HHmmss')
Copy-Item -LiteralPath $settings -Destination $backup
Write-Host "Backed up settings.json -> $backup"

# Read + patch + write
$raw = Get-Content -LiteralPath $settings -Raw -Encoding UTF8
$json = $raw | ConvertFrom-Json

$desired = [pscustomobject]@{ type = 'command'; command = $command }
if ($json.PSObject.Properties.Name -contains 'statusLine') {
    $cur = $json.statusLine
    if ($cur.command -eq $command -and $cur.type -eq 'command') {
        Write-Host "statusLine is already wired to this writer. Nothing to do."
        exit 0
    }
    Write-Host "Replacing existing statusLine."
    $json.statusLine = $desired
} else {
    $json | Add-Member -NotePropertyName statusLine -NotePropertyValue $desired
}

# Write back as UTF-8 with BOM via .NET (Set-Content -Encoding UTF8 differs
# between PS 5.1 and PS 7 — this avoids the surprise).
$out = $json | ConvertTo-Json -Depth 32
[System.IO.File]::WriteAllText($settings, $out, (New-Object System.Text.UTF8Encoding $true))

Write-Host ""
Write-Host "Added statusLine to settings.json. New value:"
Write-Host "  command: $command"
Write-Host ""
Write-Host "Next: open or refresh a Claude Code session once so the writer fires."
Write-Host "After that, ~/.claude/cache/tray-usage-snapshot.json appears and the tray"
Write-Host "widget will show the real 5h / 7d numbers."
