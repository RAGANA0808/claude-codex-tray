"""Widget geometry: column sizing and which sides are drawn."""
from pathlib import Path

import pytest

import taskbar_widget as tw


@pytest.fixture(autouse=True)
def restore_globals():
    keep = {k: getattr(tw, k) for k in
            ("SCALE", "WIDTH", "HEIGHT", "ICON_PX", "PAD_X", "GAP_ICON", "GAP",
             "BAR_W", "BAR_H", "ROW_Y_TOP", "INTER_GROUP_GAP",
             "CAP_W", "PCT_W", "TIME_W", "GROUP_W",
             "SIZE", "CAP_GAP", "TIME_GAP", "SHOW_TIME")}
    yield
    for k, v in keep.items():
        setattr(tw, k, v)


# --- which sides ------------------------------------------------------

def test_sides_default_to_both():
    assert tw.sides_shown({}) == (True, True)


def test_sides_can_be_switched_off():
    assert tw.sides_shown({"show_codex": False}) == (True, False)
    assert tw.sides_shown({"show_claude": False}) == (False, True)


def test_both_off_falls_back_to_both():
    """An empty bar helps nobody — the widget-visibility toggle covers that."""
    assert tw.sides_shown({"show_claude": False, "show_codex": False}) == (True, True)


# --- width ------------------------------------------------------------

def test_hiding_a_side_narrows_the_widget():
    both = tw.apply_layout({})
    claude_only = tw.apply_layout({"show_codex": False})
    codex_only = tw.apply_layout({"show_claude": False})
    assert claude_only < both and codex_only < both
    # Claude carries the extra Fable group, so its side is the wider one
    assert codex_only < claude_only


def test_width_covers_every_column():
    tw.apply_layout({})
    claude_side = tw.side_width(True)
    codex_side = tw.side_width(False)
    assert claude_side == tw.ICON_PX + tw.GAP_ICON + tw.GROUP_W * 2 + tw.INTER_GROUP_GAP
    assert codex_side == tw.ICON_PX + tw.GAP_ICON + tw.GROUP_W
    assert tw.WIDTH >= claude_side + codex_side


def test_group_width_is_the_sum_of_its_columns():
    tw.apply_layout({}, "full")
    assert tw.GROUP_W == (tw.CAP_W + tw.CAP_GAP + tw.BAR_W + tw.GAP + tw.PCT_W
                          + tw.TIME_GAP + tw.TIME_W)
    tw.apply_layout({}, "slim")          # no meter: caption, number, countdown
    assert tw.BAR_W == 0
    assert tw.GROUP_W == tw.CAP_W + tw.CAP_GAP + tw.PCT_W + tw.TIME_GAP + tw.TIME_W
    tw.apply_layout({}, "mini")          # numbers only
    assert not tw.SHOW_TIME
    assert tw.GROUP_W == tw.CAP_W + tw.CAP_GAP + tw.PCT_W


# --- the overflow this file exists for --------------------------------

def test_percentage_column_fits_three_digits(tk_root):
    """"100%" used to print straight over the reset countdown beside it."""
    import tkinter.font as tkfont
    tw.init_scale(tk_root, {})
    f = tkfont.Font(root=tk_root, family=tw.FONT_PCT[0], size=tw.FONT_PCT[1])
    assert tw.PCT_W >= f.measure("100%")
    t = tkfont.Font(root=tk_root, family=tw.FONT_TIME[0], size=tw.FONT_TIME[1])
    assert tw.TIME_W >= max(t.measure(s) for s in ("<1m", "now", "10d", "23h"))
    # the embedded bar draws with PIL, whose metrics differ slightly
    assert tw.PCT_W >= tw._pil_font(tw.FONT_PCT).getlength("100%")


# --- theme ------------------------------------------------------------

def test_theme_can_be_forced():
    assert tw.apply_theme({"theme": "light"}) == "light"
    assert tw.TASKBAR_BG == tw.LIGHT_PALETTE["taskbar_bg"]
    assert tw.apply_theme({"theme": "dark"}) == "dark"
    assert tw.TASKBAR_BG == tw.DARK_PALETTE["taskbar_bg"]


def test_auto_theme_follows_windows(monkeypatch):
    monkeypatch.setattr(tw, "system_uses_light_taskbar", lambda: True)
    assert tw.apply_theme({}) == "light"
    monkeypatch.setattr(tw, "system_uses_light_taskbar", lambda: False)
    assert tw.apply_theme({"theme": "auto"}) == "dark"


def test_every_palette_key_is_applied():
    for theme, pal in (("light", tw.LIGHT_PALETTE), ("dark", tw.DARK_PALETTE)):
        tw.apply_theme({"theme": theme})
        assert tw.BG == pal["taskbar_bg"] and tw.BG_BAR == pal["bg_bar"]
        assert tw.FG_LABEL == pal["fg_label"] and tw.FG_DIM == pal["fg_dim"]
        assert tw.DIVIDER == pal["divider"]
        assert tw.TILE_FILL == pal["tile_fill"] and tw.TILE_EDGE == pal["tile_edge"]
        assert tw.COLOR_OK == pal["ok"] and tw.COLOR_WARN == pal["warn"]
        assert tw.COLOR_DANGER == pal["danger"] and tw.COLOR_NA == pal["na"]
        assert tw.COLOR_FABLE == pal["fable"]


def test_light_text_is_dark_enough_to_read():
    """A light bar needs dark ink; the dark palette's would vanish on it."""
    def lum(h):
        r, g, b = tw._hex_to_rgb(h)
        return 0.2126 * r + 0.7152 * g + 0.0722 * b
    bg = lum(tw.LIGHT_PALETTE["taskbar_bg"])
    for key in ("fg_label", "fg_dim", "ok", "warn", "danger"):
        assert bg - lum(tw.LIGHT_PALETTE[key]) > 60, key


def test_taskbar_bg_override_still_wins():
    tw.apply_theme({"theme": "dark", "taskbar_bg": "#010203"})
    assert tw.TASKBAR_BG == "#010203"


# --- calibration guards ------------------------------------------------

def test_session_locked_returns_a_bool():
    assert isinstance(tw.session_locked(), bool)


def test_point_belongs_to_walks_up_to_the_owner(monkeypatch):
    """A hit on a child counts as a hit on the window that owns it.

    Screen hit-testing is the OS's job and is covered by running the real
    widget; what matters here is that the ancestor walk finds the owner and
    still terminates for an unrelated handle.
    """
    chain = {30: 20, 20: 10, 10: 0}          # child -> parent -> ... -> none

    def parent(h):
        v = h.value if hasattr(h, "value") else h
        return chain.get(int(v or 0), 0)

    class FakeUser32:
        WindowFromPoint = staticmethod(lambda pt: 30)
        GetParent = staticmethod(parent)

    real = tw.ctypes.windll

    class FakeWindll:
        user32 = FakeUser32

        def __getattr__(self, name):
            return getattr(real, name)

    monkeypatch.setattr(tw.ctypes, "windll", FakeWindll())
    assert tw._point_belongs_to(30, 1, 1)    # the hit window itself
    assert tw._point_belongs_to(10, 1, 1)    # an ancestor of it
    assert not tw._point_belongs_to(99, 1, 1)


def test_point_belongs_to_is_safe_on_a_dead_handle():
    assert tw._point_belongs_to(0, 10, 10) is False


# --- the calibration probe must not become the background --------------

def test_probe_box_is_small_and_inside_the_bar():
    tw.apply_layout({})
    x0, y0, x1, y1 = tw.TaskbarWidget._probe_box(None)   # uses module geometry only
    assert 0 < x0 < x1 < tw.WIDTH
    assert 0 < y0 < y1 < tw.HEIGHT
    assert (x1 - x0) <= tw.WIDTH // 4      # a patch, not the whole bar


def test_probe_draws_a_patch_and_leaves_the_background_alone(tk_root):
    """A probe that recoloured the whole canvas burned a grey block into the
    taskbar whenever calibration was interrupted."""
    import tkinter as tk
    import types
    tw.init_scale(tk_root, {})
    canvas = tk.Canvas(tk_root, width=tw.WIDTH, height=tw.HEIGHT)
    try:
        w = types.SimpleNamespace(canvas=canvas, win=tk_root, embedded=True, cfg={},
                                  _layered=False,
                                  _bg_offset=tw._hex_to_rgb(tw.TASKBAR_BG))
        w._adj = lambda c: tw.TaskbarWidget._adj(w, c)
        w._apply_colors = lambda: tw.TaskbarWidget._apply_colors(w)
        w._probe_box = lambda: tw.TaskbarWidget._probe_box(w)
        w.float_is_transparent = lambda: tw.TaskbarWidget.float_is_transparent(w)

        tw.TaskbarWidget._probe_paint(w, "#808080")
        assert str(canvas["bg"]).lower() != "#808080"
        assert str(canvas["bg"]).lower() == w._adj(tw.BG).lower()
        assert len(canvas.find_all()) == 1        # just the patch

        # and blanking puts the background back, whatever the probe left
        canvas.configure(bg="#808080")
        tw.TaskbarWidget._blank(w)
        assert str(canvas["bg"]).lower() == w._adj(tw.BG).lower()
        assert canvas.find_all() == ()
    finally:
        canvas.destroy()


# --- floating over a taskbar we cannot match ---------------------------

def test_transparency_is_opt_in():
    """Transparency looks perfect but makes the window layered, and a layered
    window can lose the z-order fight with the taskbar and blink out."""
    import types
    w = types.SimpleNamespace(embedded=False, cfg={})
    assert not tw.TaskbarWidget.float_is_transparent(w)
    w.cfg = {"float_transparent": True}
    assert tw.TaskbarWidget.float_is_transparent(w)
    w.embedded = True
    assert not tw.TaskbarWidget.float_is_transparent(w)   # embedding composites


def test_float_background_uses_the_sampled_taskbar_colour(monkeypatch):
    """A taskbar with transparency effects has no fixed colour to hard-code."""
    import types
    monkeypatch.setattr(tw, "session_locked", lambda: False)
    monkeypatch.setattr(tw, "_window_rect", lambda h: (0, 0, 10, 10))
    monkeypatch.setattr(tw, "sample_taskbar_beside", lambda r: "#dfdae1")
    w = types.SimpleNamespace(embedded=False, cfg={}, _hwnd=1, _matched_bg=None)
    assert tw.TaskbarWidget._float_background(w) == "#dfdae1"
    assert w._matched_bg == "#dfdae1"      # remembered for when sampling fails


def test_float_background_keeps_the_last_match_while_locked(monkeypatch):
    import types
    monkeypatch.setattr(tw, "session_locked", lambda: True)
    w = types.SimpleNamespace(embedded=False, cfg={}, _hwnd=1, _matched_bg="#dfdae1")
    # a lock screen is not the taskbar, so it must not be sampled
    monkeypatch.setattr(tw, "sample_taskbar_beside",
                        lambda r: pytest.fail("must not sample while locked"))
    assert tw.TaskbarWidget._float_background(w) == "#dfdae1"


def test_float_matching_can_be_switched_off():
    import types
    w = types.SimpleNamespace(embedded=False, cfg={"float_match_taskbar": False},
                              _hwnd=1, _matched_bg=None)
    assert tw.TaskbarWidget._float_background(w) == tw.BG


def test_embedded_never_samples_the_strip():
    import types
    w = types.SimpleNamespace(embedded=True, cfg={}, _hwnd=1, _matched_bg=None)
    assert tw.TaskbarWidget._float_background(w) == tw.BG


# --- unknown is not zero ------------------------------------------------

def test_unreadable_fable_is_not_reported_as_zero():
    """0% reads as "nothing used"; the truth was "cannot be read"."""
    src = Path(tw.__file__).read_text(encoding="utf-8")
    assert "fable_pct = 0.0" not in src
    assert "fable_shown = True" in src


def test_draw_side_accepts_a_shown_but_unknown_extra():
    import inspect
    src = inspect.getsource(tw.TaskbarWidget._draw)
    assert "extra_shown" in src
    # the slot is drawn when asked for, even with no value to put in it
    assert "if extra_shown or extra_pct is not None:" in src


# --- narrower layouts for taskbars without room (laptops) ---------------

SIDES = ({}, {"show_codex": False}, {"show_claude": False})


@pytest.mark.parametrize("sides", SIDES)
def test_each_size_is_narrower_than_the_one_before(sides):
    widths = [tw.layout_width(sides, s) for s in tw.SIZES]
    assert widths == sorted(widths, reverse=True)
    assert len(set(widths)) == len(widths)


def test_layout_width_matches_what_apply_layout_sets():
    for size in tw.SIZES:
        assert tw.apply_layout({}, size) == tw.layout_width({}, size)
        assert tw.SIZE == size


def test_sizes_follow_the_setting():
    assert tw.sizes_for({}) == tw.SIZES
    assert tw.sizes_for({"widget_size": "auto"}) == tw.SIZES
    assert tw.sizes_for({"widget_size": "slim"}) == ("slim",)
    assert tw.sizes_for({"widget_size": "huge"}) == tw.SIZES   # unknown -> auto


def _widest_snapshot():
    """Every value at its widest: 100% and a two-digit day countdown."""
    import time
    import parsers
    later = time.time() + 10 * 86400 + 60
    s = parsers.Snapshot(claude=parsers.ClaudeSnapshot(), codex=parsers.CodexSnapshot())
    c = s.claude
    c.available = True
    c.pct_5h = c.pct_7d = 100.0
    c.block_resets_at = c.week_resets_at = later
    c.fable_available = True
    c.fable_pct, c.fable_resets_at = 100.0, later
    x = s.codex
    x.available = x.has_primary = x.has_secondary = True
    x.primary_pct = x.secondary_pct = 100.0
    x.primary_resets_at = x.secondary_resets_at = later
    return s


def _fake_render_target(canvas, cfg, layered=False):
    import types
    w = types.SimpleNamespace(canvas=canvas, embedded=layered, cfg=cfg,
                              _calibrating=False, _layered=layered,
                              _icon_claude=None, _icon_codex=None, frames=[])
    w._adj = lambda color: color
    w._apply_colors = lambda: None
    w.float_is_transparent = lambda: False
    w._draw = lambda *a: tw.TaskbarWidget._draw(w, *a)
    w._icon_images_now = lambda: {}
    w._push_frame = w.frames.append
    return w


def _assert_apart(boxes, size):
    import itertools
    assert boxes
    for a, b in itertools.combinations(boxes, 2):
        apart_x = a[2] <= b[0] or b[2] <= a[0]
        apart_y = a[3] <= b[1] or b[3] <= a[1]
        assert apart_x or apart_y, (size, a, b)


@pytest.mark.parametrize("size", tw.SIZES)
@pytest.mark.parametrize("sides", SIDES)
def test_every_layout_keeps_its_text_apart_and_inside_the_bar(tk_root, size, sides):
    import tkinter as tk
    tw.init_scale(tk_root, {})
    cfg = dict(sides, widget_size=size)
    tw.apply_layout(cfg, size)
    canvas = tk.Canvas(tk_root, width=tw.WIDTH, height=tw.HEIGHT)
    try:
        w = _fake_render_target(canvas, cfg)
        tw.TaskbarWidget.render(w, _widest_snapshot(), cfg)
        x0, _y0, x1, _y1 = canvas.bbox("all")
        assert 0 <= x0 and x1 <= tw.WIDTH
        _assert_apart([canvas.bbox(i) for i in canvas.find_all()
                       if canvas.type(i) == "text"], size)
    finally:
        canvas.destroy()


@pytest.mark.parametrize("size", tw.SIZES)
@pytest.mark.parametrize("sides", SIDES)
def test_the_layered_frame_keeps_its_text_apart_and_inside_the_bar(tk_root, size, sides):
    """The embedded bar is drawn by PIL, whose text metrics are its own."""
    tw.init_scale(tk_root, {})
    cfg = dict(sides, widget_size=size)
    tw.apply_layout(cfg, size)
    w = _fake_render_target(None, cfg, layered=True)
    tw.TaskbarWidget.render(w, _widest_snapshot(), cfg)
    assert len(w.frames) == 1
    img = w.frames[0]
    assert img.size == (tw.WIDTH, tw.HEIGHT)
    # the same painter records every text box it drew
    boxes = []
    painter = tw._ImagePainter(tw.WIDTH, tw.HEIGHT)
    tw.TaskbarWidget._draw(w, painter, _widest_snapshot(), cfg, 50, 80)
    boxes = painter.texts
    assert all(0 <= b[0] and b[2] <= tw.WIDTH for b in boxes)
    _assert_apart(boxes, size)


# --- the layered frame --------------------------------------------------

def test_layered_colours_are_exact_and_the_background_lets_the_taskbar_through():
    p = tw._ImagePainter(40, 20)
    p.rect(0, 0, 10, 10, "#808080")
    assert p.img.getpixel((5, 5)) == (128, 128, 128, 255)
    # nearly invisible, yet not click-through: the bar stays draggable
    assert p.img.getpixel((30, 15)) == (0, 0, 0, 1)
    assert tw._clear_frame(4, 4).getpixel((1, 1)) == (0, 0, 0, 0)


def test_layered_text_edges_carry_alpha_not_a_baked_in_background():
    """Anti-aliased edges must fade by alpha. Blended into a background that
    is not there, they would show as a dark fringe on a light taskbar."""
    from PIL import Image
    p = tw._ImagePainter(80, 30)
    p.text(5, 15, "5h 100%", "#ffffff", tw.FONT_PCT)
    alpha_hist = p.img.getchannel("A").histogram()
    assert sum(alpha_hist[2:255]) > 0, "expected soft edges"
    light = Image.new("RGBA", p.img.size, (243, 243, 243, 255))
    shown = Image.alpha_composite(light, p.img).convert("RGB")
    # white ink on a light strip can only ever brighten it
    assert min(lo for lo, _hi in shown.getextrema()) >= 241


def test_the_frame_is_handed_over_premultiplied_bgra():
    from PIL import Image
    img = Image.new("RGBA", (1, 1), (200, 100, 50, 128))
    assert img.convert("RGBa").tobytes("raw", "BGRa") == bytes((25, 50, 100, 128))


def test_meterless_layouts_colour_the_number_instead():
    """The meter carried the warn/danger colour; without it the number must."""
    src = Path(tw.__file__).read_text(encoding="utf-8")
    assert "ink = FG_DIM if pct is None else color" in src


# --- choosing where and how wide --------------------------------------

def _fake_widget(space, cfg=None, embedded=True):
    import types
    w = types.SimpleNamespace(cfg=cfg or {}, embedded=embedded, _space=space)
    w._anchor = lambda tb: tw.TaskbarWidget._anchor(w, tb)
    w._default_right_offset = lambda tb: 0
    return w


def test_a_narrow_home_gets_a_narrower_layout():
    import taskbar_space as ts
    widths = [tw.layout_width({}, s) for s in tw.SIZES]
    margin, gap = tw.sc(tw.EMBED_MARGIN), tw.sc(tw.EMBED_GAP)
    tray = 3000
    room = widths[1] + margin + gap + 1          # compact fits, full does not
    space = ts.Space((0, 0, 3200, 72), tray,
                     ((100, tray - room), (tray, 3200)))
    size, x = tw.TaskbarWidget._choose(_fake_widget(space), (0, 0, 3200, 72))
    assert size == "compact"
    assert x + widths[1] == tray - gap


def test_a_pinned_size_is_kept_even_when_room_is_short():
    import taskbar_space as ts
    space = ts.Space((0, 0, 3200, 72), 3000, ((100, 2900), (3000, 3200)))
    size, _x = tw.TaskbarWidget._choose(
        _fake_widget(space, {"widget_size": "full"}), (0, 0, 3200, 72))
    assert size == "full"


def test_without_a_map_the_bar_keeps_the_old_spot_beside_the_clock():
    size, x = tw.TaskbarWidget._choose(_fake_widget(None), (0, 0, 3200, 72))
    assert size == "full"
    assert x == 3200 - tw.layout_width({}, "full")


def test_a_dragged_bar_is_anchored_by_the_edge_it_was_dropped_at():
    w = _fake_widget(None, {"widget_taskbar_offset": {"right": 500}})
    assert tw.TaskbarWidget._anchor(w, (0, 0, 3200, 72)) == (2700, True)
    w = _fake_widget(None, {"widget_position": {"x": 40, "y": 5}}, embedded=False)
    assert tw.TaskbarWidget._anchor(w, (0, 0, 3200, 72)) == (40, False)
    assert tw.TaskbarWidget._anchor(_fake_widget(None), (0, 0, 3200, 72)) is None


def test_covering_a_button_is_detected():
    import taskbar_space as ts
    w = _fake_widget(ts.Space((0, 0, 3200, 72), 3000, ((100, 900), (3000, 3200))))
    assert tw.TaskbarWidget._covers_something(w, (850, 0, 1200, 72))
    assert not tw.TaskbarWidget._covers_something(w, (900, 0, 1200, 72))


def test_a_click_does_not_pin_the_bar():
    """A click (or the double-click that opens the dashboard) used to save
    the spot, which silently switched off the automatic placement."""
    import types
    saved = []
    w = types.SimpleNamespace(_dragging=True, _drag_moved=False, embedded=True,
                              _pending_right=5,
                              save_taskbar_offset=lambda r: saved.append(r))
    w.redraw_last = lambda: None
    w._refit = lambda: None
    tw.TaskbarWidget._on_release(w, None)
    assert saved == [] and w._dragging is False
    w._drag_moved = True
    tw.TaskbarWidget._on_release(w, None)
    assert saved == [5]
