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
