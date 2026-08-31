"""Frameless always-on-top "taskbar widget" — sits at the bottom of the screen so
it visually lives on the Windows 11 taskbar strip.

Layout (440 x 52, two halves):

    | [Claude] 5h ████░░ 6%   | [Codex] 5h ███░░░ 15% |
    |          7d ██░░░░ 19%  |         7d ███░░░ 39% |
"""
from __future__ import annotations
import ctypes
import tkinter as tk

import paths
from typing import Callable


_GWL_EXSTYLE = -20
_GWL_STYLE = -16
_WS_EX_NOACTIVATE = 0x08000000
_WS_EX_TOOLWINDOW = 0x00000080
_WS_EX_TOPMOST = 0x00000008
_WS_CHILD = 0x40000000
_WS_POPUP = 0x80000000
_HWND_TOP = 0
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
EMBED_GAP = 24          # px kept clear between the widget and the clock

# Measured on Windows 11 (transparency effects off): the taskbar strip is a
# flat #111111, and it composites an embedded child window ADDITIVELY over
# that background — everything drawn inside comes out 17/255 brighter. So the
# widget paints BG and gets the taskbar colour back, and every other colour is
# pre-darkened by the same amount while embedded (see TaskbarWidget._adj).
TASKBAR_BG = "#111111"
BG = TASKBAR_BG
BG_BAR = "#3a3a42"
FG_LABEL = "#e8e8ee"
FG_DIM = "#9a9aa6"
COLOR_OK = "#3fbf6a"
COLOR_WARN = "#e8a828"
COLOR_DANGER = "#dc3c3c"
COLOR_NA = "#5a5a64"
COLOR_FABLE = "#9b7cd6"  # violet, matches CodeZeno PR #49

# Geometry is authored at 100% DPI and scaled up at startup. Embedding into
# the taskbar makes this mandatory: a child of the (DPI-aware) taskbar gets
# no DWM scaling of its own, so an unscaled bar renders at 2/3 size on a
# 150% display.
BASE_WIDTH = 500
BASE_HEIGHT = 48
BASE_ICON_PX = 28
SCALE = 1.0
WIDTH = BASE_WIDTH
HEIGHT = BASE_HEIGHT


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


def set_dpi_awareness() -> None:
    """Must run before the first Tk window exists."""
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)   # per-monitor v1
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def init_scale(root) -> float:
    """Derive the pixel scale from the real DPI. Tk already scales point-sized
    fonts by the same factor, so only pixel geometry is adjusted here."""
    global SCALE, WIDTH, HEIGHT, ICON_PX
    try:
        SCALE = max(1.0, float(root.winfo_fpixels("1i")) / 96.0)
    except Exception:
        SCALE = 1.0
    WIDTH = int(round(BASE_WIDTH * SCALE))
    HEIGHT = int(round(BASE_HEIGHT * SCALE))
    ICON_PX = int(round(BASE_ICON_PX * SCALE))
    return SCALE

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


def _load_app_icon(name: str, px: int) -> tk.PhotoImage | None:
    """Load the icon for `name`, resize to `px`, return as PhotoImage.

    Looks for a user-supplied app-{name}-custom.png first (git-ignored, so
    each user can drop in whatever artwork they like), then falls back to the
    bundled neutral app-{name}.png. Returns None if neither exists or
    PIL/Pillow isn't available — the caller then draws a letter tile.
    """
    try:
        from PIL import Image, ImageTk  # type: ignore
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
        return ImageTk.PhotoImage(img)
    except Exception as e:
        print(f"[widget] icon load fail ({name}): {e}")
        return None


class TaskbarWidget:
    def __init__(self, root: tk.Misc, cfg: dict,
                 on_double_click: Callable[[], None],
                 on_right_click_menu: Callable[[int, int], None],
                 save_position: Callable[[int, int], None],
                 save_taskbar_offset: Callable[[int], None] | None = None,
                 on_taskbar_lost: Callable[[], None] | None = None):
        self.cfg = cfg
        self.on_double_click = on_double_click
        self.on_right_click_menu = on_right_click_menu
        self.save_position = save_position
        self.save_taskbar_offset = save_taskbar_offset
        self.on_taskbar_lost = on_taskbar_lost
        self.embedded = False
        self._taskbar = 0
        self._visible = True
        self._bg_offset = _hex_to_rgb(str(cfg.get("taskbar_bg") or TASKBAR_BG))
        self._pending_right = 0
        self._drag_grab = 0

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

        # Cache PhotoImages here so Tk doesn't garbage-collect them.
        self._icon_claude = _load_app_icon("claude", ICON_PX)
        self._icon_codex = _load_app_icon("codex", ICON_PX)

        # Drag-to-move
        self._drag_dx = 0
        self._drag_dy = 0
        self.canvas.bind("<ButtonPress-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_motion)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)
        self.canvas.bind("<Double-Button-1>", lambda _e: self.on_double_click())
        self.canvas.bind("<Button-3>", self._on_right_click)

        self._apply_mode()
        self._reassert_after()

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

    def _apply_colors(self):
        bg = self._adj(BG)
        try:
            self.win.configure(bg=bg)
            self.canvas.configure(bg=bg)
        except tk.TclError:
            pass

    # --- taskbar embedding ----------------------------------------

    def wants_embed(self) -> bool:
        return str(self.cfg.get("widget_mode", "taskbar")).lower() == "taskbar"

    def _apply_mode(self):
        if self.wants_embed() and self._embed():
            self._apply_colors()
            return
        self._unembed()
        self._apply_colors()
        self._place_initial()

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
            if not u.SetParent(self._hwnd, tb):
                return False
            self._taskbar = tb
            self.embedded = True
            self._place_embedded()
            return True
        except Exception as e:
            print(f"[widget] embed failed: {e}")
            return False

    def _unembed(self):
        if not self.embedded:
            return
        self._blank()
        try:
            u = ctypes.windll.user32
            u.SetParent(self._hwnd, None)
            style = u.GetWindowLongW(self._hwnd, _GWL_STYLE) & 0xFFFFFFFF
            u.SetWindowLongW(self._hwnd, _GWL_STYLE,
                             _i32((style & ~_WS_CHILD) | _WS_POPUP))
            self.win.wm_attributes("-topmost", True)
        except Exception as e:
            print(f"[widget] unembed failed: {e}")
        self.embedded = False
        self._taskbar = 0

    def _default_right_offset(self, tb: int) -> int:
        return _tray_notify_width(tb) + EMBED_GAP

    def _place_embedded(self):
        self._blank()
        r = _window_rect(self._taskbar)
        if r is None:
            return
        tb_w, tb_h = r[2] - r[0], r[3] - r[1]
        h = min(HEIGHT, tb_h)
        saved = self.cfg.get("widget_taskbar_offset") or {}
        right = int(saved.get("right", self._default_right_offset(self._taskbar)))
        right = max(0, min(right, max(0, tb_w - WIDTH)))
        self._pending_right = right
        x = max(0, tb_w - WIDTH - right)
        y = max(0, (tb_h - h) // 2)
        ctypes.windll.user32.SetWindowPos(
            self._hwnd, _HWND_TOP, x, y, WIDTH, h,
            _SWP_NOACTIVATE | _SWP_SHOWWINDOW)

    def reset_position(self):
        """Back to the default spot for whichever mode is active."""
        if self.embedded:
            self.cfg.pop("widget_taskbar_offset", None)
            self._place_embedded()
            self.redraw_last()
            if self.save_taskbar_offset:
                self.save_taskbar_offset(self._pending_right)
        else:
            self.cfg.pop("widget_position", None)
            self._place_initial()

    # --- positioning ----------------------------------------------

    def _place_initial(self):
        sx = self.win.winfo_screenwidth()
        sy = self.win.winfo_screenheight()
        saved = self.cfg.get("widget_position") or {}
        tb_h = _get_taskbar_height()
        default_x = (sx - WIDTH) // 2
        default_y = sy - tb_h + max(0, (tb_h - HEIGHT) // 2)
        x = int(saved.get("x", default_x))
        y = int(saved.get("y", default_y))
        x = max(0, min(x, sx - WIDTH))
        y = max(0, min(y, sy - HEIGHT))
        self.win.geometry(f"{WIDTH}x{HEIGHT}+{x}+{y}")

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
        except tk.TclError:
            return
        except Exception:
            pass
        self.win.after(4000, self._reassert_after)

    def _on_press(self, ev):
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
        if self.embedded:
            if self.save_taskbar_offset:
                self.save_taskbar_offset(int(self._pending_right))
            self.redraw_last()
            return
        self.save_position(self.win.winfo_x(), self.win.winfo_y())

    def _on_right_click(self, ev):
        self.on_right_click_menu(ev.x_root, ev.y_root)

    # --- visibility -----------------------------------------------

    def show(self):
        self._visible = True
        if self.embedded:
            # A WS_CHILD window is not a Tk toplevel any more, so deiconify()
            # no longer reaches it — drive visibility through the API.
            ctypes.windll.user32.ShowWindow(self._hwnd, _SW_SHOWNOACTIVATE)
            return
        self.win.deiconify()

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

    def redraw_last(self):
        if getattr(self, "_last_render", None):
            self.render(*self._last_render)

    def _blank(self):
        """Wipe to the bare taskbar colour and let it paint. The taskbar never
        recomposites a region a child window vacates, so whatever is on screen
        when we move or quit stays there — leave the taskbar's own colour."""
        if not self.embedded:
            return
        try:
            self.canvas.delete("all")
            self.win.update_idletasks()
        except tk.TclError:
            pass

    def destroy(self):
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
        t = cfg.get("thresholds", {})
        warn = int(t.get("warn", 50))
        danger = int(t.get("danger", 80))

        c = self.canvas
        c.delete("all")

        def sc(v: float) -> int:
            return int(round(v * SCALE))

        pad_x = sc(8)
        gap_icon = sc(6)
        half_w = WIDTH // 2
        bar_h = sc(12)
        row_y_top = sc(6)
        row_y_bot = HEIGHT - row_y_top - bar_h
        cap_w = sc(12)
        pct_w = sc(22)
        time_w = sc(20)
        bar_w = sc(36)
        group_w = cap_w + sc(2) + bar_w + sc(3) + pct_w + sc(2) + time_w
        inter_group_gap = sc(6)

        codex = snap.codex
        claude = snap.claude

        def draw_side(x0: int, icon_obj, fallback_letter: str,
                      pct_a: float | None, pct_b: float | None,
                      reset_a: float | int | None = None,
                      reset_b: float | int | None = None,
                      annotation: str = "",
                      extra_pct: float | None = None,
                      extra_reset: float | int | None = None,
                      extra_caption: str = "",
                      extra_color: str | None = None):
            # icon (or letter fallback)
            ic_y = (HEIGHT - ICON_PX) // 2
            if icon_obj is not None:
                c.create_image(x0 + ICON_PX / 2, ic_y + ICON_PX / 2, image=icon_obj)
            else:
                c.create_rectangle(x0, ic_y, x0 + ICON_PX, ic_y + ICON_PX,
                                   fill=self._adj("#2a2a32"),
                                   outline=self._adj("#454552"))
                c.create_text(x0 + ICON_PX / 2, ic_y + ICON_PX / 2,
                              text=fallback_letter, fill=self._adj(FG_LABEL),
                              font=("Segoe UI Semibold", 13))

            # annotation (small asterisk for estimate mode, etc.) over the icon
            if annotation:
                c.create_text(x0 + ICON_PX - sc(3), ic_y + sc(2),
                              text=annotation, fill=self._adj(COLOR_WARN),
                              font=("Segoe UI Semibold", 10),
                              anchor="ne")

            bx = x0 + ICON_PX + gap_icon

            def draw_group(gx: int, y: int, caption: str,
                           pct: float | None, reset_epoch: float | int | None,
                           fill_color: str | None = None):
                c.create_text(gx + cap_w / 2, y + bar_h / 2,
                              text=caption, fill=self._adj(FG_DIM),
                              font=("Segoe UI", 8))
                bxx = gx + cap_w + sc(2)
                c.create_rectangle(bxx, y, bxx + bar_w, y + bar_h,
                                   fill=self._adj(BG_BAR), outline="")
                if pct is not None:
                    fw = int(bar_w * max(0, min(100, pct)) / 100)
                    if fw > 0:
                        color = fill_color if fill_color else _pick_color(pct, warn, danger)
                        c.create_rectangle(
                            bxx, y, bxx + fw, y + bar_h,
                            fill=self._adj(color), outline="",
                        )
                txt = "—" if pct is None else f"{pct:.0f}%"
                c.create_text(bxx + bar_w + sc(3), y + bar_h / 2,
                              text=txt, anchor="w",
                              fill=self._adj(FG_LABEL),
                              font=("Segoe UI Semibold", 9))
                cd = _format_countdown(reset_epoch)
                if cd:
                    c.create_text(bxx + bar_w + sc(3) + pct_w + sc(2), y + bar_h / 2,
                                  text=cd, anchor="w",
                                  fill=self._adj(FG_DIM), font=("Segoe UI", 9))

            draw_group(bx, row_y_top, "5h", pct_a, reset_a)
            if extra_pct is not None:
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
        if getattr(claude, "needs_login", False):
            # Never fail silently: make the dead-auth state visible.
            claude_anno = "!"
            if not fable_on:
                fable_pct = 0.0
                fable_reset = None
        draw_side(pad_x, self._icon_claude, "C", claude_5h, claude_7d,
                  claude_r5, claude_r7, claude_anno,
                  extra_pct=fable_pct, extra_reset=fable_reset,
                  extra_caption="F", extra_color=COLOR_FABLE)

        mid_x = half_w
        c.create_line(mid_x, sc(6), mid_x, HEIGHT - sc(6),
                      fill=self._adj("#33333a"))

        codex_5h = codex.primary_pct if (codex.available and codex.has_primary) else None
        codex_7d = codex.secondary_pct if (codex.available and codex.has_secondary) else None
        codex_r5 = codex.primary_resets_at if (codex.available and codex.has_primary) else None
        codex_r7 = codex.secondary_resets_at if (codex.available and codex.has_secondary) else None
        draw_side(half_w + pad_x, self._icon_codex, "X", codex_5h, codex_7d,
                  codex_r5, codex_r7)
