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
