#requires -Version 5.1
<#
.SYNOPSIS
  One-shot setup: install pystray, register Windows startup, and launch the widget.

.DESCRIPTION
  - Locates pythonw.exe (errors out if Python isn't installed).
  - Installs the pystray + Pillow dependencies if missing.
  - Creates a shortcut in the Windows Startup folder so the widget auto-launches at login.
    The shortcut targets pythonw.exe directly (no cmd window flicker).
  - Optionally adds the statusLine hook to ~/.claude/settings.json for live Claude rate_limits
    (skipped if you decline at the prompt — you can run install-statusline.ps1 later).
  - Starts the widget right now so you can confirm it works.

  Idempotent — safe to re-run.

.NOTES
  Right-click setup.ps1 → "Run with PowerShell".
  Or from a PowerShell window:
      cd <クローンしたフォルダ>
      .\setup.ps1
#>

$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $MyInvocation.MyCommand.Path

function Section($title) {
    Write-Host ""
    Write-Host "==== $title ====" -ForegroundColor Cyan
}

# --- 1. Locate Python -----------------------------------------------------------------
Section "Python の確認"
$pyCmd  = Get-Command python  -ErrorAction SilentlyContinue
$pywCmd = Get-Command pythonw -ErrorAction SilentlyContinue
$python  = if ($pyCmd)  { $pyCmd.Source }  else { $null }
$pythonw = if ($pywCmd) { $pywCmd.Source } else { $null }
if (-not $pythonw -or -not $python) {
    Write-Error "Python が見つかりません。先に Python 3.10 以上をインストールしてください。"
    exit 1
}
Write-Host "  python  = $python"
Write-Host "  pythonw = $pythonw"

# --- 2. Install dependencies ----------------------------------------------------------
Section "依存ライブラリのインストール"
$needPystray = $true
try {
    & $python -c "import pystray, PIL" 2>$null
    if ($LASTEXITCODE -eq 0) { $needPystray = $false }
} catch {}

if ($needPystray) {
    Write-Host "  pip install --user pystray Pillow ..."
    & $python -m pip install --user --quiet pystray Pillow
    if ($LASTEXITCODE -ne 0) {
        Write-Error "pip install に失敗しました。"
        exit 1
    }
    Write-Host "  OK"
} else {
    Write-Host "  pystray と Pillow は既に入っています。"
}

# --- 3. Register Startup shortcut -----------------------------------------------------
Section "Windows スタートアップへの登録"
$startup = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\Startup"
$linkPath = Join-Path $startup "Claude-Codex Usage Widget.lnk"
$trayPy = Join-Path $repo "tray.py"

if (-not (Test-Path $trayPy)) {
    Write-Error "tray.py が見つかりません: $trayPy"
    exit 1
}

$wsh = New-Object -ComObject WScript.Shell
$sc = $wsh.CreateShortcut($linkPath)
$sc.TargetPath = $pythonw
$sc.Arguments = '"' + $trayPy + '"'
$sc.WorkingDirectory = $repo
$sc.WindowStyle = 7   # Minimized (pythonw has no window anyway, just being explicit)
$sc.IconLocation = (Join-Path $repo "app-claude.png") + ",0"  # uses default if PNG can't be used
$sc.Description = "Claude / Codex usage widget"
$sc.Save()

Write-Host "  ショートカット作成: $linkPath"
Write-Host "  → 次回 Windows サインイン時から自動起動します。"
Write-Host "  → スタートアップ無効化したい時は『設定 → アプリ → スタートアップ』で OFF。"

# --- 4. statusLine wiring (optional) --------------------------------------------------
Section "Claude Code statusLine 連携 (推奨)"
$settings = Join-Path $env:USERPROFILE ".claude\settings.json"
$writer = Join-Path $repo "claude_status_writer.py"
# Use python.exe so stdin/stdout pipes attach reliably (pythonw breaks stdin).
$command = '"' + $python + '" "' + $writer + '"'

$wired = $false
if (Test-Path $settings) {
    try {
        $j = Get-Content -LiteralPath $settings -Raw -Encoding UTF8 | ConvertFrom-Json
        if ($j.PSObject.Properties.Name -contains 'statusLine' -and $j.statusLine.command -eq $command) {
            $wired = $true
        }
    } catch {}
}

if ($wired) {
    Write-Host "  statusLine は既に正しく設定されています。"
} else {
    Write-Host "  Claude Code の rate_limits を実値で取得するには ~/.claude/settings.json に"
    Write-Host "  statusLine を追加します（バックアップを作成します）。"
    $ans = Read-Host "  追加していい？ [Y/n]"
    if ($ans -eq '' -or $ans -match '^[Yy]') {
        & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $repo "install-statusline.ps1")
    } else {
        Write-Host '  スキップしました。後から .\install-statusline.ps1 で追加できます。'
    }
}

# --- 5. Launch now --------------------------------------------------------------------
Section "ウィジェット起動"
# 既存プロセスを潰さず、新しく 1 つ起こす。
Start-Process -FilePath $pythonw -ArgumentList @('"' + $trayPy + '"') -WorkingDirectory $repo | Out-Null
Write-Host "  起動しました。タスクバー上部にウィジェットが出ているはずです。"
Write-Host "  右クリックで終了/設定、ダブルクリックで詳細ダッシュボード。"

Section "完了"
Write-Host "  これでセットアップは終わりです。次回ログイン時は自動で起動します。" -ForegroundColor Green
