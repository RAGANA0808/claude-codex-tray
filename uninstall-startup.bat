@echo off
set "LINK=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\Claude-Codex Usage Tray.lnk"
if exist "%LINK%" (
  del "%LINK%"
  echo Removed auto-start shortcut.
) else (
  echo No shortcut found at %LINK%
)
