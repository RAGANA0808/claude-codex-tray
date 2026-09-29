"""Frameless always-on-top "taskbar widget" — sits at the bottom of the screen so
it visually lives on the Windows 11 taskbar strip.

Layout at full size (narrower sizes drop the meter, then the countdown — see
SIZES; which one is used depends on the free room, see taskbar_space):

    | [Claude] 5h ████░░ 6%  3h  F ██░░ 34% 3d | [Codex] 5h ███░░░ 15% 58m |
    |          7d ██░░░░ 19% 4d                |         7d ███░░░ 39% 5d  |
"""
from __future__ import annotations
import ctypes
import tkinter as tk

import paths
import taskbar_space
from typing import Callable


_GWL_EXSTYLE = -20
_GWL_STYLE = -16
_WS_EX_NOACTIVATE = 0x08000000
_WS_EX_TOOLWINDOW = 0x00000080
_WS_EX_TOPMOST = 0x00000008
_WS_EX_LAYERED = 0x00080000
_WS_CHILD = 0x40000000
_WS_POPUP = 0x80000000
_HWND_TOP = 0
_HWND_TOPMOST = -1
_SWP_NOSIZE = 0x0001
_SWP_NOMOVE = 0x0002
_SWP_NOACTIVATE = 0x0010
_SWP_SHOWWINDOW = 0x0040
_SW_HIDE = 0
_SW_SHOWNOACTIVATE = 4

# Windows 11 removed Deskbands, so there is no supported API for putting a
# custom bar inside the taskbar. Reparenting our HWND into Shell_TrayWnd is
# the working alternative: the child paints above the XAML island as long as
# we keep it at the top of the parent's Z-order.
TASKBAR_CLASS = "Shell_TrayWnd"
TRAY_NOTIFY_CLASS = "TrayNotifyWnd"
EMBED_GAP = 16          # px (at 100%) kept clear between the widget and the clock
FLOAT_RAISE_MS = 250    # how often a floating bar re-claims the top slot

# The Windows 11 taskbar composites an embedded child window ADDITIVELY over
# its own backdrop: every pixel we draw comes out brighter by the backdrop's
# colour. The backdrop differs per machine (theme, transparency effects,
# wallpaper), so the widget CALIBRATES at runtime: it paints two known
# colours, samples what actually reached the screen, and derives the offset
# to pre-subtract (see TaskbarWidget._calibrate). #111111 is only the
# initial guess (dark theme, transparency off). If the probe shows our
# pixels never reach the screen, or the backdrop is too bright for the
# additive model to stay legible, the widget falls back to float mode.
_CALIBRATION_STATE = None  # set to a Path by _calibration_state_path()

DARK_PALETTE = {
    "taskbar_bg": "#111111", "bg_bar": "#3a3a42",
    "fg_label": "#e8e8ee", "fg_dim": "#9a9aa6",
    "divider": "#33333a", "tile_fill": "#2a2a32", "tile_edge": "#454552",
    "ok": "#3fbf6a", "warn": "#e8a828", "danger": "#dc3c3c",
    "na": "#5a5a64", "fable": "#9b7cd6",   # violet, matches CodeZeno PR #49
}
# Light Windows 11 taskbars sit at roughly #f3f3f3; the accents are darkened
# so they stay readable against it.
LIGHT_PALETTE = {
    "taskbar_bg": "#f3f3f3", "bg_bar": "#cfcfd6",
    "fg_label": "#1b1b1f", "fg_dim": "#5c5c66",
    "divider": "#c2c2ca", "tile_fill": "#e4e4e8", "tile_edge": "#b4b4bc",
    "ok": "#1a7f43", "warn": "#8f5a00", "danger": "#b32222",
    "na": "#9a9aa2", "fable": "#5f43a6",
}

THEME = "dark"
TASKBAR_BG = DARK_PALETTE["taskbar_bg"]
BG = TASKBAR_BG
BG_BAR = DARK_PALETTE["bg_bar"]
FG_LABEL = DARK_PALETTE["fg_label"]
FG_DIM = DARK_PALETTE["fg_dim"]
DIVIDER = DARK_PALETTE["divider"]
TILE_FILL = DARK_PALETTE["tile_fill"]
TILE_EDGE = DARK_PALETTE["tile_edge"]
COLOR_OK = DARK_PALETTE["ok"]
COLOR_WARN = DARK_PALETTE["warn"]
COLOR_DANGER = DARK_PALETTE["danger"]
COLOR_NA = DARK_PALETTE["na"]
COLOR_FABLE = DARK_PALETTE["fable"]

# Geometry is authored at 100% DPI and scaled up at startup. Embedding into
# the taskbar makes this mandatory: a child of the (DPI-aware) taskbar gets
# no DWM scaling of its own, so an unscaled bar renders at 2/3 size on a
# 150% display.
BASE_HEIGHT = 48
BASE_ICON_PX = 28
SCALE = 1.0
HEIGHT = BASE_HEIGHT

# Column widths are measured from the real font rather than guessed: the
# old fixed 22px percentage column fitted "87%" but not "100%", so a full
# meter printed straight over the reset countdown beside it.
PAD_X = 8
GAP_ICON = 6
GAP = 3
BAR_W = 36
BAR_H = 12
ROW_Y_TOP = 6
INTER_GROUP_GAP = 8
CAP_W = 12
PCT_W = 22
TIME_W = 20
CAP_GAP = 3
TIME_GAP = 3
SHOW_TIME = True
GROUP_W = CAP_W + CAP_GAP + BAR_W + GAP + PCT_W + TIME_GAP + TIME_W
WIDTH = 500

# A laptop taskbar has far less free room than the full bar needs, so there
# are narrower layouts, richest first. Each step drops a detail rather than
# squeezing text together: the meter shortens, then goes (the number takes
# over its warn/danger colour), then the reset countdown goes.
SIZES = ("full", "compact", "slim", "mini")
# px at 100%. cap_gap follows the caption: without a meter in between it has
# to be wider, or "5h" and "100%" read as one word.
_SIZE_SPEC = {
    "full":    dict(icon=28, gap_icon=6, pad=8, inter=8, cap_gap=3, bar=36,
                    gap=3, time_gap=3, time=True),
    "compact": dict(icon=24, gap_icon=5, pad=6, inter=6, cap_gap=2, bar=18,
                    gap=2, time_gap=4, time=True),
    "slim":    dict(icon=22, gap_icon=4, pad=6, inter=7, cap_gap=5, bar=0,
                    gap=2, time_gap=4, time=True),
    "mini":    dict(icon=22, gap_icon=4, pad=5, inter=7, cap_gap=5, bar=0,
                    gap=2, time_gap=0, time=False),
}
SIZE = "full"
EMBED_MARGIN = 8        # px (at 100%) kept clear of app buttons
GROW_DELAY_S = 3.0      # room must stay free this long before the bar widens

FONT_CAP = ("Segoe UI", 8)
FONT_PCT = ("Segoe UI Semibold", 9)
FONT_TIME = ("Segoe UI", 9)
FONT_LETTER = ("Segoe UI Semibold", 13)
FONT_BADGE = ("Segoe UI Semibold", 8)
_FONT_FILES = {"Segoe UI": "segoeui.ttf", "Segoe UI Semibold": "seguisb.ttf"}
_pil_fonts: dict = {}


def _pil_font(font: tuple[str, int]):
    """The PIL twin of a Tk (family, points) font, at the pixel size Tk uses."""
    key = (font, SCALE)
    if key not in _pil_fonts:
        from PIL import ImageFont
        px = max(1, int(round(font[1] * SCALE * 96 / 72)))
        try:
            _pil_fonts[key] = ImageFont.truetype(_FONT_FILES.get(font[0], "segoeui.ttf"), px)
        except OSError:
            try:
                _pil_fonts[key] = ImageFont.load_default(px)
            except TypeError:        # Pillow < 10.1 has no sized default
                _pil_fonts[key] = ImageFont.load_default()
    return _pil_fonts[key]


def _format_countdown(reset_epoch: float | int | None) -> str:
    """CodeZeno-style compact reset countdown: '3d' / '5h' / '45m' / 'now'."""
    if not reset_epoch:
        return ""
    import time as _t
    remaining = int(float(reset_epoch) - _t.time())
    if remaining <= 0:
        return "now"
    days = remaining // 86400
    hours = remaining // 3600
    mins = remaining // 60
    if days >= 1:
        return f"{days}d"
    if hours >= 1:
        return f"{hours}h"
    if mins >= 1:
        return f"{mins}m"
    return "<1m"

ICON_PX = BASE_ICON_PX


def system_uses_light_taskbar() -> bool:
    """Windows keeps the taskbar/system shade separate from the app shade."""
    try:
        import winreg
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize")
        with key:
            return bool(winreg.QueryValueEx(key, "SystemUsesLightTheme")[0])
    except Exception:
        return False


def apply_theme(cfg: dict) -> str:
    """Pick the palette for the current taskbar shade. `theme` in config.json
    forces "light"/"dark"; "auto" follows Windows."""
    global THEME, TASKBAR_BG, BG, BG_BAR, FG_LABEL, FG_DIM, DIVIDER
    global TILE_FILL, TILE_EDGE
    global COLOR_OK, COLOR_WARN, COLOR_DANGER, COLOR_NA, COLOR_FABLE
    want = str(cfg.get("theme", "auto")).lower()
    if want not in ("light", "dark"):
        want = "light" if system_uses_light_taskbar() else "dark"
    pal = LIGHT_PALETTE if want == "light" else DARK_PALETTE
    THEME = want
    raw_bg = str(cfg.get("taskbar_bg") or "auto")
    TASKBAR_BG = pal["taskbar_bg"] if raw_bg.lower() == "auto" else raw_bg
    BG = TASKBAR_BG
    BG_BAR, FG_LABEL, FG_DIM = pal["bg_bar"], pal["fg_label"], pal["fg_dim"]
    DIVIDER, TILE_FILL, TILE_EDGE = pal["divider"], pal["tile_fill"], pal["tile_edge"]
    COLOR_OK, COLOR_WARN = pal["ok"], pal["warn"]
    COLOR_DANGER, COLOR_NA, COLOR_FABLE = pal["danger"], pal["na"], pal["fable"]
    return THEME


def set_dpi_awareness() -> None:
    """Must run before the first Tk window exists."""
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)   # per-monitor v1
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def init_scale(root, cfg: dict | None = None) -> float:
    """Derive the pixel scale from the real DPI, then size every column from
    the font actually in use. Tk scales point-sized fonts by the same factor,
    so only pixel geometry is adjusted here."""
    global SCALE, HEIGHT, BAR_H, ROW_Y_TOP
    global CAP_W, PCT_W, TIME_W
    try:
        SCALE = max(1.0, float(root.winfo_fpixels("1i")) / 96.0)
    except Exception:
        SCALE = 1.0

    apply_theme(cfg or {})
    HEIGHT = sc(BASE_HEIGHT)
    BAR_H, ROW_Y_TOP = sc(12), sc(6)

    def widest(font, samples) -> int:
        # Measured with both renderers (Tk for a floating bar, PIL for an
        # embedded one); their hinting differs by a pixel or so.
        best = 0
        try:
            import tkinter.font as tkfont
            f = tkfont.Font(root=root, family=font[0], size=font[1])
            best = max(f.measure(s) for s in samples)
        except Exception:
            pass
        try:
            import math
            f = _pil_font(font)
            best = max(best, max(math.ceil(f.getlength(s)) for s in samples))
        except Exception:
            pass
        return best

    # Widest string each column ever has to hold.
    CAP_W = max(widest(FONT_CAP, ("5h", "7d", "F")), sc(12))
    PCT_W = max(widest(FONT_PCT, ("100%", "—")), sc(22))
    TIME_W = max(widest(FONT_TIME, ("<1m", "now", "10d", "23h", "59m")), sc(20))
    apply_layout(cfg or {})
    return SCALE


def sc(v: float) -> int:
    return int(round(v * SCALE))


def sizes_for(cfg: dict) -> tuple[str, ...]:
    """The layouts the bar may use, richest first. `widget_size` in
    config.json pins one; "auto" (default) lets the free room decide."""
    want = str(cfg.get("widget_size", "auto")).lower()
    return (want,) if want in _SIZE_SPEC else SIZES


def _geometry(size: str) -> dict:
    s = _SIZE_SPEC[size]
    g = {"ICON_PX": sc(s["icon"]), "GAP_ICON": sc(s["gap_icon"]),
         "PAD_X": sc(s["pad"]), "INTER_GROUP_GAP": sc(s["inter"]),
         "CAP_GAP": sc(s["cap_gap"]), "GAP": sc(s["gap"]), "BAR_W": sc(s["bar"]),
         "TIME_GAP": sc(s["time_gap"]), "SHOW_TIME": s["time"]}
    w = CAP_W + g["CAP_GAP"] + PCT_W
    if g["BAR_W"]:
        w += g["BAR_W"] + g["GAP"]
    if g["SHOW_TIME"]:
        w += g["TIME_GAP"] + TIME_W
    g["GROUP_W"] = w
    return g


def sides_shown(cfg: dict) -> tuple[bool, bool]:
    """(claude, codex) — never both off, that would leave an empty bar."""
    claude = bool(cfg.get("show_claude", True))
    codex = bool(cfg.get("show_codex", True))
    return (True, True) if not (claude or codex) else (claude, codex)


def _side_width(g: dict, with_extra: bool) -> int:
    w = g["ICON_PX"] + g["GAP_ICON"] + g["GROUP_W"]
    if with_extra:
        w += g["INTER_GROUP_GAP"] + g["GROUP_W"]
    return w


def _total_width(cfg: dict, g: dict) -> int:
    show_claude, show_codex = sides_shown(cfg)
    pad = g["PAD_X"]
    total = pad
    if show_claude:
        total += _side_width(g, True) + pad
    if show_claude and show_codex:
        total += pad
    if show_codex:
        total += _side_width(g, False) + pad
    return total


def side_width(with_extra: bool) -> int:
    """Claude carries a second group on its top row (the Fable meter), so the
    two sides are sized independently instead of splitting the bar in half."""
    return _side_width({"ICON_PX": ICON_PX, "GAP_ICON": GAP_ICON,
                        "GROUP_W": GROUP_W, "INTER_GROUP_GAP": INTER_GROUP_GAP},
                       with_extra)


def layout_width(cfg: dict, size: str) -> int:
    """How wide the bar would be in `size`, without switching to it."""
    return _total_width(cfg, _geometry(size))


def apply_layout(cfg: dict, size: str | None = None) -> int:
    """Switch the module geometry to `size` (default: keep the current one)
    and set WIDTH from the columns and from which sides are switched on."""
    global SIZE, WIDTH, ICON_PX, GAP_ICON, PAD_X, INTER_GROUP_GAP, GAP
    global CAP_GAP, BAR_W, TIME_GAP, SHOW_TIME, GROUP_W
    if size in _SIZE_SPEC:
        SIZE = size
    g = _geometry(SIZE)
    ICON_PX, GAP_ICON, PAD_X = g["ICON_PX"], g["GAP_ICON"], g["PAD_X"]
    INTER_GROUP_GAP, GAP, BAR_W = g["INTER_GROUP_GAP"], g["GAP"], g["BAR_W"]
    CAP_GAP, TIME_GAP = g["CAP_GAP"], g["TIME_GAP"]
    SHOW_TIME, GROUP_W = g["SHOW_TIME"], g["GROUP_W"]
    WIDTH = _total_width(cfg, g)
    return WIDTH

HERE = paths.exe_dir()


def _hex_to_rgb(h: str) -> tuple[int, int, int]:
    h = h.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def _rgb_to_hex(rgb: tuple[int, int, int]) -> str:
    return "#%02x%02x%02x" % rgb


def _pick_color(pct: float | None, warn: int, danger: int) -> str:
    if pct is None:
        return COLOR_NA
    if pct >= danger:
        return COLOR_DANGER
    if pct >= warn:
        return COLOR_WARN
    return COLOR_OK


def _i32(v: int) -> int:
    return ctypes.c_int32(v & 0xFFFFFFFF).value


def _sample_screen(x: int, y: int) -> tuple[int, int, int] | None:
    """Read the composited screen pixel at physical coords (GetPixel)."""
    try:
        u = ctypes.windll.user32
        g = ctypes.windll.gdi32
        hdc = u.GetDC(0)
        if not hdc:
            return None
        try:
            c = g.GetPixel(hdc, int(x), int(y))
        finally:
            u.ReleaseDC(0, hdc)
        if c in (-1, 0xFFFFFFFF):
            return None
        return c & 0xFF, (c >> 8) & 0xFF, (c >> 16) & 0xFF
    except Exception:
        return None


def session_locked() -> bool:
    """True while the workstation is locked.

    A locked session shows the Winlogon desktop, so a screen sample reads the
    lock screen rather than our window — calibrating then concludes nonsense.
    """
    try:
        u = ctypes.windll.user32
        DESKTOP_SWITCHDESKTOP = 0x0100
        h = u.OpenInputDesktop(0, False, DESKTOP_SWITCHDESKTOP)
        if not h:
            return True
        u.CloseDesktop(h)
        return False
    except Exception:
        return False


def _point_belongs_to(hwnd: int, x: int, y: int) -> bool:
    """True when the window under (x, y) is `hwnd` or one of its children."""
    try:
        u = ctypes.windll.user32

        class _PT(ctypes.Structure):
            _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]
        u.WindowFromPoint.restype = ctypes.c_void_p
        got = u.WindowFromPoint(_PT(int(x), int(y)))
        for _ in range(8):
            if not got:
                return False
            if int(got) == int(hwnd):
                return True
            got = u.GetParent(ctypes.c_void_p(got))
        return False
    except Exception:
        return False


def sample_taskbar_beside(rect: tuple[int, int, int, int] | None) -> str | None:
    """The taskbar colour just outside `rect`, as a hex string.

    A taskbar with transparency effects is tinted by the wallpaper, so it
    has no fixed colour to hard-code. Reading the strip next to the bar is
    the only way a solid background can match it.
    """
    if not rect:
        return None
    tb = _window_rect(_find_taskbar())
    if not tb:
        return None
    y = (rect[1] + rect[3]) // 2
    picks = []
    for x in (rect[0] - 10, rect[0] - 20, rect[2] + 10, rect[2] + 20):
        if not (tb[0] + 2 <= x <= tb[2] - 2):
            continue
        got = _sample_screen(x, y)
        if got:
            picks.append(got)
    if not picks:
        return None
    picks.sort(key=sum)
    return _rgb_to_hex(picks[len(picks) // 2])


def _classify_calibration(black: tuple | None, gray: tuple | None):
    """Decide what the two probe samples mean.

    black = screen pixel while we painted #000000 (→ the backdrop itself
    under the additive model); gray = pixel while we painted #808080.
    Returns (verdict, offset): verdict is 'ok' (additive confirmed),
    'approx' (something composits, use black as best-effort offset),
    'invisible' (our paint never reached the screen), 'too-bright'
    (backdrop so light the additive model can't render legible text),
    or 'unknown' (sampling failed — keep the configured guess).
    """
    if black is None or gray is None:
        return 'unknown', None
    delta = tuple(gy - bk for gy, bk in zip(gray, black))
    if max(abs(d) for d in delta) < 10:
        return 'invisible', None
    if max(black) > 96:
        return 'too-bright', None
    if all(abs(d - 0x80) <= 48 for d in delta):
        return 'ok', black
    return 'approx', black


def _calibration_state_path():
    global _CALIBRATION_STATE
    if _CALIBRATION_STATE is None:
        from pathlib import Path as _Path
        _CALIBRATION_STATE = (_Path.home() / '.claude' / 'cache'
                              / 'tray-embed-calibration.json')
    return _CALIBRATION_STATE


def _write_calibration_state(payload: dict) -> None:
    try:
        import json
        import time as _t
        p = _calibration_state_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        payload = dict(payload, at=_t.time())
        p.write_text(json.dumps(payload), encoding='utf-8')
    except Exception:
        pass


def _find_taskbar() -> int:
    try:
        return ctypes.windll.user32.FindWindowW(TASKBAR_CLASS, None) or 0
    except Exception:
        return 0


def _window_rect(hwnd: int) -> tuple[int, int, int, int] | None:
    if not hwnd:
        return None
    try:
        class _R(ctypes.Structure):
            _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                        ("right", ctypes.c_long), ("bottom", ctypes.c_long)]
        r = _R()
        if not ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(r)):
            return None
        return r.left, r.top, r.right, r.bottom
    except Exception:
        return None


def _tray_notify_width(hwnd_tb: int) -> int:
    """Width of the clock / notification corner, so we can park to its left."""
    try:
        h = ctypes.windll.user32.FindWindowExW(hwnd_tb, None,
                                               TRAY_NOTIFY_CLASS, None)
        r = _window_rect(h)
        return (r[2] - r[0]) if r else 0
    except Exception:
        return 0


def _get_taskbar_height() -> int:
    try:
        class RECT(ctypes.Structure):
            _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                        ("right", ctypes.c_long), ("bottom", ctypes.c_long)]

        class APPBARDATA(ctypes.Structure):
            _fields_ = [
                ("cbSize", ctypes.c_uint),
                ("hWnd", ctypes.c_void_p),
                ("uCallbackMessage", ctypes.c_uint),
                ("uEdge", ctypes.c_uint),
                ("rc", RECT),
                ("lParam", ctypes.c_long),
            ]

        ABM_GETTASKBARPOS = 0x00000005
        data = APPBARDATA()
        data.cbSize = ctypes.sizeof(APPBARDATA)
        if ctypes.windll.shell32.SHAppBarMessage(ABM_GETTASKBARPOS, ctypes.byref(data)):
            return max(0, data.rc.bottom - data.rc.top)
    except Exception:
        pass
    return 48


def _load_app_icon_image(name: str, px: int):
    """The icon for `name` as a `px`-square RGBA PIL image, or None.

    Looks for a user-supplied app-{name}-custom.png first (git-ignored, so
    each user can drop in whatever artwork they like), then falls back to the
    bundled neutral app-{name}.png. Returns None if neither exists or
    PIL/Pillow isn't available — the caller then draws a letter tile.
    """
    try:
        from PIL import Image  # type: ignore
    except Exception:
        return None
    p = paths.user_data_dir() / f"app-{name}-custom.png"
    if not p.exists():
        p = paths.resource_path(f"app-{name}-custom.png")
    if not p.exists():
        p = paths.resource_path(f"app-{name}.png")
    if not p.exists():
        return None
    try:
        img = Image.open(p).convert("RGBA")
        if img.size != (px, px):
            img = img.resize((px, px), Image.LANCZOS)
        return img
    except Exception as e:
        print(f"[widget] icon load fail ({name}): {e}")
        return None


def _load_app_icon(name: str, px: int) -> tk.PhotoImage | None:
    img = _load_app_icon_image(name, px)
    if img is None:
        return None
    try:
        from PIL import ImageTk  # type: ignore
        return ImageTk.PhotoImage(img)
    except Exception as e:
        print(f"[widget] icon load fail ({name}): {e}")
        return None


# --- two ways to draw the same bar --------------------------------------
#
# A floating bar is an ordinary window and Tk draws it. Embedded, a plain
# child of the taskbar is blended ADDITIVELY into the strip (see
# _classify_calibration): nothing it draws can be darker than the taskbar,
# so a light taskbar cannot show dark text at all, and the bar needs an
# offset calibration and ghost clean-up even on a dark one. A LAYERED child
# (UpdateLayeredWindow with per-pixel alpha) is composited "over" instead:
# its colours land exactly and a transparent background shows the real
# taskbar through. That needs the whole frame as an image, hence a second
# painter with the same primitives.

class _CanvasPainter:
    """Draws with Tk canvas items."""

    def __init__(self, widget):
        self.c = widget.canvas
        self.adj = widget._adj
        self.icons = {"claude": widget._icon_claude, "codex": widget._icon_codex}
        self.c.delete("all")

    def rect(self, x0, y0, x1, y1, fill, outline=None):
        self.c.create_rectangle(x0, y0, x1, y1, fill=self.adj(fill),
                                outline=self.adj(outline) if outline else "")

    def text(self, x, y, s, fill, font, anchor="w"):
        self.c.create_text(x, y, text=s, fill=self.adj(fill), font=font,
                           anchor=anchor)

    def oval(self, x0, y0, x1, y1, fill, outline, width):
        self.c.create_oval(x0, y0, x1, y1, fill=self.adj(fill),
                           outline=self.adj(outline), width=width)

    def vline(self, x, y0, y1, fill):
        self.c.create_line(x, y0, x, y1, fill=self.adj(fill))

    def icon(self, name, x0, y0) -> bool:
        img = self.icons.get(name)
        if img is None:
            return False
        self.c.create_image(x0 + ICON_PX / 2, y0 + ICON_PX / 2, image=img)
        return True


class _ImagePainter:
    """Draws into an RGBA image for a layered window.

    Every shape is composited "over" through its own coverage mask, so the
    anti-aliased edges carry true alpha instead of being pre-blended with a
    background colour that is not there.
    """

    def __init__(self, width: int, height: int, icons: dict | None = None):
        from PIL import Image, ImageDraw
        self._Image, self._Draw = Image, ImageDraw
        # Alpha 1, not 0: a fully transparent pixel passes clicks through to
        # the taskbar, and the bar must stay draggable between its words.
        self.img = Image.new("RGBA", (max(1, width), max(1, height)), (0, 0, 0, 1))
        self.icons = icons or {}
        self.texts: list[tuple[int, int, int, int]] = []   # drawn text boxes

    def _over(self, color: str, paint) -> None:
        mask = self._Image.new("L", self.img.size, 0)
        paint(self._Draw.Draw(mask))
        layer = self._Image.new("RGBA", self.img.size, _hex_to_rgb(color) + (255,))
        layer.putalpha(mask)
        self.img.alpha_composite(layer)

    def rect(self, x0, y0, x1, y1, fill, outline=None):
        box = tuple(int(round(v)) for v in (x0, y0, x1 - 1, y1 - 1))
        if box[2] < box[0] or box[3] < box[1]:
            return
        self._over(fill, lambda d: d.rectangle(box, fill=255))
        if outline:
            self._over(outline, lambda d: d.rectangle(box, outline=255))

    def text(self, x, y, s, fill, font, anchor="w"):
        f = _pil_font(font)
        a = "lm" if anchor == "w" else "mm"
        self._over(fill, lambda d: d.text((x, y), s, font=f, fill=255, anchor=a))
        self.texts.append(self._Draw.Draw(self.img).textbbox((x, y), s, font=f, anchor=a))

    def oval(self, x0, y0, x1, y1, fill, outline, width):
        # PIL does not anti-alias ellipses: draw 4x larger and scale down.
        x0, y0 = int(round(x0)), int(round(y0))
        w, h = int(round(x1)) - x0, int(round(y1)) - y0
        if w <= 0 or h <= 0:
            return
        k = 4

        def disc(inset):
            big = self._Image.new("L", (w * k, h * k), 0)
            i = inset * k
            self._Draw.Draw(big).ellipse((i, i, w * k - 1 - i, h * k - 1 - i), fill=255)
            small = big.resize((w, h), self._Image.LANCZOS)
            return lambda d: d.bitmap((x0, y0), small, fill=255)

        if outline and width:
            self._over(outline, disc(0))
        self._over(fill, disc(width if outline else 0))

    def vline(self, x, y0, y1, fill):
        self.rect(x, y0, x + 1, y1, fill)

    def icon(self, name, x0, y0) -> bool:
        img = self.icons.get(name)
        if img is None:
            return False
        self.img.alpha_composite(img, dest=(int(round(x0)), int(round(y0))))
        return True


def _clear_frame(width: int, height: int):
    from PIL import Image
    return Image.new("RGBA", (max(1, width), max(1, height)), (0, 0, 0, 0))


class _WNDCLASSW(ctypes.Structure):
    _fields_ = [("style", ctypes.c_uint), ("lpfnWndProc", ctypes.c_void_p),
                ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
                ("hInstance", ctypes.c_void_p), ("hIcon", ctypes.c_void_p),
                ("hCursor", ctypes.c_void_p), ("hbrBackground", ctypes.c_void_p),
                ("lpszMenuName", ctypes.c_wchar_p), ("lpszClassName", ctypes.c_wchar_p)]


class _RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


_WIPE_CLASS = "ClaudeCodexUsageWipe"


def _wipe_taskbar_strip(taskbar: int, x: int, y: int, w: int, h: int) -> None:
    """Paint black over part of the taskbar through a throwaway plain child.

    Its own class with a black background, so even if Windows gets to erase
    it before it is gone, the erase is black too. Nothing is pumped in
    between: the window lives for one fill.
    """
    try:
        u = ctypes.WinDLL("user32", use_last_error=True)
        g = ctypes.WinDLL("gdi32")
        k = ctypes.WinDLL("kernel32")
        vp = ctypes.c_void_p
        k.GetModuleHandleW.restype, k.GetModuleHandleW.argtypes = vp, [ctypes.c_wchar_p]
        g.GetStockObject.restype, g.GetStockObject.argtypes = vp, [ctypes.c_int]
        u.CreateWindowExW.restype = vp
        u.CreateWindowExW.argtypes = [ctypes.c_uint32, ctypes.c_wchar_p, ctypes.c_wchar_p,
                                      ctypes.c_uint32, ctypes.c_int, ctypes.c_int,
                                      ctypes.c_int, ctypes.c_int, vp, vp, vp, vp]
        u.GetDC.restype, u.GetDC.argtypes = vp, [vp]
        u.ReleaseDC.argtypes = [vp, vp]
        u.FillRect.argtypes = [vp, ctypes.POINTER(_RECT), vp]
        u.DestroyWindow.argtypes = [vp]
        u.RegisterClassW.argtypes = [ctypes.POINTER(_WNDCLASSW)]
        hinst = k.GetModuleHandleW(None)
        black = g.GetStockObject(4)                       # BLACK_BRUSH
        wc = _WNDCLASSW(0, ctypes.cast(u.DefWindowProcW, vp).value, 0, 0, hinst,
                        None, None, black, None, _WIPE_CLASS)
        u.RegisterClassW(ctypes.byref(wc))                # fails harmlessly if registered
        hwnd = u.CreateWindowExW(0x08000000, _WIPE_CLASS, None,       # WS_EX_NOACTIVATE
                                 0x40000000 | 0x10000000,             # WS_CHILD | WS_VISIBLE
                                 x, y, w, h, taskbar, None, hinst, None)
        if not hwnd:
            return
        try:
            u.SetWindowPos(ctypes.c_void_p(hwnd), None, 0, 0, 0, 0,
                           _SWP_NOSIZE | _SWP_NOMOVE | _SWP_NOACTIVATE)   # top of the children
            hdc = u.GetDC(hwnd)
            if hdc:
                u.FillRect(hdc, ctypes.byref(_RECT(0, 0, w, h)), black)
                u.ReleaseDC(hwnd, hdc)
            g.GdiFlush()
        finally:
            u.DestroyWindow(hwnd)
    except Exception as e:
        print(f"[widget] taskbar wipe failed: {e}")


class _BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", ctypes.c_uint32), ("biWidth", ctypes.c_int32),
                ("biHeight", ctypes.c_int32), ("biPlanes", ctypes.c_uint16),
                ("biBitCount", ctypes.c_uint16), ("biCompression", ctypes.c_uint32),
                ("biSizeImage", ctypes.c_uint32), ("biXPelsPerMeter", ctypes.c_int32),
                ("biYPelsPerMeter", ctypes.c_int32), ("biClrUsed", ctypes.c_uint32),
                ("biClrImportant", ctypes.c_uint32)]


class _POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class _SIZE(ctypes.Structure):
    _fields_ = [("cx", ctypes.c_long), ("cy", ctypes.c_long)]


class _BLENDFUNCTION(ctypes.Structure):
    _fields_ = [("BlendOp", ctypes.c_ubyte), ("BlendFlags", ctypes.c_ubyte),
                ("SourceConstantAlpha", ctypes.c_ubyte), ("AlphaFormat", ctypes.c_ubyte)]


class _LayeredSurface:
    """Pushes RGBA frames to a WS_EX_LAYERED window (UpdateLayeredWindow).

    Uses private user32/gdi32 handles so the argtypes set here never leak
    into the rest of the app's ctypes calls.
    """

    def __init__(self, hwnd: int):
        self.hwnd = hwnd
        u = self._u = ctypes.WinDLL("user32")
        g = self._g = ctypes.WinDLL("gdi32")
        vp = ctypes.c_void_p
        u.GetDC.restype, u.GetDC.argtypes = vp, [vp]
        u.ReleaseDC.argtypes = [vp, vp]
        u.UpdateLayeredWindow.argtypes = [vp, vp, vp, vp, vp, vp, ctypes.c_uint32,
                                          vp, ctypes.c_uint32]
        g.CreateCompatibleDC.restype, g.CreateCompatibleDC.argtypes = vp, [vp]
        g.CreateDIBSection.restype = vp
        g.CreateDIBSection.argtypes = [vp, vp, ctypes.c_uint, ctypes.POINTER(vp), vp,
                                       ctypes.c_uint32]
        g.SelectObject.restype, g.SelectObject.argtypes = vp, [vp, vp]
        g.DeleteObject.argtypes = [vp]
        g.DeleteDC.argtypes = [vp]
        self.size: tuple[int, int] | None = None
        self.mem = self.dib = self.old = None
        self.bits = ctypes.c_void_p()

    def _alloc(self, w: int, h: int) -> None:
        self.release()
        screen = self._u.GetDC(None)
        try:
            self.mem = self._g.CreateCompatibleDC(screen)
            bmi = _BITMAPINFOHEADER(ctypes.sizeof(_BITMAPINFOHEADER), w, -h, 1, 32,
                                    0, 0, 0, 0, 0, 0)
            self.dib = self._g.CreateDIBSection(screen, ctypes.byref(bmi), 0,
                                                ctypes.byref(self.bits), None, 0)
        finally:
            self._u.ReleaseDC(None, screen)
        if not self.mem or not self.dib:
            raise OSError("could not allocate the layered frame")
        self.old = self._g.SelectObject(self.mem, self.dib)
        self.size = (w, h)

    def push(self, img) -> bool:
        w, h = img.size
        if (w, h) != self.size:
            self._alloc(w, h)
        data = img.convert("RGBa").tobytes("raw", "BGRa")   # premultiplied BGRA
        ctypes.memmove(self.bits, data, len(data))
        size, src = _SIZE(w, h), _POINT(0, 0)
        blend = _BLENDFUNCTION(0, 0, 255, 1)                  # AC_SRC_OVER, AC_SRC_ALPHA
        return bool(self._u.UpdateLayeredWindow(
            self.hwnd, None, None, ctypes.byref(size), self.mem,
            ctypes.byref(src), 0, ctypes.byref(blend), 2))     # ULW_ALPHA

    def release(self) -> None:
        if self.mem:
            if self.old:
                self._g.SelectObject(self.mem, self.old)
            self._g.DeleteDC(self.mem)
        if self.dib:
            self._g.DeleteObject(self.dib)
        self.mem = self.dib = self.old = None
        self.size = None


class TaskbarWidget:
    def __init__(self, root: tk.Misc, cfg: dict,
                 on_double_click: Callable[[], None],
                 on_right_click_menu: Callable[[int, int], None],
                 save_position: Callable[[int, int], None],
                 save_taskbar_offset: Callable[[int], None] | None = None,
                 on_taskbar_lost: Callable[[], None] | None = None,
                 on_embed_refused: Callable[[str], None] | None = None):
        self.cfg = cfg
        self.on_double_click = on_double_click
        self.on_right_click_menu = on_right_click_menu
        self.save_position = save_position
        self.save_taskbar_offset = save_taskbar_offset
        self.on_taskbar_lost = on_taskbar_lost
        self.on_embed_refused = on_embed_refused
        self.embed_verdict = ""
        self.embedded = False
        self._taskbar = 0
        self._visible = True
        raw_bg = str(cfg.get("taskbar_bg") or "auto")
        self._bg_auto = raw_bg.lower() == "auto"
        self._bg_offset = (_hex_to_rgb(TASKBAR_BG) if self._bg_auto
                           else _hex_to_rgb(raw_bg))
        self._calibrating = False
        self._pending_right = 0
        self._matched_bg: str | None = None
        self._drag_grab = 0
        self._dragging = False
        self._drag_moved = False
        self._grow: tuple[tuple[str, int], float] | None = None
        self._grow_timer = None
        self._icons: dict[tuple[str, int], tk.PhotoImage | None] = {}
        self._icon_images: dict[tuple[str, int], object] = {}
        self._layered = False          # embedded and drawn via UpdateLayeredWindow
        self._surface: _LayeredSurface | None = None

        # Map of the taskbar's free room, kept current on another thread.
        # Waiting briefly for the first map avoids opening at full width over
        # a laptop's app buttons and shrinking a moment later.
        self._watch = taskbar_space.watcher()
        self._watch.first.wait(0.5)
        self._space_seq = self._watch.seq
        self._space = self._watch.latest

        self.win = tk.Toplevel(root)
        self.win.overrideredirect(True)
        self.win.wm_attributes("-topmost", True)
        self.win.configure(bg=BG)
        self.win.geometry(f"{WIDTH}x{HEIGHT}")
        self.win.update_idletasks()
        try:
            hwnd = ctypes.windll.user32.GetParent(self.win.winfo_id()) or self.win.winfo_id()
            ex = ctypes.windll.user32.GetWindowLongW(hwnd, _GWL_EXSTYLE)
            ctypes.windll.user32.SetWindowLongW(
                hwnd, _GWL_EXSTYLE,
                ex | _WS_EX_NOACTIVATE | _WS_EX_TOOLWINDOW | _WS_EX_TOPMOST,
            )
        except Exception as e:
            print(f"[widget] failed to set extended styles: {e}")
        self._hwnd = hwnd

        self.canvas = tk.Canvas(
            self.win, width=WIDTH, height=HEIGHT,
            bg=BG, highlightthickness=0, bd=0,
        )
        self.canvas.pack(fill="both", expand=True)

        self._load_icons()

        # Drag-to-move
        self._drag_dx = 0
        self._drag_dy = 0
        self._press_x = 0
        self.canvas.bind("<ButtonPress-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_motion)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)
        self.canvas.bind("<Double-Button-1>", lambda _e: self.on_double_click())
        self.canvas.bind("<Button-3>", self._on_right_click)

        self._apply_mode()
        self._reassert_after()
        self._follow_space()

    def _load_icons(self):
        # Cache PhotoImages here so Tk doesn't garbage-collect them; one set
        # per icon size, since the narrower layouts use smaller icons.
        for name in ("claude", "codex"):
            key = (name, ICON_PX)
            if key not in self._icons:
                self._icons[key] = _load_app_icon(name, ICON_PX)
            if key not in self._icon_images:
                self._icon_images[key] = _load_app_icon_image(name, ICON_PX)
        self._icon_claude = self._icons[("claude", ICON_PX)]
        self._icon_codex = self._icons[("codex", ICON_PX)]

    def _icon_images_now(self) -> dict:
        return {name: self._icon_images.get((name, ICON_PX))
                for name in ("claude", "codex")}

    # --- colour ----------------------------------------------------

    def _adj(self, color: str) -> str:
        """Cancel the taskbar's additive composite so an embedded widget shows
        the authored colour. A no-op when floating, where nothing blends."""
        if not self.embedded:
            return color
        try:
            r, g, b = _hex_to_rgb(color)
        except Exception:
            return color
        o = self._bg_offset
        return _rgb_to_hex((max(0, r - o[0]), max(0, g - o[1]), max(0, b - o[2])))

    def float_is_transparent(self) -> bool:
        return (not self.embedded
                and bool(self.cfg.get("float_transparent", False)))

    def _float_background(self) -> str:
        """What a floating bar should paint behind itself."""
        if self.embedded or not self.cfg.get("float_match_taskbar", True):
            return BG
        if session_locked():
            return self._matched_bg or BG   # a lock screen is not the taskbar
        got = sample_taskbar_beside(_window_rect(self._hwnd))
        if got:
            self._matched_bg = got
        return self._matched_bg or BG

    def _apply_colors(self):
        bg = self._adj(BG) if self.embedded else self._float_background()
        try:
            self.win.configure(bg=bg)
            self.canvas.configure(bg=bg)
        except tk.TclError:
            pass
        if self._layered:
            # Tk's -transparentcolor switches WS_EX_LAYERED off whenever it
            # has no colour key of its own, which would end the layered frame.
            return
        # A floating bar cannot match a taskbar that is tinted by the
        # wallpaper, so drop the background entirely and let the real strip
        # show through. Keyed on our own background colour, so anti-aliased
        # edges fade into exactly the shade they would have blended with.
        try:
            self.win.wm_attributes(
                "-transparentcolor", bg if self.float_is_transparent() else "")
        except tk.TclError:
            pass

    # --- taskbar embedding ----------------------------------------

    def wants_embed(self) -> bool:
        return str(self.cfg.get("widget_mode", "taskbar")).lower() == "taskbar"

    def _apply_mode(self):
        if self.wants_embed() and self._embed():
            self._apply_colors()
            # A layered bar needs no colour offset, but the probe still has to
            # prove that this taskbar shows it at all.
            if self._bg_auto or self._layered:
                self.win.after(400, self._calibrate)
            return
        self._unembed()
        self._apply_colors()
        self._place_initial()

    # --- runtime colour calibration -------------------------------

    def _retry_embed(self) -> None:
        if self.embedded or not self.wants_embed():
            return
        if self._embed():
            self.relayout()
            if self._bg_auto or self._layered:
                self.win.after(400, self._calibrate)
        else:
            self.win.after(300_000, self._retry_embed)

    def _sweep_stale_frames(self) -> None:
        """Wipe whatever an earlier plain child left in the taskbar's surface.

        A plain child draws into the taskbar's own surface, and the taskbar
        never repaints a region a child leaves: a previous version that was
        killed rather than quit leaves its last frame there for good. The
        layered bar's background is see-through, so that frame would show
        through it. Black adds nothing in the additive blend, so painting it
        over the stretch beside the clock (and our own slot) removes the
        stale pixels without visibly changing the taskbar.
        """
        r = _window_rect(self._taskbar)
        if r is None:
            return
        size, x = self._target(r)
        spans = [(x, x + layout_width(self.cfg, size))]
        home = self._space.home() if self._space is not None else None
        if home is not None:
            spans.append(home)
        else:
            # where every earlier version sat by default
            right = r[2] - self._default_right_offset(self._taskbar)
            spans.append((right - layout_width(self.cfg, SIZES[0]), right))
        lo = max(r[0], min(s[0] for s in spans))
        hi = min(r[2], max(s[1] for s in spans))
        if hi > lo:
            _wipe_taskbar_strip(self._taskbar, lo - r[0], 0, hi - lo, r[3] - r[1])

    def _enter_layered(self) -> bool:
        """Draw the embedded bar through UpdateLayeredWindow (see the painters
        above). False, with the window left as it was, where that fails."""
        if not self.cfg.get("embed_layered", True):
            return False
        if self._layered and self._surface is not None:
            return True          # re-embedding (e.g. after a theme switch)
        try:
            # A colour key left from a see-through floating bar makes Windows
            # refuse UpdateLayeredWindow on the same window; clear it first.
            self.win.wm_attributes("-transparentcolor", "")
        except tk.TclError:
            pass
        try:
            u = ctypes.windll.user32
            ex = u.GetWindowLongW(self._hwnd, _GWL_EXSTYLE)
            u.SetWindowLongW(self._hwnd, _GWL_EXSTYLE, ex | _WS_EX_LAYERED)
            self._surface = _LayeredSurface(self._hwnd)
            self._layered = True
            if self._surface.push(_clear_frame(WIDTH, HEIGHT)):
                return True
            print("[widget] UpdateLayeredWindow refused the frame")
        except Exception as e:
            print(f"[widget] layered drawing unavailable: {e}")
        self._leave_layered()
        return False

    def _leave_layered(self) -> None:
        """Back to Tk's own painting."""
        if self._surface is not None:
            try:
                self._surface.release()
            except Exception:
                pass
            self._surface = None
        self._layered = False
        try:
            u = ctypes.windll.user32
            ex = u.GetWindowLongW(self._hwnd, _GWL_EXSTYLE)
            if ex & _WS_EX_LAYERED:
                u.SetWindowLongW(self._hwnd, _GWL_EXSTYLE, ex & ~_WS_EX_LAYERED)
                # RDW_ERASE | RDW_INVALIDATE | RDW_FRAME | RDW_ALLCHILDREN
                u.RedrawWindow(ctypes.c_void_p(self._hwnd), None, None, 0x4 | 0x1 | 0x400 | 0x80)
        except Exception:
            pass

    def _push_frame(self, img) -> None:
        try:
            ok = self._surface is not None and self._surface.push(img)
        except Exception as e:
            print(f"[widget] layered frame failed: {e}")
            ok = False
        if not ok and self._layered:
            # Keep showing something: fall back to painting the plain child.
            self._leave_layered()
            self._bg_offset = _hex_to_rgb(TASKBAR_BG)
            self._apply_colors()
            self.redraw_last()

    def _probe_box(self) -> tuple[int, int, int, int]:
        """A small patch in the middle of the bar.

        The probe used to recolour the whole widget, so an interrupted
        calibration burned a bar-wide grey block into the taskbar. A patch
        bounds that to a few pixels.
        """
        cx, cy = WIDTH // 2, HEIGHT // 2
        h = max(4, int(round(6 * SCALE)))
        return (cx - h, cy - h, cx + h, cy + h)

    def _probe_paint(self, color: str) -> None:
        if self._layered:
            p = _ImagePainter(WIDTH, HEIGHT)
            p.rect(*self._probe_box(), color)
            self._push_frame(p.img)
            return
        try:
            self.canvas.delete("all")
            self._apply_colors()
            self.canvas.create_rectangle(self._probe_box(), fill=color, outline="")
            self.win.update_idletasks()
        except tk.TclError:
            pass

    def _probe_point(self) -> tuple[int, int] | None:
        r = _window_rect(self._hwnd)
        if r is None:
            return None
        return (r[0] + r[2]) // 2, (r[1] + r[3]) // 2

    def _calibrate(self) -> None:
        """Measure how this machine's taskbar composites our pixels and adapt.

        Paints solid black, samples the screen (→ the backdrop / additive
        offset), paints 50%% gray, samples again (→ proves our drawing is
        visible and the composite is additive). Runs asynchronously so the
        compositor has time to show each frame.
        """
        if not self.embedded or self._calibrating or not self._visible:
            return
        pt = self._probe_point()
        if session_locked() or pt is None or not _point_belongs_to(self._hwnd, *pt):
            # Locked screen, or something sits over our slot. Measuring now
            # would read someone else's pixels and wrongly conclude that the
            # taskbar cannot show us, so wait and look again.
            self.win.after(30_000, self._calibrate)
            return
        self._calibrating = True
        self._probe_paint("#000000")

        def step2():
            pt = self._probe_point()
            s_black = _sample_screen(*pt) if pt else None
            self._probe_paint("#808080")

            def step3():
                s_gray = _sample_screen(*pt) if pt else None
                self._calibrating = False
                verdict, offset = _classify_calibration(s_black, s_gray)
                _write_calibration_state({
                    "verdict": verdict, "black": s_black, "gray": s_gray,
                    "offset": offset, "layered": self._layered})
                if (self._layered and verdict in ("ok", "approx")
                        and offset is not None and max(offset) > 8):
                    # Black did not come out black: this taskbar adds even a
                    # layered bar onto its backdrop. Use the plain child, whose
                    # offset correction handles exactly that, and measure again.
                    print(f"[widget] layered bar blended additively ({offset}); "
                          "using the plain child")
                    self._leave_layered()
                    self._bg_offset = offset
                    self._apply_colors()
                    self.redraw_last()
                    self.win.after(400, self._calibrate)
                    return
                if verdict in ("ok", "approx") and offset is not None:
                    self._bg_offset = offset
                    self._apply_colors()
                    self.redraw_last()
                elif verdict in ("invisible", "too-bright"):
                    # This taskbar can't show us legibly — float instead.
                    print(f"[widget] embed calibration: {verdict} — "
                          "falling back to float mode")
                    self.embed_verdict = verdict
                    if self.on_embed_refused:
                        self.on_embed_refused(verdict)
                    self._unembed()
                    self._apply_colors()
                    self._place_initial()
                    self.redraw_last()
                    if verdict == "invisible" and self.wants_embed():
                        # A lock screen or a window over the strip looks the
                        # same as a taskbar that cannot host us. Float now so
                        # the bar is never missing, and look again later.
                        self.win.after(300_000, self._retry_embed)
                else:  # unknown — keep the configured guess
                    self._apply_colors()
                    self.redraw_last()

            self.win.after(250, step3)

        self.win.after(250, step2)

    def set_embedded(self, on: bool):
        """Switch between living inside the taskbar and floating over it."""
        self.cfg["widget_mode"] = "taskbar" if on else "float"
        self._apply_mode()

    def _embed(self) -> bool:
        tb = _find_taskbar()
        if not tb or not self._hwnd:
            return False
        try:
            u = ctypes.windll.user32
            style = u.GetWindowLongW(self._hwnd, _GWL_STYLE) & 0xFFFFFFFF
            u.SetWindowLongW(self._hwnd, _GWL_STYLE,
                             _i32((style & ~_WS_POPUP) | _WS_CHILD))
            # SetParent returns the PREVIOUS parent, which is 0 for a window
            # that had none — a success that reads as failure. Confirm by
            # asking who the parent is now.
            u.SetParent.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
            u.SetParent.restype = ctypes.c_void_p
            u.GetParent.argtypes = [ctypes.c_void_p]
            u.GetParent.restype = ctypes.c_void_p
            u.SetParent(self._hwnd, tb)
            if int(u.GetParent(self._hwnd) or 0) != int(tb):
                return False
            self._taskbar = tb
            self.embedded = True
            self._sweep_stale_frames()
            if self._enter_layered():
                self._bg_offset = (0, 0, 0)   # composited "over": no offset
            # Repaint in the embedded palette BEFORE the window is shown at its
            # slot: the canvas is built in the floating colours, and the taskbar
            # keeps whatever a child last drew, so showing it first burns that
            # uncorrected shade into the strip.
            self._apply_colors()
            self._place_embedded()
            return True
        except Exception as e:
            print(f"[widget] embed failed: {e}")
            return False

    def _unembed(self):
        if not self.embedded:
            return
        self._blank()
        if self._layered:
            # Go invisible while still layered and only drop the style once
            # we are a top-level window again: turned into a plain child first,
            # Tk would paint one frame into the taskbar and leave it there.
            self._push_frame(_clear_frame(WIDTH, HEIGHT))
        try:
            u = ctypes.windll.user32
            u.SetParent(self._hwnd, None)
            style = u.GetWindowLongW(self._hwnd, _GWL_STYLE) & 0xFFFFFFFF
            u.SetWindowLongW(self._hwnd, _GWL_STYLE,
                             _i32((style & ~_WS_CHILD) | _WS_POPUP))
            self._leave_layered()
            self.win.wm_attributes("-topmost", True)
        except Exception as e:
            print(f"[widget] unembed failed: {e}")
        self.embedded = False
        self._taskbar = 0

    def _default_right_offset(self, tb: int) -> int:
        return _tray_notify_width(tb) + sc(EMBED_GAP)

    # --- choosing a size and a spot ------------------------------------

    def _anchor(self, tb: tuple[int, int, int, int]) -> tuple[int, bool] | None:
        """(screen x, grows_left) of an edge the user dragged the bar to, or
        None while the bar is placed automatically."""
        if self.embedded:
            saved = self.cfg.get("widget_taskbar_offset") or {}
            if "right" not in saved:
                return None
            return tb[2] - int(saved["right"]), True
        saved = self.cfg.get("widget_position") or {}
        if "x" not in saved:
            return None
        return int(saved["x"]), False

    def _choose(self, tb: tuple[int, int, int, int]) -> tuple[str, int]:
        """The layout and the screen x for a bar on the taskbar at `tb`."""
        sizes = sizes_for(self.cfg)
        widths = [layout_width(self.cfg, s) for s in sizes]
        anchor = self._anchor(tb)
        space = self._space
        if space is None or (space.taskbar[0], space.taskbar[2]) != (tb[0], tb[2]):
            # No map of the buttons (UI Automation unavailable, or the map is
            # from before the taskbar moved): the old spot beside the clock.
            w = widths[0]
            if anchor is None:
                return sizes[0], tb[2] - self._default_right_offset(_find_taskbar()) - w
            edge, grows_left = anchor
            return sizes[0], (edge - w) if grows_left else edge
        margin = sc(EMBED_MARGIN)
        if anchor is None:
            i, x = taskbar_space.fit_home(space, widths, margin, sc(EMBED_GAP))
            return sizes[i], x
        edge, grows_left = anchor
        i = taskbar_space.fit_anchored(space, widths, margin, edge, grows_left)
        return sizes[i], (edge - widths[i]) if grows_left else edge

    def _target(self, tb: tuple[int, int, int, int]) -> tuple[str, int]:
        """_choose(), with x clamped the way the bar will actually be placed."""
        size, x = self._choose(tb)
        w = layout_width(self.cfg, size)
        lo, hi = (tb[0], tb[2] - w) if self.embedded else (
            0, self.win.winfo_screenwidth() - w)
        return size, max(lo, min(x, max(lo, hi)))

    def _set_size(self, size: str) -> None:
        before = (WIDTH, ICON_PX)
        apply_layout(self.cfg, size)
        if (WIDTH, ICON_PX) == before:
            return
        self._load_icons()
        try:
            self.canvas.configure(width=WIDTH, height=HEIGHT)
            self.win.geometry(f"{WIDTH}x{HEIGHT}")
        except tk.TclError:
            pass

    def _follow_space(self):
        """Refit whenever the watcher publishes a new map of the taskbar."""
        try:
            if (self._watch.seq != self._space_seq and not self._dragging
                    and not self._calibrating):
                self._space_seq = self._watch.seq
                self._space = self._watch.latest
                self._refit()
            self.win.after(400, self._follow_space)
        except tk.TclError:
            return

    def _covers_something(self, rect) -> bool:
        space = self._space
        if space is None or rect is None:
            return False
        return any(lo < rect[2] and hi > rect[0] for lo, hi in space.blocks)

    def _refit(self):
        """Move or resize the bar if the free room around it has changed.

        Anything that stops the bar covering a button happens at once.
        Growing back while the bar is already clear waits a few seconds: a
        window that opens and closes quickly should not make the bar pulse.
        """
        if self._dragging or self._calibrating or not self._visible:
            return
        tb = _window_rect(self._taskbar if self.embedded else _find_taskbar())
        if tb is None:
            return
        if not self.embedded and not self._float_on_taskbar(tb):
            # Parked on the desktop: nothing to dodge, so the richest layout.
            if SIZE != sizes_for(self.cfg)[0]:
                self._place_initial()
                self.redraw_last()
            return
        size, x = self._target(tb)
        cur = _window_rect(self._hwnd)
        if size == SIZE and cur is not None and cur[0] == x:
            self._grow = None
            return
        if (SIZES.index(size) < SIZES.index(SIZE)
                and not self._covers_something(cur)):
            import time as _t
            now = _t.monotonic()
            if self._grow is None or self._grow[0] != (size, x):
                self._grow = ((size, x), now)
            if now - self._grow[1] < GROW_DELAY_S:
                if self._grow_timer is None:
                    self._grow_timer = self.win.after(1000, self._grow_tick)
                return
        self._grow = None
        if self.embedded:
            self._place_embedded()
        else:
            self._place_initial()
        self.redraw_last()

    def _grow_tick(self):
        self._grow_timer = None
        self._refit()

    def _place_embedded(self):
        self._blank()
        r = _window_rect(self._taskbar)
        if r is None:
            return
        tb_w, tb_h = r[2] - r[0], r[3] - r[1]
        size, x_screen = self._target(r)
        self._set_size(size)
        h = min(HEIGHT, tb_h)
        x = x_screen - r[0]
        self._pending_right = tb_w - WIDTH - x
        y = max(0, (tb_h - h) // 2)
        ctypes.windll.user32.SetWindowPos(
            self._hwnd, _HWND_TOP, x, y, WIDTH, h,
            _SWP_NOACTIVATE | _SWP_SHOWWINDOW)

    def reset_position(self):
        """Back to the automatic spot for whichever mode is active."""
        if self.embedded:
            self.cfg.pop("widget_taskbar_offset", None)
            self._place_embedded()
            self.redraw_last()
        else:
            self.cfg.pop("widget_position", None)
            self._place_initial()
            self.redraw_last()

    # --- positioning ----------------------------------------------

    def _float_on_taskbar(self, tb: tuple[int, int, int, int]) -> bool:
        """A floating bar the user parked away from the taskbar has nothing
        to dodge, so it keeps its size wherever it is."""
        saved = self.cfg.get("widget_position") or {}
        if "y" not in saved:
            return True
        mid = int(saved["y"]) + HEIGHT // 2
        return tb[1] <= mid <= tb[3]

    def _place_initial(self):
        sx = self.win.winfo_screenwidth()
        sy = self.win.winfo_screenheight()
        saved = self.cfg.get("widget_position") or {}
        tb = _window_rect(_find_taskbar())
        if tb is not None and self._float_on_taskbar(tb):
            # Same room-finding as the embedded bar; it used to default to the
            # middle of the screen, right over Windows 11's centred buttons.
            size, x = self._target(tb)
            self._set_size(size)
            y = int(saved.get("y", tb[1] + max(0, (tb[3] - tb[1] - HEIGHT) // 2)))
        else:
            self._set_size(sizes_for(self.cfg)[0])
            tb_h = _get_taskbar_height()
            x = int(saved.get("x", (sx - WIDTH) // 2))
            y = int(saved.get("y", sy - tb_h + max(0, (tb_h - HEIGHT) // 2)))
        x = max(0, min(x, sx - WIDTH))
        y = max(0, min(y, sy - HEIGHT))
        self.win.geometry(f"{WIDTH}x{HEIGHT}+{x}+{y}")
        # Apply it now. Left pending, the -topmost raise that follows made Tk
        # re-read the unmapped window's position, so a bar started in float
        # mode opened in the top-left corner of the screen.
        self.win.update_idletasks()

    def _reassert_after(self):
        try:
            if self.embedded:
                u = ctypes.windll.user32
                if not u.IsWindow(self._taskbar):
                    # explorer.exe restarted — the taskbar took our window
                    # down with it, so the whole widget has to be rebuilt.
                    self.embedded = False
                    if self.on_taskbar_lost:
                        self.on_taskbar_lost()
                    return
                if self._visible:
                    u.SetWindowPos(self._hwnd, _HWND_TOP, 0, 0, 0, 0,
                                   _SWP_NOACTIVATE | _SWP_NOSIZE | _SWP_NOMOVE)
            else:
                self.win.wm_attributes("-topmost", True)
                # A layered (transparent) window loses the z-order fight with
                # the taskbar even while it still carries WS_EX_TOPMOST, and Tk
                # skips the raise when the attribute is already set. Ask for the
                # top slot outright.
                u = ctypes.windll.user32
                u.SetWindowPos(self._hwnd, _HWND_TOPMOST, 0, 0, 0, 0,
                               _SWP_NOSIZE | _SWP_NOMOVE | _SWP_NOACTIVATE)
        except tk.TclError:
            return
        except Exception:
            pass
        # The Windows 11 taskbar re-asserts its own topmost slot, which pushes a
        # floating bar underneath it within a second or two — it was blinking in
        # and out at exactly the old 4s re-assert interval. Inside the taskbar
        # nothing competes for the slot, so that case stays slow.
        self.win.after(4000 if self.embedded else FLOAT_RAISE_MS,
                       self._reassert_after)

    def _on_press(self, ev):
        self._dragging = True
        self._drag_moved = False
        self._press_x = ev.x_root
        if self.embedded:
            r = _window_rect(self._hwnd)
            self._drag_grab = ev.x_root - (r[0] if r else ev.x_root)
            # Drag as an empty slot: the trail left behind is then the
            # taskbar's own colour instead of a smear of old bars.
            self._blank()
            return
        self._drag_dx = ev.x_root - self.win.winfo_x()
        self._drag_dy = ev.y_root - self.win.winfo_y()

    def _on_motion(self, ev):
        if abs(ev.x_root - self._press_x) < 3 and not self._drag_moved:
            return    # a click, not a drag
        self._drag_moved = True
        if self.embedded:
            # Inside the taskbar only the horizontal slot is meaningful; the
            # 72px strip fixes the vertical position for us.
            r = _window_rect(self._taskbar)
            if r is None:
                return
            tb_w, tb_h = r[2] - r[0], r[3] - r[1]
            x = ev.x_root - self._drag_grab - r[0]
            x = max(0, min(x, max(0, tb_w - WIDTH)))
            h = min(HEIGHT, tb_h)
            y = max(0, (tb_h - h) // 2)
            ctypes.windll.user32.SetWindowPos(
                self._hwnd, _HWND_TOP, x, y, WIDTH, h,
                _SWP_NOACTIVATE | _SWP_SHOWWINDOW)
            self._pending_right = tb_w - WIDTH - x
            return
        x = ev.x_root - self._drag_dx
        y = ev.y_root - self._drag_dy
        self.win.geometry(f"+{x}+{y}")

    def _on_release(self, _ev):
        self._dragging = False
        # Only a real drag pins the bar. A click (or the double-click that
        # opens the dashboard) used to save the spot too, which silently
        # switched off the automatic placement.
        if not self._drag_moved:
            self.redraw_last()
            return
        if self.embedded:
            if self.save_taskbar_offset:
                self.save_taskbar_offset(int(self._pending_right))
        else:
            self.save_position(self.win.winfo_x(), self.win.winfo_y())
        # Dropped somewhere narrower (or wider): fit the room found there.
        self._refit()
        self.redraw_last()

    def _on_right_click(self, ev):
        self.on_right_click_menu(ev.x_root, ev.y_root)

    # --- visibility -----------------------------------------------

    def show(self):
        self._visible = True
        if self.embedded:
            # A WS_CHILD window is not a Tk toplevel any more, so deiconify()
            # no longer reaches it — drive visibility through the API.
            ctypes.windll.user32.ShowWindow(self._hwnd, _SW_SHOWNOACTIVATE)
        else:
            self.win.deiconify()
        # The buttons may have moved while the bar was hidden.
        self._refit()

    def hide(self):
        self._visible = False
        if self.embedded:
            ctypes.windll.user32.ShowWindow(self._hwnd, _SW_HIDE)
            return
        self.win.withdraw()

    def is_visible(self) -> bool:
        if self.embedded:
            return self._visible
        return bool(self.win.winfo_viewable())

    def retheme(self):
        """Re-apply after the Windows shade changed: a light taskbar cannot host
        the widget, so the mode may have to change too."""
        self._bg_offset = _hex_to_rgb(TASKBAR_BG)
        self._apply_mode()
        self.relayout()

    def relayout(self):
        """Re-measure the bar after a settings change and put it back.
        The size is chosen again too: hiding a side or pinning a layout
        changes what fits."""
        try:
            self._blank()
            if self.embedded:
                self._place_embedded()
            else:
                self._place_initial()
        except tk.TclError:
            return
        self.redraw_last()

    def redraw_last(self):
        if getattr(self, "_last_render", None):
            self.render(*self._last_render)

    def _blank(self):
        """Wipe to the bare taskbar colour and let it paint. The taskbar never
        recomposites a region a child window vacates, so whatever is on screen
        when we move or quit stays there — leave the taskbar's own colour.
        A layered bar is composited on its own and leaves nothing behind."""
        if not self.embedded or self._layered:
            return
        try:
            self.canvas.delete("all")
            # Calibration repaints the background, so restore it here rather
            # than trusting whatever colour the canvas was left holding.
            self._apply_colors()
            self.win.update_idletasks()
        except tk.TclError:
            pass

    def destroy(self):
        self._visible = False          # stops any pending refit from placing us
        self._blank()
        # Detach from the taskbar first: destroying a live child of
        # Shell_TrayWnd from Tk's side is more fragile than unparenting.
        try:
            self._unembed()
        except Exception:
            pass
        try:
            self.win.destroy()
        except tk.TclError:
            pass

    # --- rendering ------------------------------------------------

    def render(self, snap, cfg: dict):
        self._last_render = (snap, cfg)
        if self._calibrating:
            # A poller redraw would repaint over the probe colours and corrupt
            # the samples; calibration ends with redraw_last(), so just record.
            return
        t = cfg.get("thresholds", {})
        warn = int(t.get("warn", 50))
        danger = int(t.get("danger", 80))

        if getattr(self, "_layered", False):
            p = _ImagePainter(WIDTH, HEIGHT, self._icon_images_now())
        else:
            if not self.embedded:
                # Re-match on every redraw: the strip's tint moves with the
                # wallpaper and with whatever is behind it.
                self._apply_colors()
            p = _CanvasPainter(self)
        self._draw(p, snap, cfg, warn, danger)
        if isinstance(p, _ImagePainter):
            self._push_frame(p.img)

    def _draw(self, p, snap, cfg: dict, warn: int, danger: int) -> None:
        pad_x, gap_icon = PAD_X, GAP_ICON
        bar_h, bar_w = BAR_H, BAR_W
        row_y_top = ROW_Y_TOP
        row_y_bot = HEIGHT - row_y_top - bar_h
        cap_w, pct_w = CAP_W, PCT_W
        group_w, inter_group_gap = GROUP_W, INTER_GROUP_GAP
        show_claude, show_codex = sides_shown(cfg)

        codex = snap.codex
        claude = snap.claude

        def draw_side(x0: int, icon_name: str, fallback_letter: str,
                      pct_a: float | None, pct_b: float | None,
                      reset_a: float | int | None = None,
                      reset_b: float | int | None = None,
                      annotation: str = "",
                      extra_pct: float | None = None,
                      extra_reset: float | int | None = None,
                      extra_caption: str = "",
                      extra_color: str | None = None,
                      extra_shown: bool = False):
            # icon (or letter fallback)
            ic_y = (HEIGHT - ICON_PX) // 2
            if not p.icon(icon_name, x0, ic_y):
                p.rect(x0, ic_y, x0 + ICON_PX, ic_y + ICON_PX, TILE_FILL,
                       outline=TILE_EDGE)
                p.text(x0 + ICON_PX / 2, ic_y + ICON_PX / 2, fallback_letter,
                       FG_LABEL, FONT_LETTER, anchor="center")

            # State marker over the icon. Drawn as a badge: bare text sank into
            # the icon's own artwork and went unnoticed.
            if annotation:
                r = max(sc(5), 5)
                cx = x0 + ICON_PX - r
                cy = ic_y + r
                tone = COLOR_DANGER if annotation == "!" else COLOR_WARN
                p.oval(cx - r, cy - r, cx + r, cy + r, tone, BG, max(1, sc(1)))
                p.text(cx, cy, annotation, "#ffffff", FONT_BADGE, anchor="center")

            bx = x0 + ICON_PX + gap_icon

            def draw_group(gx: int, y: int, caption: str,
                           pct: float | None, reset_epoch: float | int | None,
                           fill_color: str | None = None):
                p.text(gx + cap_w / 2, y + bar_h / 2, caption, FG_DIM, FONT_CAP,
                       anchor="center")
                bxx = gx + cap_w + CAP_GAP
                color = fill_color if fill_color else _pick_color(pct, warn, danger)
                ink = FG_LABEL
                if bar_w:
                    p.rect(bxx, y, bxx + bar_w, y + bar_h, BG_BAR)
                    if pct is not None:
                        fw = int(bar_w * max(0, min(100, pct)) / 100)
                        if fw > 0:
                            p.rect(bxx, y, bxx + fw, y + bar_h, color)
                    bxx += bar_w + GAP
                else:
                    # No meter to carry the warn/danger colour, so the
                    # number carries it.
                    ink = FG_DIM if pct is None else color
                txt = "—" if pct is None else f"{pct:.0f}%"
                p.text(bxx, y + bar_h / 2, txt, ink, FONT_PCT)
                cd = _format_countdown(reset_epoch) if SHOW_TIME else ""
                if cd:
                    p.text(bxx + pct_w + TIME_GAP, y + bar_h / 2, cd, FG_DIM, FONT_TIME)

            draw_group(bx, row_y_top, "5h", pct_a, reset_a)
            if extra_shown or extra_pct is not None:
                draw_group(bx + group_w + inter_group_gap, row_y_top,
                           extra_caption or "F", extra_pct, extra_reset,
                           fill_color=extra_color)
            draw_group(bx, row_y_bot, "7d", pct_b, reset_b)

        claude_5h = claude.pct_5h if claude.available else None
        claude_7d = claude.pct_7d if claude.available else None
        claude_r5 = claude.block_resets_at if claude.available else None
        claude_r7 = claude.week_resets_at if claude.available else None
        claude_anno = "~" if (claude.available and claude.note.startswith("estimate")) else ""
        fable_on = claude.available and getattr(claude, "fable_available", False)
        fable_pct = claude.fable_pct if fable_on else None
        fable_reset = claude.fable_resets_at if fable_on else None
        fable_shown = fable_on
        if getattr(claude, "needs_login", False):
            # Never fail silently: make the dead-auth state visible.
            claude_anno = "!"
            if not fable_on:
                # Keep the slot, but as "—". It used to print 0%, which reads
                # as "nothing used" when the truth is "cannot be read".
                fable_shown = True
                fable_pct = None
                fable_reset = None
        if self.float_is_transparent():
            # Transparent pixels pass clicks through, so leave something
            # solid to grab the bar by.
            p.rect(0, row_y_top, max(2, sc(3)), HEIGHT - row_y_top, FG_DIM)

        x = pad_x
        if show_claude:
            draw_side(x, "claude", "C", claude_5h, claude_7d,
                      claude_r5, claude_r7, claude_anno,
                      extra_pct=fable_pct, extra_reset=fable_reset,
                      extra_caption="F", extra_color=COLOR_FABLE,
                      extra_shown=fable_shown)
            x += side_width(True) + pad_x

        if show_claude and show_codex:
            p.vline(x, ROW_Y_TOP, HEIGHT - ROW_Y_TOP, DIVIDER)
            x += pad_x

        codex_5h = codex.primary_pct if (codex.available and codex.has_primary) else None
        codex_7d = codex.secondary_pct if (codex.available and codex.has_secondary) else None
        codex_r5 = codex.primary_resets_at if (codex.available and codex.has_primary) else None
        codex_r7 = codex.secondary_resets_at if (codex.available and codex.has_secondary) else None
        if getattr(codex, "limit_reached", False):
            codex_anno = "!"
        elif getattr(codex, "stale", False):
            # The account keeps being spent by surfaces this tool cannot see,
            # so an aged reading must not be read as the current figure.
            codex_anno = "~"
        else:
            codex_anno = ""
        if show_codex:
            draw_side(x, "codex", "X", codex_5h, codex_7d,
                      codex_r5, codex_r7, codex_anno)
