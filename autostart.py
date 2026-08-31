"""Register / unregister the app in the Windows Startup folder.

Uses a shortcut (.lnk) created via PowerShell's WScript.Shell COM object so we
don't need pywin32 as a dependency.
"""
from __future__ import annotations
import os
import subprocess
import sys
from pathlib import Path

import paths

SHORTCUT_NAME = "Claude-Codex Usage Widget.lnk"
_CREATE_NO_WINDOW = 0x08000000


def startup_dir() -> Path:
    return (Path(os.environ.get("APPDATA", Path.home()))
            / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup")


def shortcut_path() -> Path:
    return startup_dir() / SHORTCUT_NAME


def is_enabled() -> bool:
    return shortcut_path().exists()


def _launch_target() -> tuple[str, str]:
    """Return (target, arguments) for the shortcut."""
    if paths.is_frozen():
        return str(Path(sys.executable).resolve()), ""
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    exe = str(pythonw if pythonw.exists() else sys.executable)
    return exe, f'"{Path(__file__).resolve().parent / "tray.py"}"'


def _run_ps(script: str) -> bool:
    try:
        r = subprocess.run(
            ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
            capture_output=True, timeout=30, creationflags=_CREATE_NO_WINDOW,
        )
        return r.returncode == 0
    except Exception as e:
        print(f"[autostart] powershell failed: {e}")
        return False


def enable() -> bool:
    target, args = _launch_target()
    link = shortcut_path()
    workdir = str(Path(target).parent)
    icon = paths.resource_path("app-icon.ico")
    icon_line = f"$sc.IconLocation = '{icon}';" if icon.exists() else ""
    script = (
        f"$ErrorActionPreference='Stop';"
        f"$s = New-Object -ComObject WScript.Shell;"
        f"$sc = $s.CreateShortcut('{link}');"
        f"$sc.TargetPath = '{target}';"
        f"$sc.Arguments = '{args}';"
        f"$sc.WorkingDirectory = '{workdir}';"
        f"$sc.WindowStyle = 7;"
        f"{icon_line}"
        f"$sc.Description = 'Claude / Codex usage widget';"
        f"$sc.Save()"
    )
    startup_dir().mkdir(parents=True, exist_ok=True)
    return _run_ps(script) and link.exists()


def disable() -> bool:
    link = shortcut_path()
    try:
        if link.exists():
            link.unlink()
        return not link.exists()
    except Exception as e:
        print(f"[autostart] remove failed: {e}")
        return False


def toggle() -> bool:
    return disable() if is_enabled() else enable()
