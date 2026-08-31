"""Parsers for Claude Code transcripts and Codex CLI rollouts."""
from __future__ import annotations
import json
import os
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

import config as cfg_mod


# ---------- Shared snapshot ----------

@dataclass
class CodexSnapshot:
    available: bool = False
    # Semantic (window-classified) fields: primary_* is ALWAYS the 5h/session
    # window; secondary_* is ALWAYS the 7d/weekly window. Which JSON slot they
    # came from depends on Codex's current schema.
    primary_pct: float = 0.0
    secondary_pct: float = 0.0
    primary_resets_at: int = 0
    secondary_resets_at: int = 0
    primary_window_minutes: int = 300
    secondary_window_minutes: int = 10080
    has_primary: bool = False       # False = no 5h window on this plan
    has_secondary: bool = False     # False = no 7d window
    plan_type: str = "?"
    last_event_at: str = ""
    total_tokens: int = 0
    note: str = ""


@dataclass
class ClaudeSnapshot:
    available: bool = False
    # Authoritative values from Claude Code's statusLine pipe
    pct_5h: float = 0.0
    pct_7d: float = 0.0
    block_resets_at: float = 0.0   # unix seconds, set by Claude Code
    week_resets_at: float = 0.0
    snapshot_age_seconds: float = 0.0  # how stale the snapshot is
    context_used_pct: float = 0.0
    model: str = ""
    # Fable-scoped weekly meter (only populated when /api/oauth/usage succeeds
    # and returns a Fable-scoped limit — requires user:profile OAuth scope).
    fable_available: bool = False
    fable_pct: float = 0.0
    fable_resets_at: float = 0.0
    # True when the OAuth refresh token died and only an interactive
    # `claude auth login` can restore live data (notably the Fable meter).
    needs_login: bool = False
    # Local-only fallback metrics (informational; not used for the bars)
    tokens_5h: int = 0
    tokens_7d: int = 0
    cost_5h_usd: float = 0.0
    cost_7d_usd: float = 0.0
    block_started_at: float = 0.0
    models_5h: dict = field(default_factory=dict)
    note: str = ""


# ---------- Codex parser ----------

def _iter_codex_files(root: Path) -> Iterable[Path]:
    if not root.exists():
        return
    # Files are organized as YYYY/MM/DD/rollout-*.jsonl. Walk and sort by mtime desc.
    for p in root.rglob("rollout-*.jsonl"):
        yield p


def _read_last_token_count(path: Path) -> dict | None:
    """Scan a rollout JSONL for the *last* token_count event. Returns parsed payload or None."""
    try:
        size = path.stat().st_size
        if size == 0:
            return None
        # Read from end in chunks until we find a token_count event.
        chunk = 64 * 1024
        last_found = None
        with path.open("rb") as f:
            # Cheap path: read whole file if small.
            if size <= chunk * 4:
                data = f.read().decode("utf-8", errors="ignore")
                for line in data.splitlines():
                    if '"token_count"' in line:
                        last_found = line
                return _parse_event_line(last_found) if last_found else None
            # Larger: tail seek.
            buf = b""
            pos = size
            while pos > 0 and last_found is None:
                step = min(chunk, pos)
                pos -= step
                f.seek(pos)
                buf = f.read(step) + buf
                lines = buf.split(b"\n")
                # Keep partial first line for next iter
                buf = lines[0]
                for line in reversed(lines[1:]):
                    if b'"token_count"' in line:
                        last_found = line.decode("utf-8", errors="ignore")
                        break
            if last_found:
                return _parse_event_line(last_found)
    except Exception as e:
        print(f"[codex] read fail {path.name}: {e}")
    return None


def _parse_event_line(line: str) -> dict | None:
    try:
        obj = json.loads(line)
        if obj.get("type") == "event_msg" and obj.get("payload", {}).get("type") == "token_count":
            return obj
    except Exception:
        return None
    return None


def collect_codex(cfg: dict) -> CodexSnapshot:
    root = Path(cfg["codex_dir"])
    snap = CodexSnapshot()
    if not root.exists():
        snap.note = f"not found: {root}"
        return snap

    # Find the latest rollout by mtime — that's the most recently active session.
    files = sorted(_iter_codex_files(root), key=lambda p: p.stat().st_mtime, reverse=True)
    if not files:
        snap.note = "no rollout files"
        return snap

    event = None
    # Walk a few recent files until we find a token_count event with rate_limits.
    for p in files[:20]:
        ev = _read_last_token_count(p)
        if ev and ev.get("payload", {}).get("rate_limits"):
            event = ev
            break

    if not event:
        snap.note = "no token_count event with rate_limits"
        return snap

    payload = event["payload"]
    rl = payload.get("rate_limits", {}) or {}
    info = payload.get("info", {}) or {}
    total = (info.get("total_token_usage") or {}).get("total_tokens", 0)

    # Classify each present slot by its window_minutes rather than trusting its
    # position. Codex has changed which slot holds the 7d window before.
    session_slot = None   # <= 12h window
    weekly_slot = None    # > 12h window
    for slot_key in ("primary", "secondary"):
        slot = rl.get(slot_key) or {}
        if not slot:
            continue
        win_min = int(slot.get("window_minutes", 0) or 0)
        if win_min <= 12 * 60:
            session_slot = slot
        else:
            weekly_slot = slot

    def _extract(slot, default_win_min):
        pct = float(slot.get("used_percent", 0.0))
        reset = int(slot.get("resets_at", 0) or 0)
        win_min = int(slot.get("window_minutes", 0) or 0) or default_win_min
        # If reset is in the past, advance to the next window boundary.
        now_t = int(time.time())
        if reset and now_t > reset:
            pct = 0.0
            step = max(60, win_min) * 60
            while reset < now_t:
                reset += step
        return pct, reset, win_min

    if session_slot:
        p_pct, p_reset, p_win = _extract(session_slot, 300)
        snap.primary_pct = p_pct
        snap.primary_resets_at = p_reset
        snap.primary_window_minutes = p_win
        snap.has_primary = True
    if weekly_slot:
        s_pct, s_reset, s_win = _extract(weekly_slot, 10080)
        snap.secondary_pct = s_pct
        snap.secondary_resets_at = s_reset
        snap.secondary_window_minutes = s_win
        snap.has_secondary = True

    snap.available = True
    snap.plan_type = str(rl.get("plan_type", "?"))
    snap.last_event_at = event.get("timestamp", "")
    snap.total_tokens = int(total)
    return snap


# ---------- Claude Code parser ----------

def _iter_claude_files(root: Path) -> Iterable[Path]:
    if not root.exists():
        return
    for p in root.rglob("*.jsonl"):
        yield p


def _parse_ts(ts: str) -> float:
    try:
        if ts.endswith("Z"):
            ts = ts[:-1] + "+00:00"
        return datetime.fromisoformat(ts).timestamp()
    except Exception:
        return 0.0


def _cost_for_usage(model: str, usage: dict, cfg: dict) -> float:
    """Real API USD cost — for informational display."""
    p = cfg_mod.model_pricing(cfg, model)
    in_tok = usage.get("input_tokens", 0) or 0
    out_tok = usage.get("output_tokens", 0) or 0
    cc = usage.get("cache_creation_input_tokens", 0) or 0
    cr = usage.get("cache_read_input_tokens", 0) or 0
    return (in_tok * p["input"] + out_tok * p["output"]
            + cc * p["cache_creation"] + cr * p["cache_read"]) / 1_000_000.0


def _ratelimit_cost(model: str, usage: dict, cfg: dict) -> float:
    """Rate-limit-weighted spend proxy. Anthropic's published 5h/7d ceilings do
    NOT seem to count cache_read tokens (they're served from cache and don't
    re-bill compute), so we exclude them. This matches Anthropic's own usage
    panel within ~1-2 percentage points when calibrated."""
    p = cfg_mod.model_pricing(cfg, model)
    in_tok = usage.get("input_tokens", 0) or 0
    out_tok = usage.get("output_tokens", 0) or 0
    cc = usage.get("cache_creation_input_tokens", 0) or 0
    return (in_tok * p["input"] + out_tok * p["output"]
            + cc * p["cache_creation"]) / 1_000_000.0


def _total_tokens(usage: dict) -> int:
    return ((usage.get("input_tokens", 0) or 0)
            + (usage.get("output_tokens", 0) or 0)
            + (usage.get("cache_creation_input_tokens", 0) or 0)
            + (usage.get("cache_read_input_tokens", 0) or 0))


SNAPSHOT_FILE = Path.home() / ".claude" / "cache" / "tray-usage-snapshot.json"
LIVE_CACHE_FILE = Path.home() / ".claude" / "cache" / "tray-live-cache.json"

# --- CodeZeno-style live fetch from Anthropic API ---------------------
# Uses a long-lived OAuth token (from `claude setup-token`) discovered in
# ~/.claude/projects/**/memory/.env.claude_oauth, or the short-lived access
# token in ~/.claude/.credentials.json as a fallback. The primary endpoint
# GET /api/oauth/usage requires the user:profile scope which long-lived tokens
# lack, so we always use POST /v1/messages and read the anthropic-ratelimit-
# unified-* response headers. This is the same pattern CodeZeno uses; 429 is
# accepted as success (the headers are still present on 429).

_LIVE_TTL_SEC = 300           # don't hit the API more often than this (5 min)
_LIVE_TOKEN_HEAD_RE = re.compile(r"CLAUDE_CODE_OAUTH_TOKEN=([^\s#]+)")
# Persisted state so we don't re-hit /api/oauth/usage every poll when it 403s.
_OAUTH_USAGE_STATE = Path.home() / ".claude" / "cache" / "tray-oauth-usage-state.json"
_OAUTH_USAGE_FORBIDDEN_TTL = 24 * 3600
_OAUTH_USAGE_LAST_GOOD = Path.home() / ".claude" / "cache" / "tray-oauth-usage-lastgood.json"
_OAUTH_USAGE_REFRESH_TTL = 5 * 60             # want fresh data after this age
# Fable is a weekly counter — showing a stale value beats showing nothing.
# Tolerate last-good for a full day; refresh happens every ~5min when possible.
_OAUTH_USAGE_STALE_TTL = 24 * 60 * 60
_OAUTH_USAGE_MIN_INTERVAL = 5 * 60            # don't hit endpoint more often than this
_OAUTH_USAGE_LAST_ATTEMPT = Path.home() / ".claude" / "cache" / "tray-oauth-usage-lastattempt.json"
# Treat a token as expired this long before its real expiry. A request fired
# in the final seconds of a token's life comes back 401, which used to be read
# as "this account has no Fable scope" and blanked the meter for a full day.
_TOKEN_EXPIRY_MARGIN_SEC = 300
# Sentinel: the endpoint rejected the token itself (401). Distinct from a
# scope refusal (403) because a 401 is fixed by refreshing, not by giving up.
_OAUTH_UNAUTHORIZED = object()


def _find_long_lived_token() -> str | None:
    root = Path.home() / ".claude" / "projects"
    if not root.exists():
        return None
    # Only shallow-scan a few likely locations. The user's setup-token file lives
    # under some project's memory/ dir.
    for env_file in root.rglob(".env.claude_oauth"):
        try:
            m = _LIVE_TOKEN_HEAD_RE.search(env_file.read_text(encoding="utf-8", errors="ignore"))
            if m:
                return m.group(1).strip()
        except Exception:
            continue
    return None


_CLI_REFRESH_LAST_ATTEMPT = Path.home() / ".claude" / "cache" / "tray-cli-refresh-lastattempt.json"
_CLI_REFRESH_LOG = Path.home() / ".claude" / "cache" / "tray-cli-refresh.log"
# `claude -p` only rotates the OAuth token when it is ALREADY expired — calling
# it on a still-valid token is a no-op. So there is no proactive refresh: we
# wait for expiry and then refresh immediately. The throttle only exists to
# stop a spin loop if refresh keeps failing.
_CLI_REFRESH_MIN_INTERVAL = 60
# When the refresh token itself dies, `claude -p` returns 401 telling the user
# to re-authenticate. No amount of retrying fixes that, so we latch the state,
# stop hammering the CLI, and surface it in the UI.
_NEEDS_LOGIN_FLAG = Path.home() / ".claude" / "cache" / "tray-needs-login.json"
_NEEDS_LOGIN_RETRY_INTERVAL = 60 * 60
_REAUTH_MARKERS = (
    "re-authenticate",
    "oauth access token has expired",
    "please run /login",
    "invalid authentication credentials",
)


def _get_desktop_access_token(
        margin_sec: float = _TOKEN_EXPIRY_MARGIN_SEC) -> tuple[str | None, bool]:
    p = Path.home() / ".claude" / ".credentials.json"
    if not p.exists():
        return None, True
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        o = d.get("claudeAiOauth") or {}
        tok = o.get("accessToken")
        exp = int(o.get("expiresAt", 0))
        now_ms = int(time.time() * 1000)
        return tok, now_ms >= exp - int(margin_sec * 1000)
    except Exception:
        return None, True


def _get_fresh_desktop_token() -> str | None:
    """Return a non-expired Desktop OAuth token, refreshing via the CLI if the
    stored one has expired. This token carries the user:profile scope that
    /api/oauth/usage (and therefore the Fable meter) requires."""
    tok, expired = _get_desktop_access_token()
    if tok and not expired:
        return tok
    return _force_desktop_token_refresh()


def _force_desktop_token_refresh() -> str | None:
    """Rotate the Desktop token via the CLI even when the stored one still
    looks valid — used after a 401, where the stored expiry evidently lied."""
    if _cli_refresh_last_attempt_ago() < _CLI_REFRESH_MIN_INTERVAL:
        return None
    if needs_login() and _needs_login_age() < _NEEDS_LOGIN_RETRY_INTERVAL:
        return None
    _mark_cli_refresh_attempt()
    if not _try_cli_refresh():
        return None
    tok2, expired2 = _get_desktop_access_token()
    return tok2 if (tok2 and not expired2) else None


def _cli_refresh_last_attempt_ago() -> float:
    try:
        d = json.loads(_CLI_REFRESH_LAST_ATTEMPT.read_text(encoding="utf-8"))
        return time.time() - float(d.get("at", 0))
    except Exception:
        return 1e12


def _mark_cli_refresh_attempt() -> None:
    try:
        _CLI_REFRESH_LAST_ATTEMPT.parent.mkdir(parents=True, exist_ok=True)
        _CLI_REFRESH_LAST_ATTEMPT.write_text(json.dumps({"at": time.time()}), encoding="utf-8")
    except Exception:
        pass


def _log_cli_refresh(msg: str) -> None:
    try:
        _CLI_REFRESH_LOG.parent.mkdir(parents=True, exist_ok=True)
        with _CLI_REFRESH_LOG.open("a", encoding="utf-8") as f:
            f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n")
    except Exception:
        pass


def _token_ttl_minutes() -> int:
    try:
        d = json.loads((Path.home() / ".claude" / ".credentials.json").read_text(encoding="utf-8"))
        exp = int((d.get("claudeAiOauth") or {}).get("expiresAt", 0))
        return (exp - int(time.time() * 1000)) // 60000
    except Exception:
        return -999999


def _find_claude_cli() -> Path | None:
    """Locate the Claude Code CLI. Installs vary by machine (native installer,
    npm global, winget), so search PATH first and then the usual locations."""
    import shutil
    found = shutil.which("claude")
    if found:
        return Path(found)
    appdata = os.environ.get("APPDATA", "")
    localapp = os.environ.get("LOCALAPPDATA", "")
    candidates = [
        Path.home() / ".local" / "bin" / "claude.exe",
        Path(appdata) / "npm" / "claude.cmd" if appdata else None,
        Path(appdata) / "npm" / "claude.ps1" if appdata else None,
        Path(localapp) / "Programs" / "claude" / "claude.exe" if localapp else None,
    ]
    for c in candidates:
        if c and c.exists():
            return c
    return None


def needs_login() -> bool:
    """True when the CLI reported that interactive re-login is required.

    Self-clears as soon as a valid token is observed, so the warning disappears
    on its own right after the user runs `claude auth login`.
    """
    try:
        d = json.loads(_NEEDS_LOGIN_FLAG.read_text(encoding="utf-8"))
    except Exception:
        return False
    if not d.get("at"):
        return False
    tok, expired = _get_desktop_access_token(margin_sec=0)
    if tok and not expired:
        _clear_needs_login()
        return False
    return True


def _needs_login_age() -> float:
    try:
        d = json.loads(_NEEDS_LOGIN_FLAG.read_text(encoding="utf-8"))
        return time.time() - float(d.get("at", 0))
    except Exception:
        return 1e12


def _set_needs_login(detail: str) -> None:
    try:
        _NEEDS_LOGIN_FLAG.parent.mkdir(parents=True, exist_ok=True)
        _NEEDS_LOGIN_FLAG.write_text(
            json.dumps({"at": time.time(), "detail": detail[:300]}), encoding="utf-8")
    except Exception:
        pass


def _clear_needs_login() -> None:
    try:
        _NEEDS_LOGIN_FLAG.unlink(missing_ok=True)
    except Exception:
        pass


def _try_cli_refresh() -> bool:
    import subprocess
    claude_exe = _find_claude_cli()
    if claude_exe is None:
        _log_cli_refresh("claude CLI not found on PATH or in known locations")
        return False

    # Build a minimal, deterministic environment (verified to trigger a
    # file-persisted token refresh). Inheriting the parent env has produced
    # runs where the CLI responded but never wrote .credentials.json.
    keep = ["SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "COMSPEC", "PATHEXT",
            "USERPROFILE", "USERNAME", "APPDATA", "LOCALAPPDATA", "TEMP",
            "TMP", "PROGRAMFILES", "PROGRAMDATA", "PUBLIC", "COMPUTERNAME",
            "OS", "NUMBER_OF_PROCESSORS"]
    env = {k: os.environ[k] for k in keep if k in os.environ}
    env["PATH"] = ";".join([
        r"C:\Windows\system32",
        r"C:\Windows",
        str(claude_exe.parent),
    ])

    # .cmd / .ps1 shims can't be exec'd directly — route them through cmd.exe.
    if claude_exe.suffix.lower() in (".cmd", ".bat"):
        argv = ["cmd.exe", "/c", str(claude_exe), "-p", "hi"]
    elif claude_exe.suffix.lower() == ".ps1":
        argv = ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
                "-File", str(claude_exe), "-p", "hi"]
    else:
        argv = [str(claude_exe), "-p", "hi"]

    t0 = time.time()
    try:
        r = subprocess.run(
            argv,
            env=env,
            cwd=str(Path.home()),
            capture_output=True,
            timeout=90,
            creationflags=0x08000000,
        )
        elapsed = time.time() - t0
        _log_cli_refresh(
            f"exit={r.returncode} elapsed={elapsed:.1f}s "
            f"stdout={r.stdout[:120]!r} stderr={r.stderr[:200]!r}"
        )
    except subprocess.TimeoutExpired:
        _log_cli_refresh(f"timeout after {time.time()-t0:.1f}s")
        return False
    except Exception as e:
        _log_cli_refresh(f"exception: {e}")
        return False
    ttl_min = _token_ttl_minutes()
    ok = ttl_min > 0
    _log_cli_refresh(f"post-refresh: token_ttl={ttl_min}min ok={ok}")
    if ok:
        _clear_needs_login()
    else:
        blob = (r.stdout[:400] + b" " + r.stderr[:400]).decode("utf-8", "ignore").lower()
        if any(m in blob for m in _REAUTH_MARKERS):
            _set_needs_login(blob.strip())
            _log_cli_refresh("=> refresh token dead; interactive `claude auth login` required")
    return ok


def _resolve_live_token() -> tuple[str | None, str]:
    """Token used for POST /v1/messages (the 5h / 7d numbers).

    Prefer the long-lived setup-token when one exists (it never expires), but
    most machines won't have one — so fall through to the Desktop token and
    refresh it via the CLI when it has expired. Without that refresh a machine
    would simply lose all live data every 8 hours.
    """
    ll = _find_long_lived_token()
    if ll:
        return ll, "setup-token"
    tok, expired = _get_desktop_access_token()
    if tok and not expired:
        return tok, "desktop-oauth"
    refreshed = _get_fresh_desktop_token()
    if refreshed:
        return refreshed, "desktop-oauth-refreshed"
    return None, "none"


def _read_live_cache() -> dict | None:
    try:
        if not LIVE_CACHE_FILE.exists():
            return None
        d = json.loads(LIVE_CACHE_FILE.read_text(encoding="utf-8"))
        if time.time() - float(d.get("fetched_at", 0)) > _LIVE_TTL_SEC:
            return None
        return d
    except Exception:
        return None


def _write_live_cache(payload: dict) -> None:
    try:
        LIVE_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = LIVE_CACHE_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, LIVE_CACHE_FILE)
    except Exception:
        pass


def _token_fingerprint(token: str | None) -> str:
    if not token:
        return ""
    import hashlib
    return hashlib.sha256(token.encode("utf-8")).hexdigest()[:16]


def _oauth_usage_forbidden_recently() -> bool:
    try:
        d = json.loads(_OAUTH_USAGE_STATE.read_text(encoding="utf-8"))
    except Exception:
        return False
    if time.time() - float(d.get("forbidden_at", 0)) >= _OAUTH_USAGE_FORBIDDEN_TTL:
        return False
    # The latch belongs to the token that was refused, not to the machine, so
    # a re-login or a token rotation gets a fresh chance immediately instead
    # of waiting out the whole day.
    fp = d.get("token")
    if fp:
        tok, _ = _get_desktop_access_token(margin_sec=0)
        if tok and _token_fingerprint(tok) != fp:
            return False
    return True


def _mark_oauth_usage_forbidden(token: str | None = None) -> None:
    try:
        _OAUTH_USAGE_STATE.parent.mkdir(parents=True, exist_ok=True)
        _OAUTH_USAGE_STATE.write_text(
            json.dumps({"forbidden_at": time.time(),
                        "token": _token_fingerprint(token)}), encoding="utf-8"
        )
    except Exception:
        pass


def _parse_iso8601(s) -> float:
    if not s:
        return 0.0
    try:
        s = str(s)
        # Handle trailing Z or timezone offsets
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        return datetime.fromisoformat(s).timestamp()
    except Exception:
        return 0.0


def _read_last_good_oauth_usage(max_age: float = _OAUTH_USAGE_STALE_TTL) -> dict | None:
    try:
        d = json.loads(_OAUTH_USAGE_LAST_GOOD.read_text(encoding="utf-8"))
        if time.time() - float(d.get("fetched_at", 0)) > max_age:
            return None
        return d
    except Exception:
        return None


def _write_last_good_oauth_usage(payload: dict) -> None:
    try:
        _OAUTH_USAGE_LAST_GOOD.parent.mkdir(parents=True, exist_ok=True)
        tmp = _OAUTH_USAGE_LAST_GOOD.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, _OAUTH_USAGE_LAST_GOOD)
    except Exception:
        pass


def _oauth_usage_last_attempt_ago() -> float:
    try:
        d = json.loads(_OAUTH_USAGE_LAST_ATTEMPT.read_text(encoding="utf-8"))
        return time.time() - float(d.get("at", 0))
    except Exception:
        return 1e12  # never


def _mark_oauth_usage_attempt() -> None:
    try:
        _OAUTH_USAGE_LAST_ATTEMPT.parent.mkdir(parents=True, exist_ok=True)
        _OAUTH_USAGE_LAST_ATTEMPT.write_text(json.dumps({"at": time.time()}), encoding="utf-8")
    except Exception:
        pass


def _try_oauth_usage(token: str) -> dict | None:
    """Try /api/oauth/usage. On 429 (rate limit), return last-known-good cached
    response if we have one under 15min old. On 403 (scope refusal) mark
    forbidden and return None; on 401 return _OAUTH_UNAUTHORIZED so the caller
    can refresh the token and retry. On success, write to last-good cache."""
    req = urllib.request.Request(
        "https://api.anthropic.com/api/oauth/usage",
        headers={
            "Authorization": "Bearer " + token,
            "anthropic-beta": "oauth-2025-04-20",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            body = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 401:
            _log_cli_refresh("oauth usage 401 — token rejected, refreshing and retrying")
            return _OAUTH_UNAUTHORIZED
        if e.code == 403:
            _mark_oauth_usage_forbidden(token)
            return None
        if e.code == 429:
            return _read_last_good_oauth_usage()
        return None
    except Exception:
        return None

    def _sec(key: str) -> tuple[float, float]:
        # Usage-endpoint utilization is already 0-100
        sec = body.get(key) or {}
        pct = float(sec.get("utilization") or 0.0)
        return pct, _parse_iso8601(sec.get("resets_at"))

    p5, r5 = _sec("five_hour")
    p7, r7 = _sec("seven_day")

    out: dict = {
        "five_hour_pct": p5,
        "seven_day_pct": p7,
        "five_hour_reset": r5,
        "seven_day_reset": r7,
    }

    for lim in body.get("limits") or []:
        try:
            model_name = (
                ((lim.get("scope") or {}).get("model") or {}).get("display_name") or ""
            )
        except Exception:
            model_name = ""
        if str(model_name).strip().lower() == "fable":
            out["fable_pct"] = float(lim.get("percent") or 0.0)
            out["fable_reset"] = _parse_iso8601(lim.get("resets_at"))
            break

    out["fetched_at"] = time.time()
    _write_last_good_oauth_usage(out)
    return out


def _fetch_live_rate_limits() -> dict | None:
    """First try /api/oauth/usage (returns Fable data on capable tokens).
    Fall back to POST /v1/messages + anthropic-ratelimit-unified-* headers.
    Returns dict with keys {five_hour_pct, seven_day_pct, five_hour_reset,
    seven_day_reset, source, fetched_at, [fable_pct, fable_reset]} or None."""
    cached = _read_live_cache()
    if cached is not None:
        return cached

    token, source = _resolve_live_token()
    if not token:
        return None

    # Fable harvest runs independently of the token chosen above: it always
    # needs a Desktop (user:profile) token, refreshed on demand. Falling back
    # to the stale cache is unconditional so a token rotation never blanks the
    # Fable bar.
    fable_extra: dict | None = None
    # Keep the Desktop token alive even while the latch is on: it is the only
    # credential that can lift the latch, and letting it die is what turned a
    # 3-second expiry race into a day-long Fable blackout.
    desktop_tok = _get_fresh_desktop_token()
    if not _oauth_usage_forbidden_recently():
        fable_extra = _read_last_good_oauth_usage(_OAUTH_USAGE_REFRESH_TTL)
        if fable_extra is None:
            if desktop_tok and _oauth_usage_last_attempt_ago() >= _OAUTH_USAGE_MIN_INTERVAL:
                _mark_oauth_usage_attempt()
                fable_extra = _try_oauth_usage(desktop_tok)
                if fable_extra is _OAUTH_UNAUTHORIZED:
                    retry_tok = _force_desktop_token_refresh()
                    fable_extra = _try_oauth_usage(retry_tok) if retry_tok else None
                    if fable_extra is _OAUTH_UNAUTHORIZED:
                        fable_extra = None
            if fable_extra is None:
                fable_extra = _read_last_good_oauth_usage(_OAUTH_USAGE_STALE_TTL)

    # --- Path B: POST /v1/messages, read unified headers (no Fable) ---
    body = json.dumps({
        "model": "claude-haiku-4-5-20251001",
        "max_tokens": 1,
        "messages": [{"role": "user", "content": "."}],
    }).encode()
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=body,
        method="POST",
        headers={
            "Authorization": "Bearer " + token,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        },
    )

    def _read_headers(r_or_err) -> dict:
        return {k.lower(): v for k, v in r_or_err.headers.items()}

    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            hdrs = _read_headers(r)
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            # scope/auth problem — can't recover automatically here
            return None
        hdrs = _read_headers(e)   # 429 is fine — headers are still there
    except Exception:
        return None

    def _f(name: str) -> float | None:
        v = hdrs.get(name)
        if v is None:
            return None
        try:
            return float(v)
        except ValueError:
            return None

    h5 = _f("anthropic-ratelimit-unified-5h-utilization")
    h7 = _f("anthropic-ratelimit-unified-7d-utilization")
    if h5 is None and h7 is None:
        return None
    r5 = _f("anthropic-ratelimit-unified-5h-reset")
    r7 = _f("anthropic-ratelimit-unified-7d-reset")

    # Handle "rejected" status: representative-claim bucket is at 100%.
    if hdrs.get("anthropic-ratelimit-unified-status") == "rejected":
        claim = hdrs.get("anthropic-ratelimit-unified-representative-claim")
        if claim == "five_hour":
            h5 = 1.0
        elif claim == "seven_day":
            h7 = 1.0

    out = {
        "five_hour_pct": (h5 or 0.0) * 100.0,
        "seven_day_pct": (h7 or 0.0) * 100.0,
        "five_hour_reset": r5 or 0.0,
        "seven_day_reset": r7 or 0.0,
        "source": source,
        "fetched_at": time.time(),
    }
    if fable_extra and "fable_pct" in fable_extra:
        out["fable_pct"] = fable_extra["fable_pct"]
        out["fable_reset"] = fable_extra.get("fable_reset", 0)
        out["source"] = f"{source}+fable"
    _write_live_cache(out)
    return out


def _read_status_snapshot() -> dict | None:
    try:
        if not SNAPSHOT_FILE.exists():
            return None
        return json.loads(SNAPSHOT_FILE.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"[claude] snapshot read fail: {e}")
        return None


def _last_weekly_reset_jst() -> float:
    """Anthropic's weekly limit resets on Friday 19:59 JST.
    Returns unix timestamp of the most recent past reset."""
    from datetime import datetime
    JST = timezone(timedelta(hours=9))
    now = datetime.now(JST)
    # Python weekday: Monday=0 ... Friday=4 ... Sunday=6
    days_since_fri = (now.weekday() - 4) % 7
    fri = (now - timedelta(days=days_since_fri)).replace(
        hour=19, minute=59, second=0, microsecond=0
    )
    if fri > now:
        fri -= timedelta(days=7)
    return fri.timestamp()


def collect_claude(cfg: dict) -> ClaudeSnapshot:
    snap = ClaudeSnapshot()
    snap.needs_login = needs_login()

    # Primary path (NEW, CodeZeno-style): hit /v1/messages, read the unified
    # rate-limit headers directly. 7d is plan-shared so it always matches
    # Anthropic's UI; 5h is per-token so it reflects this tool's own API session.
    live = _fetch_live_rate_limits()
    if live is not None:
        snap.available = True
        snap.pct_5h = float(live["five_hour_pct"])
        snap.pct_7d = float(live["seven_day_pct"])
        snap.block_resets_at = float(live["five_hour_reset"])
        snap.week_resets_at = float(live["seven_day_reset"])
        snap.snapshot_age_seconds = max(0.0, time.time() - float(live["fetched_at"]))
        snap.model = f"live:{live['source']}"
        # Fable meter (only present when path A succeeded on a scoped token).
        if "fable_pct" in live:
            snap.fable_available = True
            snap.fable_pct = float(live["fable_pct"])
            snap.fable_resets_at = float(live.get("fable_reset") or 0.0)
        # Skip statusLine fallback / local estimate — live is authoritative.
        return _enrich_claude_with_local_cost(snap, cfg)

    # Secondary: statusLine snapshot dropped by claude_status_writer.py
    # (only fires under the CLI Claude Code, not Desktop).
    raw = _read_status_snapshot()
    if raw is not None:
        now_t = time.time()
        five = raw.get("five_hour") or {}
        seven = raw.get("seven_day") or {}
        pct5 = five.get("used_pct")
        pct7 = seven.get("used_pct")
        if pct5 is not None or pct7 is not None:
            snap.available = True
            snap.pct_5h = float(pct5) if pct5 is not None else 0.0
            snap.pct_7d = float(pct7) if pct7 is not None else 0.0
            snap.block_resets_at = float(five.get("resets_at") or 0)
            snap.week_resets_at = float(seven.get("resets_at") or 0)
            snap.context_used_pct = float(raw.get("context_used_pct") or 0.0)
            snap.model = raw.get("model") or ""
            snap.snapshot_age_seconds = max(0.0, now_t - float(raw.get("updated_at") or 0))
            if snap.snapshot_age_seconds > 6 * 3600:
                snap.note = f"snapshot is {snap.snapshot_age_seconds/3600:.1f}h old — start Claude Code to refresh"
            # Fall through so we ALSO compute local cost/tokens as informational.

    return _enrich_claude_with_local_cost(snap, cfg)


def _enrich_claude_with_local_cost(snap: ClaudeSnapshot, cfg: dict) -> ClaudeSnapshot:
    """Compute rl_cost / real_cost from transcripts. If snap.available is False,
    fills the pct bars from the local estimate; otherwise adds informational
    cost figures next to the live values."""
    # Local enrichment: tokens / $ computed from transcripts.
    root = Path(cfg["claude_dir"])
    if not root.exists():
        if not snap.available:
            snap.note = f"not found: {root}; no live data"
        return snap

    # Anthropic's weekly window is boundary-based (Friday 19:59 JST reset), not
    # a rolling 7-day. Sum from the most recent past reset instead.
    weekly_reset_at = _last_weekly_reset_jst()
    cutoff_7d = weekly_reset_at
    next_weekly_reset = weekly_reset_at + 7 * 86400

    seen_request_ids: set[str] = set()
    # Per-event records within 7d window
    events: list[tuple[float, str, dict]] = []  # (ts, model, usage)

    # Only walk files modified since a bit before the weekly reset for performance.
    mtime_cutoff = weekly_reset_at - 86400

    files_scanned = 0
    for p in _iter_claude_files(root):
        try:
            if p.stat().st_mtime < mtime_cutoff:
                continue
        except OSError:
            continue
        files_scanned += 1
        try:
            with p.open("r", encoding="utf-8", errors="ignore") as f:
                for line in f:
                    if '"usage"' not in line or '"assistant"' not in line:
                        continue
                    try:
                        obj = json.loads(line)
                    except Exception:
                        continue
                    if obj.get("type") != "assistant":
                        continue
                    msg = obj.get("message") or {}
                    usage = msg.get("usage") or {}
                    if not usage:
                        continue
                    ts = _parse_ts(obj.get("timestamp", ""))
                    if ts <= 0 or ts < cutoff_7d:
                        continue
                    rid = obj.get("requestId") or msg.get("id")
                    if rid:
                        if rid in seen_request_ids:
                            continue
                        seen_request_ids.add(rid)
                    model = msg.get("model", "")
                    if model == "<synthetic>":
                        continue
                    events.append((ts, model, usage))
        except Exception as e:
            print(f"[claude] read fail {p}: {e}")

    if not events:
        if not snap.available:
            snap.note = f"no usage events found ({files_scanned} files scanned) and no statusLine snapshot"
        return snap

    events.sort(key=lambda x: x[0])

    BLOCK = 5 * 3600
    block_start = events[0][0]
    last_ts = block_start
    block_tokens = 0
    block_cost = 0.0          # real $ cost (informational)
    block_rl_cost = 0.0       # rate-limit-weighted spend (drives the bar)
    block_models: dict[str, int] = {}
    for ts, model, usage in events:
        if ts - last_ts > BLOCK or ts - block_start > BLOCK:
            block_start = ts
            block_tokens = 0
            block_cost = 0.0
            block_rl_cost = 0.0
            block_models = {}
        block_tokens += _total_tokens(usage)
        block_cost += _cost_for_usage(model, usage, cfg)
        block_rl_cost += _ratelimit_cost(model, usage, cfg)
        block_models[model] = block_models.get(model, 0) + _total_tokens(usage)
        last_ts = ts

    tokens_7d = 0
    cost_7d = 0.0
    rl_cost_7d = 0.0
    for ts, model, usage in events:
        if ts < cutoff_7d:
            continue
        tokens_7d += _total_tokens(usage)
        cost_7d += _cost_for_usage(model, usage, cfg)
        rl_cost_7d += _ratelimit_cost(model, usage, cfg)

    snap.tokens_5h = block_tokens
    snap.tokens_7d = tokens_7d
    snap.cost_5h_usd = block_cost
    snap.cost_7d_usd = cost_7d
    snap.block_started_at = block_start
    snap.models_5h = block_models

    # If we still have no authoritative snapshot, fall back to the rate-limit
    # cost estimate against plan ceilings. Mark as approximate.
    if not snap.available:
        plan_limit = cfg_mod.claude_plan_limit_usd(cfg)
        snap.available = True
        snap.pct_5h = (
            min(100.0, (block_rl_cost / plan_limit["window_5h"]) * 100.0)
            if plan_limit["window_5h"] else 0.0
        )
        snap.pct_7d = (
            min(100.0, (rl_cost_7d / plan_limit["window_7d"]) * 100.0)
            if plan_limit["window_7d"] else 0.0
        )
        snap.block_resets_at = block_start + BLOCK
        snap.week_resets_at = next_weekly_reset
        snap.note = "estimate (cache_read excluded; calibrate plan_limits_usd against Anthropic's panel)"
    return snap


# ---------- Combined ----------

@dataclass
class Snapshot:
    codex: CodexSnapshot
    claude: ClaudeSnapshot
    refreshed_at: float = 0.0

    def overall_pct(self) -> float:
        vals = []
        if self.codex.available:
            vals.append(self.codex.primary_pct)
            vals.append(self.codex.secondary_pct)
        if self.claude.available:
            vals.append(self.claude.pct_5h)
            vals.append(self.claude.pct_7d)
        return max(vals) if vals else 0.0


def collect_all(cfg: dict) -> Snapshot:
    return Snapshot(
        codex=collect_codex(cfg),
        claude=collect_claude(cfg),
        refreshed_at=time.time(),
    )
