#requires -Version 5.1
<#
.SYNOPSIS
  Build the distributable single-file exe.

.DESCRIPTION
  Produces dist\ClaudeCodexUsage.exe — a self-contained, no-console tray widget
  that needs no Python on the target machine.

.NOTES
  cd <クローンしたフォルダ>
  .\build.ps1
#>

$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot

Write-Host "==== 依存関係の確認 ====" -ForegroundColor Cyan
$py = (Get-Command python -ErrorAction Stop).Source
& $py -c "import PyInstaller, pystray, PIL" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "  不足パッケージをインストールします..."
    & $py -m pip install --user --quiet pyinstaller pystray Pillow
}
Write-Host "  OK"

Write-Host "`n==== 実行中のプロセスを停止 ====" -ForegroundColor Cyan
Get-Process -Name ClaudeCodexUsage -ErrorAction SilentlyContinue | Stop-Process -Force
Write-Host "  OK"

Write-Host "`n==== アイコン生成 ====" -ForegroundColor Cyan
& $py make_icon.py
Write-Host "  OK"

Write-Host "`n==== ビルド ====" -ForegroundColor Cyan
& $py -m PyInstaller ClaudeCodexUsage.spec --noconfirm --clean
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }

$exe = Join-Path $PSScriptRoot 'dist\ClaudeCodexUsage.exe'
$size = [math]::Round((Get-Item $exe).Length / 1MB, 1)

Write-Host "`n==== 完了 ====" -ForegroundColor Green
Write-Host "  $exe  ($size MB)"
Write-Host ""
Write-Host "  配布するときは dist\ClaudeCodexUsage.exe を 1 ファイル渡すだけで OK。"
Write-Host "  設定は exe と同じフォルダに config.json が自動生成されます。"
