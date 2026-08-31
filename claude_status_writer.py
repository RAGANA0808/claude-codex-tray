"""Claude Code statusLine command. Receives the live JSON Claude Code pipes in on
stdin (which carries the authoritative `rate_limits.five_hour.used_percentage` /
`seven_day.used_percentage`), persists a snapshot the tray reads, and prints a
short status line back so Claude's UI still shows something useful.

Wire-up: add to ~/.claude/settings.json:
    "statusLine": { "type": "command",
                    "command": "pythonw \\"<this script>\\"" }
"""
from __future__ import annotations
import json
import os
import sys
import time
from pathlib import Path

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

SNAPSHOT_PATH = Path.home() / ".claude" / "cache" / "tray-usage-snapshot.json"


def _atomic_write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def main() -> int:
    raw = sys.stdin.read()
    try:
        data = json.loads(raw) if raw else {}
    except Exception:
        data = {}

    rl = data.get("rate_limits") or {}
    five = rl.get("five_hour") or {}
    seven = rl.get("seven_day") or {}
    model = (data.get("model") or {}).get("display_name") or ""
    ctx = (data.get("context_window") or {}).get("used_percentage")

    snap = {
        "updated_at": time.time(),
        "model": model,
        "context_used_pct": ctx,
        "five_hour": {
            "used_pct": five.get("used_percentage"),
            "resets_at": five.get("resets_at"),
        },
        "seven_day": {
            "used_pct": seven.get("used_percentage"),
            "resets_at": seven.get("resets_at"),
        },
    }
    try:
        _atomic_write(SNAPSHOT_PATH, snap)
    except Exception:
        pass

    # Print a compact statusline so the Claude Code UI shows useful info.
    parts = []
    if model:
        parts.append(model)
    if ctx is not None:
        parts.append(f"ctx {ctx:.0f}%")
    f = snap["five_hour"]["used_pct"]
    if f is not None:
        parts.append(f"5h {f:.0f}%")
    s = snap["seven_day"]["used_pct"]
    if s is not None:
        parts.append(f"7d {s:.0f}%")
    sys.stdout.write(" │ ".join(parts))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
