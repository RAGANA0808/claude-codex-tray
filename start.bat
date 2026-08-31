@echo off
REM Launch the tray app with pythonw so no console window appears.
REM Double-click this file to start; close from the tray icon's menu.
cd /d "%~dp0"
start "" pythonw "%~dp0tray.py"
