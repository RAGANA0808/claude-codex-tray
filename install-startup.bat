@echo off
REM Register the widget as a Windows Startup app (one-time setup).
REM Drops a shortcut into the user's Startup folder that targets pythonw.exe
REM directly, so there is no cmd window flicker at sign-in.
REM
REM Re-run safely; it just overwrites the existing shortcut.

setlocal
set "REPO=%~dp0"
set "STARTUP=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup"
set "LINK=%STARTUP%\Claude-Codex Usage Widget.lnk"

REM Find pythonw.exe
for /f "delims=" %%i in ('where pythonw 2^>nul') do (
  set "PYTHONW=%%i"
  goto :found
)
echo Python (pythonw.exe) が見つかりません。Python 3.10+ をインストールしてください。
exit /b 1

:found
echo Using pythonw: %PYTHONW%

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$s = New-Object -ComObject WScript.Shell;" ^
  "$sc = $s.CreateShortcut('%LINK%');" ^
  "$sc.TargetPath = '%PYTHONW%';" ^
  "$sc.Arguments = '\"%REPO%tray.py\"';" ^
  "$sc.WorkingDirectory = '%REPO%';" ^
  "$sc.WindowStyle = 7;" ^
  "$sc.Description = 'Claude / Codex usage widget';" ^
  "$sc.Save()"

if errorlevel 1 (
  echo Failed to create shortcut.
  exit /b 1
)

echo.
echo  作成: %LINK%
echo  → 次回サインインからウィジェットが自動起動します。
echo  → 無効化したい時は『設定 → アプリ → スタートアップ』から OFF。
endlocal
