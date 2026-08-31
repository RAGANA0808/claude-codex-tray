"""Configuration loader. Reads config.json next to this file; falls back to defaults."""
from __future__ import annotations
import json
from pathlib import Path

import paths

HERE = paths.user_data_dir()
CONFIG_PATH = paths.config_path()

DEFAULTS = {
    "poll_seconds": 30,
    # Resolved at runtime from the current user's home so a config.json copied
    # between machines never carries another user's absolute paths.
    "claude_dir": None,
    "codex_dir": None,

    # Claude Code plan limits. Pick one of: pro / max5 / max20 / custom
    "claude_plan": "max20",

    # USD-based budgets per plan (rolling 5h / rolling 7d).
    # Values are best-effort estimates; tune in config.json.
    # NOTE: Anthropic does not publish exact USD ceilings; these are working estimates.
    # Tune by editing config.json once you see how the bar moves day-to-day.
    "plan_limits_usd": {
        "pro":   {"window_5h": 7.0,   "window_7d": 200.0},
        "max5":  {"window_5h": 35.0,  "window_7d": 1000.0},
        "max20": {"window_5h": 250.0, "window_7d": 5000.0},
        "custom": {"window_5h": 50.0, "window_7d": 1400.0},
    },

    # Per-Mtok pricing (USD). Keys are matched as substrings against the model id.
    "pricing": {
        "opus":   {"input": 15.0, "output": 75.0, "cache_creation": 18.75, "cache_read": 1.50},
        "sonnet": {"input": 3.0,  "output": 15.0, "cache_creation": 3.75,  "cache_read": 0.30},
        "haiku":  {"input": 1.0,  "output": 5.0,  "cache_creation": 1.25,  "cache_read": 0.10},
        "fable":  {"input": 15.0, "output": 75.0, "cache_creation": 18.75, "cache_read": 1.50},
        "default": {"input": 3.0, "output": 15.0, "cache_creation": 3.75,  "cache_read": 0.30},
    },

    # Theme thresholds (%)
    "thresholds": {"warn": 50, "danger": 80},

    # "taskbar" = reparent the bar into the Windows taskbar strip.
    # "float"   = old behaviour, a topmost window drawn over the taskbar.
    # Falls back to "float" automatically if the taskbar cannot be found.
    "widget_mode": "taskbar",

    # Colour of the Windows taskbar strip. Used to cancel the additive blend
    # applied to an embedded widget. Set to "#000000" to disable that
    # correction (e.g. if transparency effects make the strip translucent).
    "taskbar_bg": "#111111",
}


def _deep_merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load() -> dict:
    cfg = dict(DEFAULTS)
    if CONFIG_PATH.exists():
        try:
            user = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            cfg = _deep_merge(cfg, user)
        except Exception as e:
            print(f"[config] failed to load {CONFIG_PATH}: {e}")
    if not cfg.get("claude_dir"):
        cfg["claude_dir"] = str(Path.home() / ".claude" / "projects")
    if not cfg.get("codex_dir"):
        cfg["codex_dir"] = str(Path.home() / ".codex" / "sessions")
    return cfg


def write_default_if_missing() -> None:
    """Write only the knobs a user would reasonably tune. Machine-specific
    paths stay out of the file so it stays portable between PCs."""
    if CONFIG_PATH.exists():
        return
    seed = {
        "poll_seconds": DEFAULTS["poll_seconds"],
        "claude_plan": DEFAULTS["claude_plan"],
        "plan_limits_usd": DEFAULTS["plan_limits_usd"],
        "thresholds": DEFAULTS["thresholds"],
    }
    CONFIG_PATH.write_text(
        json.dumps(seed, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def claude_plan_limit_usd(cfg: dict) -> dict:
    plan = cfg.get("claude_plan", "max20")
    limits = cfg["plan_limits_usd"].get(plan) or cfg["plan_limits_usd"]["max20"]
    return limits


def model_pricing(cfg: dict, model: str) -> dict:
    pricing = cfg["pricing"]
    model_lower = (model or "").lower()
    for key, prices in pricing.items():
        if key == "default":
            continue
        if key in model_lower:
            return prices
    return pricing["default"]
