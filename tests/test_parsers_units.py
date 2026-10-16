"""Unit tests for the pure logic in parsers.py — no network, no ~/.claude."""
import io
import json
import time
import urllib.error

import pytest

import parsers


# --- _parse_iso8601 --------------------------------------------------

def test_parse_iso8601_z_suffix():
    ts = parsers._parse_iso8601("2026-01-01T00:00:00Z")
    assert ts == pytest.approx(1767225600.0)


def test_parse_iso8601_offset():
    utc = parsers._parse_iso8601("2026-01-01T09:00:00+09:00")
    assert utc == pytest.approx(1767225600.0)


@pytest.mark.parametrize("bad", [None, "", "not-a-date", 0])
def test_parse_iso8601_garbage_returns_zero(bad):
    assert parsers._parse_iso8601(bad) == 0.0


# --- token accounting ------------------------------------------------

def test_total_tokens_sums_all_buckets():
    usage = {"input_tokens": 1, "output_tokens": 2,
             "cache_creation_input_tokens": 3, "cache_read_input_tokens": 4}
    assert parsers._total_tokens(usage) == 10


def test_total_tokens_tolerates_missing_and_none():
    assert parsers._total_tokens({}) == 0
    assert parsers._total_tokens({"input_tokens": None, "output_tokens": 5}) == 5


# --- oauth-usage endpoint parsing ------------------------------------

class _FakeResponse:
    def __init__(self, body: dict):
        self._raw = json.dumps(body).encode("utf-8")

    def read(self):
        return self._raw

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


OAUTH_BODY = {
    "five_hour": {"utilization": 12.5, "resets_at": "2026-01-01T00:00:00Z"},
    "seven_day": {"utilization": 40.0, "resets_at": "2026-01-03T00:00:00Z"},
    "limits": [
        {"kind": "session", "percent": 12, "scope": None},
        {"kind": "weekly_scoped", "percent": 7,
         "resets_at": "2026-01-03T00:00:00Z",
         "scope": {"model": {"id": None, "display_name": "Fable"}}},
    ],
}


@pytest.fixture
def isolated_cache(tmp_path, monkeypatch):
    """Point every cache-file constant at a temp dir."""
    for name in ("_OAUTH_USAGE_LAST_GOOD", "_OAUTH_USAGE_STATE",
                 "_OAUTH_USAGE_LAST_ATTEMPT", "_CLI_REFRESH_LAST_ATTEMPT",
                 "_NEEDS_LOGIN_FLAG"):
        monkeypatch.setattr(parsers, name, tmp_path / f"{name}.json")
    return tmp_path


def test_oauth_usage_extracts_fable_meter(isolated_cache, monkeypatch):
    monkeypatch.setattr(parsers.urllib.request, "urlopen",
                        lambda req, timeout=0: _FakeResponse(OAUTH_BODY))
    out = parsers._try_oauth_usage("tok")
    assert out["five_hour_pct"] == 12.5
    assert out["seven_day_pct"] == 40.0
    assert out["fable_pct"] == 7.0
    assert out["fable_reset"] > 0
    # success must persist to the last-good cache
    cached = json.loads(parsers._OAUTH_USAGE_LAST_GOOD.read_text(encoding="utf-8"))
    assert cached["fable_pct"] == 7.0


def _http_error(code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError("u", code, "err", {}, io.BytesIO(b""))


def test_oauth_usage_401_returns_sentinel_not_latch(isolated_cache, monkeypatch):
    def raise401(req, timeout=0):
        raise _http_error(401)
    monkeypatch.setattr(parsers.urllib.request, "urlopen", raise401)
    assert parsers._try_oauth_usage("tok") is parsers._OAUTH_UNAUTHORIZED
    # a 401 is a token problem, not a scope refusal — must NOT latch
    assert not parsers._oauth_usage_forbidden_recently()


def test_oauth_usage_403_latches_current_token(isolated_cache, monkeypatch):
    def raise403(req, timeout=0):
        raise _http_error(403)
    monkeypatch.setattr(parsers.urllib.request, "urlopen", raise403)
    monkeypatch.setattr(parsers, "_get_desktop_access_token",
                        lambda margin_sec=0: ("tok-a", False))
    assert parsers._try_oauth_usage("tok-a") is None
    assert parsers._oauth_usage_forbidden_recently()


def test_forbidden_latch_lifts_on_token_rotation(isolated_cache, monkeypatch):
    parsers._mark_oauth_usage_forbidden("tok-a")
    monkeypatch.setattr(parsers, "_get_desktop_access_token",
                        lambda margin_sec=0: ("tok-b", False))
    # a different token deserves a fresh chance immediately
    assert not parsers._oauth_usage_forbidden_recently()


def test_forbidden_latch_expires(isolated_cache):
    parsers._OAUTH_USAGE_STATE.write_text(json.dumps(
        {"forbidden_at": time.time() - parsers._OAUTH_USAGE_FORBIDDEN_TTL - 1,
         "token": ""}), encoding="utf-8")
    assert not parsers._oauth_usage_forbidden_recently()


# --- token expiry margin ---------------------------------------------

def test_desktop_token_expiry_margin(tmp_path, monkeypatch):
    cred = tmp_path / ".credentials.json"
    monkeypatch.setattr(parsers.Path, "home", classmethod(lambda cls: tmp_path))
    (tmp_path / ".claude").mkdir()
    cred = tmp_path / ".claude" / ".credentials.json"
    # expires in 60s: fine with margin 0, "expired" with the 300s safety margin
    cred.write_text(json.dumps({"claudeAiOauth": {
        "accessToken": "t", "expiresAt": int((time.time() + 60) * 1000)}}),
        encoding="utf-8")
    tok, expired = parsers._get_desktop_access_token(margin_sec=0)
    assert tok == "t" and not expired
    tok, expired = parsers._get_desktop_access_token()
    assert expired


# --- the usage endpoint is rate limited harder than /v1/messages -------

def test_usage_endpoint_keeps_its_own_floor():
    """Lowering the general refresh must not hammer /api/oauth/usage."""
    try:
        parsers.set_live_refresh(15)
        assert parsers._LIVE_TTL_SEC == 15
        assert parsers._OAUTH_USAGE_MIN_INTERVAL >= parsers._OAUTH_USAGE_FLOOR
        assert parsers._OAUTH_USAGE_REFRESH_TTL >= parsers._OAUTH_USAGE_FLOOR
    finally:
        parsers.set_live_refresh(60)


def test_429_backs_off_and_doubles(isolated_cache, monkeypatch):
    def raise429(req, timeout=0):
        raise _http_error(429)
    monkeypatch.setattr(parsers.urllib.request, "urlopen", raise429)

    assert parsers._oauth_usage_backoff_remaining() == 0
    parsers._try_oauth_usage("tok")
    first = parsers._oauth_usage_backoff_remaining()
    assert first >= parsers._OAUTH_USAGE_BACKOFF_MIN - 1

    parsers._try_oauth_usage("tok")
    second = parsers._oauth_usage_backoff_remaining()
    assert second > first          # refused again -> wait longer


def test_backoff_is_capped(isolated_cache, monkeypatch):
    def raise429(req, timeout=0):
        raise _http_error(429)
    monkeypatch.setattr(parsers.urllib.request, "urlopen", raise429)
    for _ in range(12):
        parsers._try_oauth_usage("tok")
    assert parsers._oauth_usage_backoff_remaining() <= parsers._OAUTH_USAGE_BACKOFF_MAX + 1


def test_success_clears_the_backoff(isolated_cache, monkeypatch):
    parsers._note_oauth_usage_rate_limited()
    assert parsers._oauth_usage_backoff_remaining() > 0
    monkeypatch.setattr(parsers.urllib.request, "urlopen",
                        lambda req, timeout=0: _FakeResponse(OAUTH_BODY))
    assert parsers._try_oauth_usage("tok")["fable_pct"] == 7.0
    assert parsers._oauth_usage_backoff_remaining() == 0


def test_429_still_serves_the_last_good_reading(isolated_cache, monkeypatch):
    """Fable must not blink out just because the endpoint is busy."""
    monkeypatch.setattr(parsers.urllib.request, "urlopen",
                        lambda req, timeout=0: _FakeResponse(OAUTH_BODY))
    parsers._try_oauth_usage("tok")
    def raise429(req, timeout=0):
        raise _http_error(429)
    monkeypatch.setattr(parsers.urllib.request, "urlopen", raise429)
    out = parsers._try_oauth_usage("tok")
    assert out is not None and out["fable_pct"] == 7.0


# --- finding the Claude Code CLI -----------------------------------------

def _version_dir(root, version, verified=True):
    d = root / version
    d.mkdir(parents=True)
    (d / "claude.exe").write_bytes(b"MZ")
    if verified:
        (d / ".verified").write_text("x", encoding="utf-8")
    return d


@pytest.fixture
def no_cli_elsewhere(tmp_path, monkeypatch):
    """A machine where Claude Code exists only inside the desktop app."""
    import shutil
    monkeypatch.setattr(shutil, "which", lambda *a, **k: None)
    monkeypatch.setattr(parsers, "_registry_path", lambda: "")
    monkeypatch.setattr(parsers.Path, "home", classmethod(lambda cls: tmp_path / "home"))
    monkeypatch.setenv("APPDATA", str(tmp_path / "Roaming"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "Local"))
    return tmp_path


def test_the_store_desktop_app_copy_is_found_under_its_package(no_cli_elsewhere):
    """The Microsoft Store build virtualises AppData: from outside the app its
    Claude Code lives under Packages/Claude_*/LocalCache, and %APPDATA%/Claude
    is empty. That is why "Claude にログイン" said Claude Code was missing."""
    root = (no_cli_elsewhere / "Local" / "Packages" / "Claude_pzs8sxrjxfjjc"
            / "LocalCache" / "Roaming" / "Claude" / "claude-code")
    _version_dir(root, "2.1.260")
    newest = _version_dir(root, "2.1.280")
    assert parsers._find_claude_cli() == newest / "claude.exe"


def test_a_plain_install_copy_in_appdata_is_found_too(no_cli_elsewhere):
    root = no_cli_elsewhere / "Roaming" / "Claude" / "claude-code"
    d = _version_dir(root, "2.1.9")
    assert parsers._find_claude_cli() == d / "claude.exe"


def test_versions_compare_as_numbers_not_text(no_cli_elsewhere):
    root = no_cli_elsewhere / "Roaming" / "Claude" / "claude-code"
    _version_dir(root, "2.1.99")
    newest = _version_dir(root, "2.1.100")
    assert parsers._find_claude_cli() == newest / "claude.exe"


def test_a_half_downloaded_version_is_skipped(no_cli_elsewhere):
    root = no_cli_elsewhere / "Roaming" / "Claude" / "claude-code"
    good = _version_dir(root, "2.1.280")
    _version_dir(root, "2.1.290", verified=False)        # still downloading
    assert parsers._find_claude_cli() == good / "claude.exe"


def test_a_standalone_install_wins_over_the_desktop_copy(no_cli_elsewhere):
    root = no_cli_elsewhere / "Roaming" / "Claude" / "claude-code"
    _version_dir(root, "2.1.280")
    own = no_cli_elsewhere / "home" / ".local" / "bin"
    own.mkdir(parents=True)
    (own / "claude.exe").write_bytes(b"MZ")
    assert parsers._find_claude_cli() == own / "claude.exe"


def test_nothing_installed_is_reported_as_none(no_cli_elsewhere):
    assert parsers._find_claude_cli() is None


def test_a_path_added_after_start_up_is_still_searched(no_cli_elsewhere, monkeypatch):
    """Our own PATH is from sign-in; the registry has the current one."""
    import shutil
    fresh = no_cli_elsewhere / "fresh"
    monkeypatch.setattr(parsers, "_registry_path", lambda: str(fresh))
    monkeypatch.setattr(shutil, "which",
                        lambda name, path=None, **k: str(fresh / "claude.exe") if path else None)
    assert parsers._find_claude_cli() == fresh / "claude.exe"


# --- incremental transcript reading ----------------------------------

def _assistant_line(rid, ts="2026-01-01T00:00:00Z", tokens=10):
    return json.dumps({
        "type": "assistant", "timestamp": ts, "requestId": rid,
        "message": {"model": "m", "usage": {"input_tokens": tokens}},
    }) + "\n"


def test_read_claude_events_reads_only_appended_lines(tmp_path, monkeypatch):
    monkeypatch.setattr(parsers, "_CLAUDE_FILE_CACHE", {})
    p = tmp_path / "s.jsonl"
    p.write_text(_assistant_line("a"), encoding="utf-8")
    first = parsers._read_claude_events(str(p), p.stat().st_size, 0.0)
    assert [e[1] for e in first] == ["a"]

    # A line still being written is left for the next poll.
    partial = _assistant_line("b")
    with p.open("a", encoding="utf-8") as f:
        f.write(partial[:20])
    got = parsers._read_claude_events(str(p), p.stat().st_size, 0.0)
    assert [e[1] for e in got] == ["a"]

    with p.open("a", encoding="utf-8") as f:
        f.write(partial[20:])
    got = parsers._read_claude_events(str(p), p.stat().st_size, 0.0)
    assert [e[1] for e in got] == ["a", "b"]


def test_read_claude_events_restarts_when_file_shrinks(tmp_path, monkeypatch):
    monkeypatch.setattr(parsers, "_CLAUDE_FILE_CACHE", {})
    p = tmp_path / "s.jsonl"
    p.write_text(_assistant_line("a") + _assistant_line("b"), encoding="utf-8")
    parsers._read_claude_events(str(p), p.stat().st_size, 0.0)
    p.write_text(_assistant_line("c"), encoding="utf-8")
    got = parsers._read_claude_events(str(p), p.stat().st_size, 0.0)
    assert [e[1] for e in got] == ["c"]


def test_read_claude_events_drops_events_before_cutoff(tmp_path, monkeypatch):
    monkeypatch.setattr(parsers, "_CLAUDE_FILE_CACHE", {})
    p = tmp_path / "s.jsonl"
    p.write_text(_assistant_line("old", "2026-01-01T00:00:00Z")
                 + _assistant_line("new", "2026-01-08T00:00:00Z"), encoding="utf-8")
    size = p.stat().st_size
    parsers._read_claude_events(str(p), size, 0.0)
    cutoff = parsers._parse_ts("2026-01-05T00:00:00Z")
    got = parsers._read_claude_events(str(p), size, cutoff)
    assert [e[1] for e in got] == ["new"]
