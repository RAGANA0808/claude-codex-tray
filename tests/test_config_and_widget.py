"""Unit tests for config.py merging/pricing and taskbar_widget pure helpers."""
import time
import types

import config
import taskbar_widget as tw


# --- config ----------------------------------------------------------

def test_deep_merge_nested_override():
    base = {"a": {"x": 1, "y": 2}, "b": 3}
    over = {"a": {"y": 9}, "c": 4}
    out = config._deep_merge(base, over)
    assert out == {"a": {"x": 1, "y": 9}, "b": 3, "c": 4}
    assert base["a"]["y"] == 2  # no mutation


def test_model_pricing_substring_match():
    cfg = {"pricing": config.DEFAULTS["pricing"]}
    assert config.model_pricing(cfg, "claude-opus-5")["input"] == 15.0
    assert config.model_pricing(cfg, "claude-haiku-4-5")["input"] == 1.0
    assert config.model_pricing(cfg, "mystery-model") == cfg["pricing"]["default"]


def test_plan_limit_falls_back_to_max20():
    cfg = {"claude_plan": "no-such-plan",
           "plan_limits_usd": config.DEFAULTS["plan_limits_usd"]}
    assert config.claude_plan_limit_usd(cfg) == cfg["plan_limits_usd"]["max20"]


# --- colour helpers --------------------------------------------------

def test_hex_rgb_roundtrip():
    assert tw._hex_to_rgb("#3fbf6a") == (0x3F, 0xBF, 0x6A)
    assert tw._rgb_to_hex((0x3F, 0xBF, 0x6A)) == "#3fbf6a"


def test_pick_color_thresholds():
    assert tw._pick_color(None, 50, 80) == tw.COLOR_NA
    assert tw._pick_color(10, 50, 80) == tw.COLOR_OK
    assert tw._pick_color(50, 50, 80) == tw.COLOR_WARN
    assert tw._pick_color(80, 50, 80) == tw.COLOR_DANGER


def test_adj_cancels_taskbar_composite_offset():
    """Embedded mode pre-darkens colours by the taskbar bg so the additive
    composite lands on the authored colour; floating mode is untouched."""
    w = types.SimpleNamespace(embedded=True,
                              _bg_offset=tw._hex_to_rgb(tw.TASKBAR_BG))
    assert tw.TaskbarWidget._adj(w, "#111111") == "#000000"
    assert tw.TaskbarWidget._adj(w, "#3fbf6a") == "#2eae59"
    assert tw.TaskbarWidget._adj(w, "#000000") == "#000000"  # clamps at 0
    w.embedded = False
    assert tw.TaskbarWidget._adj(w, "#3fbf6a") == "#3fbf6a"


def test_i32_wraps_like_win32():
    assert tw._i32(0x80000000) == -0x80000000
    assert tw._i32(0x7FFFFFFF) == 0x7FFFFFFF
    assert tw._i32(-1 & 0xFFFFFFFF) == -1


# --- countdown formatting --------------------------------------------

def test_format_countdown_buckets():
    now = time.time()
    assert tw._format_countdown(None) == ""
    assert tw._format_countdown(now - 5) == "now"
    assert tw._format_countdown(now + 30) == "<1m"
    # +2s guards against int() truncation shaving one unit off the boundary
    assert tw._format_countdown(now + 5 * 60 + 2) == "5m"
    assert tw._format_countdown(now + 3 * 3600 + 2) == "3h"
    assert tw._format_countdown(now + 2 * 86400 + 2) == "2d"


# --- embed calibration decision --------------------------------------

def test_calibration_additive_dark_confirmed():
    # reon's machine: backdrop (17,17,17), gray probe comes back +128
    v, off = tw._classify_calibration((17, 17, 17), (145, 145, 145))
    assert v == "ok" and off == (17, 17, 17)


def test_calibration_invisible_falls_back():
    # paint changes nothing on screen → the XAML layer covers us
    v, off = tw._classify_calibration((30, 30, 30), (31, 30, 32))
    assert v == "invisible" and off is None


def test_calibration_light_theme_falls_back():
    # light taskbar backdrop → additive model saturates, unreadable
    v, off = tw._classify_calibration((243, 242, 244), (255, 255, 255))
    assert v == "too-bright" and off is None


def test_calibration_alpha_blend_is_approx():
    # visible but not additive (e.g. 50% alpha) → best-effort offset
    v, off = tw._classify_calibration((20, 20, 22), (84, 84, 86))
    assert v == "approx" and off == (20, 20, 22)


def test_calibration_sampling_failure_keeps_guess():
    assert tw._classify_calibration(None, (1, 2, 3)) == ("unknown", None)
    assert tw._classify_calibration((1, 2, 3), None) == ("unknown", None)
