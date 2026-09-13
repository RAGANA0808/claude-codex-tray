"""Widget geometry: column sizing and which sides are drawn."""
import pytest

import taskbar_widget as tw


@pytest.fixture(autouse=True)
def restore_globals():
    keep = {k: getattr(tw, k) for k in
            ("SCALE", "WIDTH", "HEIGHT", "ICON_PX", "PAD_X", "GAP_ICON", "GAP",
             "BAR_W", "BAR_H", "ROW_Y_TOP", "INTER_GROUP_GAP",
             "CAP_W", "PCT_W", "TIME_W", "GROUP_W")}
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
    tw.apply_layout({})
    assert tw.GROUP_W == tw.CAP_W + tw.GAP + tw.BAR_W + tw.GAP + tw.PCT_W + tw.GAP + tw.TIME_W


# --- the overflow this file exists for --------------------------------

def test_percentage_column_fits_three_digits():
    """"100%" used to print straight over the reset countdown beside it."""
    tk = pytest.importorskip("tkinter")
    try:
        root = tk.Tk()
    except Exception:
        pytest.skip("no display for Tk")
    try:
        root.withdraw()
        tw.init_scale(root, {})
        import tkinter.font as tkfont
        f = tkfont.Font(root=root, family=tw.FONT_PCT[0], size=tw.FONT_PCT[1])
        assert tw.PCT_W >= f.measure("100%")
        t = tkfont.Font(root=root, family=tw.FONT_TIME[0], size=tw.FONT_TIME[1])
        assert tw.TIME_W >= max(t.measure(s) for s in ("<1m", "now", "10d", "23h"))
    finally:
        root.destroy()


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


def test_probe_draws_a_patch_and_leaves_the_background_alone():
    """A probe that recoloured the whole canvas burned a grey block into the
    taskbar whenever calibration was interrupted."""
    tk = pytest.importorskip("tkinter")
    import types
    try:
        root = tk.Tk()
    except Exception:
        pytest.skip("no display for Tk")
    try:
        root.withdraw()
        tw.init_scale(root, {})
        canvas = tk.Canvas(root, width=tw.WIDTH, height=tw.HEIGHT)
        w = types.SimpleNamespace(canvas=canvas, win=root, embedded=True, cfg={},
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
        root.destroy()


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
