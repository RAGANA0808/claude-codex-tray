"""Codex rate-limit parsing, including the exhausted-bucket case.

Shapes here are copied from real rollout files: when the metered "codex"
bucket runs out, Codex writes one final event for a window-less bucket
(credits), which used to blank the whole widget at the worst moment.
"""
import json
import time

import parsers


def _token_count(rate_limits: dict, ts: str) -> str:
    return json.dumps({
        "timestamp": ts,
        "type": "event_msg",
        "payload": {"type": "token_count", "rate_limits": rate_limits,
                    "info": {"total_token_usage": {"total_tokens": 1234}}},
    })


def _codex_bucket(pct5: float, pct7: float) -> dict:
    now = int(time.time())
    return {
        "limit_id": "codex", "limit_name": None,
        "primary": {"used_percent": pct5, "window_minutes": 300,
                    "resets_at": now + 3600},
        "secondary": {"used_percent": pct7, "window_minutes": 10080,
                      "resets_at": now + 4 * 86400},
        "credits": {"has_credits": False, "unlimited": False, "balance": "0"},
        "plan_type": "plus", "rate_limit_reached_type": None,
    }


SPENT_BUCKET = {
    "limit_id": "premium", "limit_name": None,
    "primary": None, "secondary": None,
    "credits": {"has_credits": False, "unlimited": False, "balance": "0"},
    "individual_limit": None, "spend_control_reached": None,
    "plan_type": "plus", "rate_limit_reached_type": None,
}


def _write_rollout(tmp_path, events):
    d = tmp_path / "2026" / "09" / "07"
    d.mkdir(parents=True, exist_ok=True)
    f = d / "rollout-2026-09-07T09-20-48-test.jsonl"
    f.write_text("\n".join(events) + "\n", encoding="utf-8")
    return f


# --- helpers ---------------------------------------------------------

def test_rl_has_windows():
    assert parsers._rl_has_windows(_codex_bucket(10, 5))
    assert not parsers._rl_has_windows(SPENT_BUCKET)
    assert not parsers._rl_has_windows({})
    assert not parsers._rl_has_windows(None)


# --- the bug this suite exists for -----------------------------------

def test_recovers_last_reading_when_bucket_is_spent(tmp_path):
    """The window-less final event must not blank the meters."""
    _write_rollout(tmp_path, [
        _token_count(_codex_bucket(84, 32), "2026-09-07T00:23:08Z"),
        _token_count(_codex_bucket(92, 33), "2026-09-07T00:23:21Z"),
        _token_count(SPENT_BUCKET, "2026-09-07T00:23:22Z"),
    ])
    snap = parsers.collect_codex({"codex_dir": str(tmp_path)})
    assert snap.available
    assert snap.limit_reached
    # percentages come from the last windowed reading, not from nowhere
    assert snap.has_primary and snap.primary_pct == 92.0
    assert snap.has_secondary and snap.secondary_pct == 33.0
    assert snap.primary_resets_at > time.time()
    assert snap.reading_at == "2026-09-07T00:23:21Z"
    assert snap.plan_type == "plus"


def test_healthy_session_is_not_flagged(tmp_path):
    _write_rollout(tmp_path, [
        _token_count(_codex_bucket(8, 20), "2026-09-07T00:20:56Z"),
        _token_count(_codex_bucket(11, 21), "2026-09-07T00:21:05Z"),
    ])
    snap = parsers.collect_codex({"codex_dir": str(tmp_path)})
    assert snap.available and not snap.limit_reached
    assert snap.primary_pct == 11.0 and snap.secondary_pct == 21.0


def test_spent_bucket_with_no_history_still_reports_the_limit(tmp_path):
    """No windowed reading anywhere — say "limit reached", not "no data"."""
    _write_rollout(tmp_path, [_token_count(SPENT_BUCKET, "2026-09-07T00:23:22Z")])
    snap = parsers.collect_codex({"codex_dir": str(tmp_path)})
    assert snap.limit_reached
    assert snap.available          # we do know the plan and the state
    assert not snap.has_primary    # but honestly report no percentage
    assert "上限" in snap.note


def test_predicate_scan_works_on_large_files(tmp_path):
    """The backwards chunk scan must honour the predicate past 256KB."""
    filler = json.dumps({"timestamp": "x", "type": "response_item",
                         "payload": {"type": "message", "pad": "z" * 2000}})
    events = ([_token_count(_codex_bucket(92, 33), "2026-09-07T00:23:21Z")]
              + [filler] * 200
              + [_token_count(SPENT_BUCKET, "2026-09-07T00:23:22Z")])
    f = _write_rollout(tmp_path, events)
    assert f.stat().st_size > 256 * 1024   # forces the tail-seek path
    snap = parsers.collect_codex({"codex_dir": str(tmp_path)})
    assert snap.limit_reached
    assert snap.primary_pct == 92.0


# --- which reading wins ----------------------------------------------

def _write_named(tmp_path, name, events, mtime=None):
    import os
    d = tmp_path / "2026" / "09" / "09"
    d.mkdir(parents=True, exist_ok=True)
    f = d / f"rollout-{name}.jsonl"
    f.write_text("\n".join(events) + "\n", encoding="utf-8")
    if mtime is not None:
        os.utime(f, (mtime, mtime))
    return f


def _bucket(pct5, pct7, reset_in=3600):
    now = int(time.time())
    return {
        "limit_id": "codex", "limit_name": None,
        "primary": {"used_percent": pct5, "window_minutes": 300,
                    "resets_at": now + reset_in},
        "secondary": {"used_percent": pct7, "window_minutes": 10080,
                      "resets_at": now + 4 * 86400},
        "credits": {"has_credits": False, "unlimited": False, "balance": "0"},
        "plan_type": "plus", "rate_limit_reached_type": None,
    }


def test_file_mtime_does_not_decide_the_reading(tmp_path):
    """mtime moves on any write, so the newest file can hold an older figure.

    Parallel sessions each record their own snapshot; usage only climbs inside
    a window, so the highest reading for the live window is the closest one.
    """
    now = time.time()
    _write_named(tmp_path, "low",
                 [_token_count(_bucket(87, 29), "2026-09-09T00:32:55.570Z")],
                 mtime=now)              # newest mtime, lower reading
    _write_named(tmp_path, "high",
                 [_token_count(_bucket(90, 30), "2026-09-09T00:32:55.708Z")],
                 mtime=now - 300)        # older mtime, newer/higher reading
    snap = parsers.collect_codex({"codex_dir": str(tmp_path)})
    assert snap.primary_pct == 90.0
    assert snap.secondary_pct == 30.0


def test_expired_window_never_beats_the_live_one(tmp_path):
    """A high figure from a window that already reset must not be shown."""
    now = time.time()
    _write_named(tmp_path, "spent",
                 [_token_count(_bucket(98, 40, reset_in=-600),
                               "2026-09-09T00:10:00.000Z")], mtime=now)
    _write_named(tmp_path, "current",
                 [_token_count(_bucket(12, 31), "2026-09-09T00:40:00.000Z")],
                 mtime=now - 60)
    snap = parsers.collect_codex({"codex_dir": str(tmp_path)})
    assert snap.primary_pct == 12.0


def test_old_reading_is_flagged_stale(tmp_path):
    from datetime import datetime, timedelta, timezone
    old = (datetime.now(timezone.utc) - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    _write_named(tmp_path, "old", [_token_count(_bucket(87, 29), old)])
    snap = parsers.collect_codex({"codex_dir": str(tmp_path)})
    assert snap.stale
    assert snap.reading_age_seconds > 3000
    assert "更新されません" in snap.note


def test_recent_reading_is_not_stale(tmp_path):
    from datetime import datetime, timedelta, timezone
    fresh = (datetime.now(timezone.utc) - timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    _write_named(tmp_path, "fresh", [_token_count(_bucket(40, 20), fresh)])
    snap = parsers.collect_codex({"codex_dir": str(tmp_path)})
    assert not snap.stale and snap.note == ""


def test_stale_threshold_is_configurable(tmp_path):
    from datetime import datetime, timedelta, timezone
    ts = (datetime.now(timezone.utc) - timedelta(minutes=20)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    _write_named(tmp_path, "mid", [_token_count(_bucket(40, 20), ts)])
    cfg = {"codex_dir": str(tmp_path)}
    assert parsers.collect_codex(cfg).stale                      # default 10min
    assert not parsers.collect_codex({**cfg, "codex_stale_minutes": 60}).stale
