"""Finding room on the taskbar, and choosing the layout that fits it."""
import types

import pytest

import taskbar_space as ts

# A 3840px taskbar shaped like a real Windows 11 one: centred app buttons,
# the clock corner on the right.
TASKBAR = (0, 2088, 3840, 2160)
ICONS = (1490, 2350)
TRAY = (3419, 3840)
WIDTHS = [663, 544, 461, 325]          # full, compact, slim, mini at 150%
MARGIN, TRAY_GAP = 12, 24


def space(*blocks, tray_left=TRAY[0]):
    return ts.Space(TASKBAR, tray_left, tuple(ts.merge(blocks)))


# --- free stretches ----------------------------------------------------

def test_free_runs_are_the_gaps_between_blocks():
    assert ts.free_runs((0, 100), [(10, 20), (50, 60)]) == [(0, 10), (20, 50), (60, 100)]


def test_overlapping_and_touching_blocks_merge():
    assert ts.merge([(50, 60), (10, 30), (25, 40), (40, 45)]) == [(10, 45), (50, 60)]


def test_blocks_at_the_edges_leave_no_empty_runs():
    assert ts.free_runs((0, 100), [(0, 10), (90, 100)]) == [(10, 90)]
    assert ts.free_runs((0, 100), [(-5, 200)]) == []


def test_home_is_the_stretch_that_ends_at_the_clock():
    assert space(ICONS, TRAY).home() == (2350, 3419)


def test_no_home_when_buttons_run_into_the_clock():
    """The stretch left of the centred buttons must not pose as home."""
    assert space((1490, 3419), TRAY).home() is None


def test_home_without_a_tray_is_the_rightmost_stretch():
    sp = ts.Space(TASKBAR, TASKBAR[2], tuple(ts.merge([ICONS])))
    assert sp.home() == (2350, 3840)


# --- choosing a layout with no user-chosen spot ------------------------

def test_the_full_bar_sits_beside_the_clock_when_it_fits():
    i, x = ts.fit_home(space(ICONS, TRAY), WIDTHS, MARGIN, TRAY_GAP)
    assert i == 0
    assert x + WIDTHS[0] == TRAY[0] - TRAY_GAP


@pytest.mark.parametrize("icons_end, expected", [
    (2700, 0),      # 719 free: the full bar still fits
    (2800, 1),      # 619: compact
    (2900, 2),      # 519: slim
    (3020, 3),      # 399: mini
])
def test_the_bar_narrows_as_the_buttons_take_the_room(icons_end, expected):
    """What a laptop needs: less room beside the clock, a narrower bar."""
    i, x = ts.fit_home(space((1490, icons_end), TRAY), WIDTHS, MARGIN, TRAY_GAP)
    assert i == expected
    assert x >= icons_end + MARGIN                   # clear of the buttons
    assert x + WIDTHS[i] <= TRAY[0] - TRAY_GAP       # clear of the clock


def test_with_no_room_beside_the_clock_the_bar_moves_to_another_stretch():
    i, x = ts.fit_home(space((1300, 3200), TRAY), WIDTHS, MARGIN, TRAY_GAP)
    assert (i, x) == (0, MARGIN)                     # left of the buttons, full size
    assert x + WIDTHS[0] <= 1300


def test_the_other_stretch_gets_the_richest_layout_it_can_hold():
    i, x = ts.fit_home(space((600, 3300), TRAY), WIDTHS, MARGIN, TRAY_GAP)
    assert i == 1 and x + WIDTHS[1] <= 600


def test_when_nothing_fits_anywhere_the_smallest_goes_in_the_widest_gap():
    i, x = ts.fit_home(space((100, 3300), TRAY), WIDTHS, MARGIN, TRAY_GAP)
    assert i == len(WIDTHS) - 1
    assert 0 <= x < 3840


def test_a_pinned_layout_is_placed_but_not_resized():
    i, x = ts.fit_home(space((1490, 3020), TRAY), [WIDTHS[0]], MARGIN, TRAY_GAP)
    assert i == 0     # only one layout allowed; it moves left of the buttons
    assert x + WIDTHS[0] <= 1490


# --- a spot the user dragged the bar to --------------------------------

def test_a_dragged_bar_keeps_its_right_edge_and_narrows_to_the_room():
    sp = space(ICONS, TRAY)
    assert ts.fit_anchored(sp, WIDTHS, MARGIN, 3000, grows_left=True) == 1   # 638 free
    assert ts.fit_anchored(sp, WIDTHS, MARGIN, 3395, grows_left=True) == 0


def test_a_floating_bar_keeps_its_left_edge():
    sp = space(ICONS, TRAY)
    assert ts.fit_anchored(sp, WIDTHS, MARGIN, 2900, grows_left=False) == 2   # 507 free


def test_a_bar_dropped_on_the_buttons_takes_the_smallest_layout():
    sp = space(ICONS, TRAY)
    assert ts.fit_anchored(sp, WIDTHS, MARGIN, 2000, grows_left=True) == 3


# --- reading the taskbar ----------------------------------------------

class FakeUser32:
    @staticmethod
    def FindWindowW(cls, name):
        return 1


def fake_measure_env(monkeypatch, elements, tray=(3420, 2088, 3840, 2160)):
    rects = {1: TASKBAR, 2: tray, 3: TASKBAR}
    monkeypatch.setattr(ts, "_user32", lambda: FakeUser32)
    monkeypatch.setattr(ts, "_rect", lambda h: rects.get(h))
    monkeypatch.setattr(ts, "_child", lambda parent, cls: {
        ts.TRAY_CLASS: 2 if tray else 0, ts.XAML_CLASS: 3}[cls])
    return types.SimpleNamespace(elements=lambda hwnd: elements)


def test_measure_counts_buttons_but_not_the_panes_around_them(monkeypatch):
    button, pane, image = 50000, 50033, 50006
    uia = fake_measure_env(monkeypatch, [
        (pane, (0, 2088, 3840, 2160)),        # the XAML frame itself
        (pane, (1557, 2088, 2217, 2160)),     # a stale legacy task list
        (button, (1490, 2088, 1558, 2160)),   # Start
        (button, (1558, 2088, 2350, 2160)),
        (image, (3479, 2112, 3503, 2136)),    # icon inside a tray button
        (button, (0, 0, 60, 40)),             # another monitor's taskbar
        (button, (500, 2088, 500, 2160)),     # zero width
    ])
    sp = ts.measure(uia)
    assert sp.blocks == ((1490, 2350), (3420, 3840))
    assert sp.home() == (2350, 3420)


def test_measure_ignores_anything_wider_than_a_real_button(monkeypatch):
    uia = fake_measure_env(monkeypatch, [(50000, (0, 2088, 2560, 2160))])
    assert ts.measure(uia).blocks == ((3420, 3840),)


def test_measure_gives_up_on_a_vertical_taskbar(monkeypatch):
    fake_measure_env(monkeypatch, [])
    monkeypatch.setattr(ts, "_rect", lambda h: (0, 0, 48, 1080))
    assert ts.measure(types.SimpleNamespace(elements=lambda h: [])) is None


def test_the_real_taskbar_can_be_read_or_is_reported_missing():
    """On a desktop this reads the live taskbar; on a CI runner without one
    it must say so instead of raising. Runs on its own thread, as the
    watcher does, so COM starts clean."""
    import ctypes
    import threading
    result = {}

    def work():
        ctypes.windll.ole32.CoInitializeEx(None, 0)
        try:
            result["space"] = ts.measure(ts._Automation())
        except OSError as e:
            result["unavailable"] = e

    t = threading.Thread(target=work)
    t.start()
    t.join(30)
    if "unavailable" in result:
        pytest.skip(f"UI Automation unavailable here: {result['unavailable']}")
    got = result["space"]
    if got is not None:
        assert got.taskbar[2] > got.taskbar[0]
        assert all(lo < hi for lo, hi in got.blocks)
        assert got.blocks == tuple(ts.merge(got.blocks))
