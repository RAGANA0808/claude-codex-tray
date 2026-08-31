"""Path resolution that works both as a plain script and as a PyInstaller exe.

Two distinct kinds of path:
  - bundled read-only assets (icons)  -> resource_path()
  - writable user state (config.json) -> user_data_dir()

Under PyInstaller onefile, __file__ points inside a temp extraction dir that is
deleted on exit, so writing there silently loses data.
"""
from __future__ import annotations
import os
import sys
from pathlib import Path

APP_DIR_NAME = "claude-codex-tray"


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def _bundle_dir() -> Path:
    if is_frozen():
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parent


def exe_dir() -> Path:
    """Directory the exe (or script) actually lives in."""
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def resource_path(name: str) -> Path:
    """Read-only asset shipped with the app."""
    return _bundle_dir() / name


def _is_writable(d: Path) -> bool:
    try:
        d.mkdir(parents=True, exist_ok=True)
        probe = d / ".write-probe"
        probe.write_text("", encoding="utf-8")
        probe.unlink()
        return True
    except Exception:
        return False


_cached_data_dir: Path | None = None


def user_data_dir() -> Path:
    """Writable directory for config.json and caches.

    Prefers sitting next to the exe (portable, easy to find), falling back to
    %LOCALAPPDATA% when the install location is read-only (Program Files).
    """
    global _cached_data_dir
    if _cached_data_dir is not None:
        return _cached_data_dir

    if not is_frozen():
        _cached_data_dir = Path(__file__).resolve().parent
        return _cached_data_dir

    beside_exe = exe_dir()
    if _is_writable(beside_exe):
        _cached_data_dir = beside_exe
        return _cached_data_dir

    fallback = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / APP_DIR_NAME
    fallback.mkdir(parents=True, exist_ok=True)
    _cached_data_dir = fallback
    return _cached_data_dir


def config_path() -> Path:
    return user_data_dir() / "config.json"
