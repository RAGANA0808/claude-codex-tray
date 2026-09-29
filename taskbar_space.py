"""Where on the taskbar the bar can sit without covering anything.

Windows 11 draws the taskbar buttons in XAML, so they are not windows and
GetWindowRect cannot see them. UI Automation can: every button reports its
rectangle, and the stretches between them are the room the bar has. On a
laptop that room is far smaller than the full layout, so the widget picks a
narrower layout (or another stretch) instead of covering app icons.

The query costs ~50 ms inside explorer, so it runs on its own thread and only
when something cheap to watch (the set of app windows, the taskbar and tray
rectangles) says the buttons may have moved, plus a slow periodic check.
"""
from __future__ import annotations

import ctypes
import threading
import time
from ctypes import wintypes
from dataclasses import dataclass

TASKBAR_CLASS = "Shell_TrayWnd"
TRAY_CLASS = "TrayNotifyWnd"
XAML_CLASS = "Windows.UI.Composition.DesktopWindowContentBridge"

_CLSID_CUIAutomation = "{ff48dba4-60ef-4201-aa87-54103eef594e}"
_IID_IUIAutomation = "{30cbe57d-d9d0-452a-ab13-7ac5ac4825ee}"
_TREESCOPE_DESCENDANTS = 4

# vtable slots, from UIAutomationClient.h (published COM interfaces never
# reorder their methods).
_UIA_ELEMENT_FROM_HANDLE = 6
_UIA_CREATE_TRUE_CONDITION = 21
_EL_FIND_ALL = 6
_EL_CONTROL_TYPE = 21
_EL_BOUNDING_RECT = 43
_ARR_LENGTH = 3
_ARR_GET_ELEMENT = 4

# Control types that only group other elements. Their rectangles span empty
# stretches too (the Windows 10 task list toolbar covers the whole free area),
# so only what they contain counts as occupied.
_CONTAINER_TYPES = {
    50008,  # List
    50009,  # Menu
    50010,  # MenuBar
    50017,  # StatusBar
    50018,  # Tab
    50021,  # ToolBar
    50023,  # Tree
    50026,  # Group
    50028,  # DataGrid
    50030,  # Document
    50032,  # Window
    50033,  # Pane
    50036,  # Table
    50037,  # TitleBar
    50039,  # SemanticZoom
    50040,  # AppBar
}

WATCH_SECONDS = 0.5        # how often the cheap signature is compared
SETTLE_SECONDS = (0.6, 2.0)  # re-measure after a change, once the buttons settle
PERIODIC_SECONDS = 30.0    # catch changes the signature cannot see (pins, tray)
MIN_GAP_SECONDS = 1.0      # never measure more often than this


@dataclass(frozen=True)
class Space:
    taskbar: tuple[int, int, int, int]      # screen rect of Shell_TrayWnd
    tray_left: int                          # screen x where the clock corner starts
    blocks: tuple[tuple[int, int], ...]     # occupied stretches, screen x, merged

    def runs(self) -> list[tuple[int, int]]:
        return free_runs((self.taskbar[0], self.taskbar[2]), self.blocks)

    def home(self) -> tuple[int, int] | None:
        """The free stretch that ends at the clock corner — the bar's usual spot.

        None when app buttons run right up to the clock: there is no home
        then, and a stretch further left must not be mistaken for one.
        """
        # A tray icon can appear a moment before the corner's window grows to
        # hold it, so the stretch may end up to two icons short of tray_left.
        # Buttons that reach the corner leave it ending far further left.
        slack = 2 * (self.taskbar[3] - self.taskbar[1])
        for run in reversed(self.runs()):
            if run[1] > self.tray_left + 2:
                continue
            return run if self.tray_left - run[1] <= slack else None
        return None


# --- pure geometry ---------------------------------------------------------

def merge(blocks) -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    for lo, hi in sorted(blocks):
        if out and lo <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], hi))
        else:
            out.append((lo, hi))
    return out


def free_runs(span: tuple[int, int], blocks) -> list[tuple[int, int]]:
    """The parts of `span` that no block covers, left to right."""
    lo, hi = span
    runs = []
    x = lo
    for b_lo, b_hi in merge(blocks):
        if b_hi <= x:
            continue
        if b_lo >= hi:
            break
        if b_lo > x:
            runs.append((x, b_lo))
        x = max(x, b_hi)
    if x < hi:
        runs.append((x, hi))
    return runs


def fit_home(space: Space, widths: list[int], margin: int,
             tray_gap: int) -> tuple[int, int]:
    """Choose (layout index, left x) with no user-chosen position.

    `widths` runs richest first. The bar stays beside the clock and narrows
    as far as it has to; only when even the narrowest layout cannot fit there
    does it move to another free stretch, and only when nothing fits anywhere
    does it accept covering something, in the widest stretch there is.
    """
    home = space.home()
    if home is not None:
        room = (home[1] - tray_gap) - (home[0] + margin)
        for i, w in enumerate(widths):
            if w <= room:
                return i, home[1] - tray_gap - w
    others = [r for r in space.runs() if r != home]
    for i, w in enumerate(widths):
        for lo, hi in sorted(others, key=lambda r: -r[1]):
            if w <= (hi - margin) - (lo + margin):
                return i, lo + margin
    last = len(widths) - 1
    runs = space.runs()
    if not runs:
        return last, space.taskbar[2] - tray_gap - widths[last]
    lo, hi = max(runs, key=lambda r: r[1] - r[0])
    if home is not None and (lo, hi) == home:
        return last, max(lo, hi - tray_gap - widths[last])
    return last, lo + min(margin, max(0, (hi - lo) - widths[last]))


def fit_anchored(space: Space, widths: list[int], margin: int,
                 edge: int, grows_left: bool) -> int:
    """Richest layout that fits beside an edge the user dragged the bar to.

    grows_left: the bar's right edge is pinned at `edge` (embedded bars keep
    their distance from the clock); otherwise its left edge is.
    """
    room = 0
    for lo, hi in space.runs():
        if grows_left and lo < edge <= hi:
            room = edge - (lo + margin)
        elif not grows_left and lo <= edge < hi:
            room = (hi - margin) - edge
    for i, w in enumerate(widths):
        if w <= room:
            return i
    return len(widths) - 1


# --- Win32 / UI Automation ---------------------------------------------------

def _user32():
    return ctypes.windll.user32


def _rect(hwnd) -> tuple[int, int, int, int] | None:
    if not hwnd:
        return None
    r = wintypes.RECT()
    if not _user32().GetWindowRect(ctypes.c_void_p(hwnd), ctypes.byref(r)):
        return None
    return r.left, r.top, r.right, r.bottom


def _child(parent, cls: str) -> int:
    u = _user32()
    u.FindWindowExW.restype = ctypes.c_void_p
    return u.FindWindowExW(ctypes.c_void_p(parent), None, cls, None) or 0


class _GUID(ctypes.Structure):
    _fields_ = [("d1", ctypes.c_uint32), ("d2", ctypes.c_uint16),
                ("d3", ctypes.c_uint16), ("d4", ctypes.c_ubyte * 8)]


def _guid(text: str) -> _GUID:
    g = _GUID()
    ctypes.windll.ole32.CLSIDFromString(ctypes.c_wchar_p(text), ctypes.byref(g))
    return g


def _vcall(obj, index: int, argtypes, *args):
    vtbl = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    fn = ctypes.WINFUNCTYPE(ctypes.HRESULT, ctypes.c_void_p, *argtypes)(vtbl[index])
    return fn(obj, *args)


def _release(obj) -> None:
    if obj:
        vtbl = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
        ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)(vtbl[2])(obj)


class _Automation:
    """The two calls this module needs from IUIAutomation, over raw vtables so
    the exe does not have to ship comtypes."""

    def __init__(self):
        self.ptr = ctypes.c_void_p()
        hr = ctypes.windll.ole32.CoCreateInstance(
            ctypes.byref(_guid(_CLSID_CUIAutomation)), None, 1,
            ctypes.byref(_guid(_IID_IUIAutomation)), ctypes.byref(self.ptr))
        if hr != 0 or not self.ptr:
            raise OSError(f"CoCreateInstance(CUIAutomation) failed: {hr & 0xFFFFFFFF:#x}")
        self.cond = ctypes.c_void_p()
        _vcall(self.ptr, _UIA_CREATE_TRUE_CONDITION,
               [ctypes.POINTER(ctypes.c_void_p)], ctypes.byref(self.cond))

    def elements(self, hwnd: int) -> list[tuple[int, tuple[int, int, int, int]]]:
        """(control type, screen rect) of every element under `hwnd`."""
        root = ctypes.c_void_p()
        arr = ctypes.c_void_p()
        out = []
        try:
            _vcall(self.ptr, _UIA_ELEMENT_FROM_HANDLE,
                   [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)],
                   ctypes.c_void_p(hwnd), ctypes.byref(root))
            _vcall(root, _EL_FIND_ALL,
                   [ctypes.c_int, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)],
                   _TREESCOPE_DESCENDANTS, self.cond, ctypes.byref(arr))
            n = ctypes.c_int()
            _vcall(arr, _ARR_LENGTH, [ctypes.POINTER(ctypes.c_int)], ctypes.byref(n))
            for i in range(n.value):
                el = ctypes.c_void_p()
                try:
                    _vcall(arr, _ARR_GET_ELEMENT,
                           [ctypes.c_int, ctypes.POINTER(ctypes.c_void_p)],
                           i, ctypes.byref(el))
                    ct = ctypes.c_int()
                    _vcall(el, _EL_CONTROL_TYPE, [ctypes.POINTER(ctypes.c_int)],
                           ctypes.byref(ct))
                    r = wintypes.RECT()
                    _vcall(el, _EL_BOUNDING_RECT, [ctypes.POINTER(wintypes.RECT)],
                           ctypes.byref(r))
                    out.append((ct.value, (r.left, r.top, r.right, r.bottom)))
                except OSError:
                    continue      # an element vanished mid-walk
                finally:
                    _release(el)
        finally:
            _release(arr)
            _release(root)
        return out


def measure(uia: _Automation) -> Space | None:
    tb = _user32().FindWindowW(TASKBAR_CLASS, None) or 0
    tb_rect = _rect(tb)
    if not tb_rect:
        return None
    tb_l, tb_t, tb_r, tb_b = tb_rect
    if (tb_r - tb_l) <= (tb_b - tb_t):
        return None       # a vertical taskbar has no horizontal room to share
    tray = _rect(_child(tb, TRAY_CLASS))
    # On Windows 11 every button lives in the XAML island; querying just that
    # also keeps the walk away from our own window when it is embedded.
    root = _child(tb, XAML_CLASS) or tb
    blocks = []
    too_wide = 0.4 * (tb_r - tb_l)
    for ctype, (left, top, right, bottom) in uia.elements(root):
        if ctype in _CONTAINER_TYPES or right <= left or bottom <= top:
            continue
        if bottom <= tb_t or top >= tb_b or right <= tb_l or left >= tb_r:
            continue
        if (right - left) >= too_wide:
            continue
        blocks.append((max(left, tb_l), min(right, tb_r)))
    if tray:
        blocks.append((max(tray[0], tb_l), min(tray[2], tb_r)))
        tray_left = tray[0]
    else:
        tray_left = tb_r
    return Space(taskbar=tb_rect, tray_left=tray_left, blocks=tuple(merge(blocks)))


def _signature():
    """Cheap fingerprint of everything that moves the taskbar buttons."""
    u = _user32()
    tb = u.FindWindowW(TASKBAR_CLASS, None) or 0
    wins = []
    GW_OWNER = 4
    WS_EX_TOOLWINDOW = 0x80
    proto = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

    def visit(h, _lp):
        if (u.IsWindowVisible(ctypes.c_void_p(h))
                and not u.GetWindow(ctypes.c_void_p(h), GW_OWNER)
                and not (u.GetWindowLongW(ctypes.c_void_p(h), -20) & WS_EX_TOOLWINDOW)):
            wins.append(h)
        return True

    try:
        u.EnumWindows(proto(visit), 0)
    except Exception:
        pass
    return (_rect(tb), _rect(_child(tb, TRAY_CLASS)) if tb else None,
            frozenset(wins))


class _Watcher(threading.Thread):
    def __init__(self):
        super().__init__(name="taskbar-space", daemon=True)
        self.latest: Space | None = None
        self.seq = 0
        self.available = True
        self.first = threading.Event()
        self._wake = threading.Event()

    def request(self) -> None:
        self._wake.set()

    def run(self):
        try:
            ctypes.windll.ole32.CoInitializeEx(None, 0)   # COINIT_MULTITHREADED
            uia = _Automation()
        except Exception as e:
            print(f"[space] UI Automation unavailable: {e}")
            self.available = False
            self.first.set()
            return
        last_sig = None
        due: list[float] = [0.0]
        next_periodic = 0.0
        last_measured = float("-inf")
        while True:
            now = time.monotonic()
            try:
                sig = _signature()
            except Exception:
                sig = last_sig
            if sig != last_sig:
                if last_sig is not None:
                    # Look soon, and again once the buttons stop sliding. An
                    # app that keeps opening windows only pushes the second
                    # look later; MIN_GAP_SECONDS caps the cost either way.
                    if not due:
                        due.append(now + SETTLE_SECONDS[0])
                    due.append(now + SETTLE_SECONDS[1])
                last_sig = sig
            if self._wake.is_set():
                self._wake.clear()
                due.insert(0, now)
            if (((due and now >= due[0]) or now >= next_periodic)
                    and now - last_measured >= MIN_GAP_SECONDS):
                due = [t for t in due if t > now]
                next_periodic = now + PERIODIC_SECONDS
                last_measured = now
                try:
                    got = measure(uia)
                except Exception as e:
                    print(f"[space] measure failed: {e}")
                    got = None
                if got != self.latest:
                    self.latest = got
                    self.seq += 1
                self.first.set()
            self._wake.wait(WATCH_SECONDS)


_watcher: _Watcher | None = None
_watcher_lock = threading.Lock()


def watcher() -> _Watcher:
    """The process-wide watcher, started on first use."""
    global _watcher
    with _watcher_lock:
        if _watcher is None:
            _watcher = _Watcher()
            _watcher.start()
        return _watcher
