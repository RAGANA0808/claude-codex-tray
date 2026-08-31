"""Dynamic taskbar icon. Two horizontal bars: top = Codex, bottom = Claude.
Each bar fills left-to-right; color shifts green -> yellow -> red as % rises.
Small letter ('X' for Codex, 'C' for Claude) is overlaid for at-a-glance reading.
"""
from __future__ import annotations
from PIL import Image, ImageDraw, ImageFont

SIZE = 64  # Windows scales taskbar icons; 64 is sharp on 100%-150% DPI.


def _color_for_pct(pct: float, warn: int = 50, danger: int = 80) -> tuple[int, int, int, int]:
    pct = max(0.0, min(100.0, pct))
    if pct >= danger:
        return (220, 60, 60, 255)
    if pct >= warn:
        return (235, 170, 40, 255)
    return (60, 180, 90, 255)


def _font(size: int) -> ImageFont.ImageFont:
    for name in ("seguisb.ttf", "segoeui.ttf", "arial.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def make_icon(codex_pct: float | None, claude_pct: float | None,
              warn: int = 50, danger: int = 80) -> Image.Image:
    """Render the tray icon. None -> dim gray bar (data not available)."""
    img = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    # Background plate (very dark, rounded)
    d.rounded_rectangle((2, 2, SIZE - 3, SIZE - 3), radius=10,
                        fill=(28, 28, 32, 230), outline=(70, 70, 78, 255), width=1)

    bar_h = 20
    gap = 4
    pad_x = 6
    top_y = 8
    bot_y = top_y + bar_h + gap

    def draw_bar(y: int, pct: float | None, label: str):
        # bar background
        d.rounded_rectangle((pad_x, y, SIZE - pad_x - 1, y + bar_h),
                            radius=5, fill=(50, 50, 56, 255))
        if pct is None:
            color = (110, 110, 118, 255)
            fill_w = (SIZE - 2 * pad_x - 2)
            d.rounded_rectangle((pad_x + 1, y + 1,
                                 pad_x + 1 + fill_w, y + bar_h - 1),
                                radius=4, fill=(70, 70, 76, 255))
        else:
            color = _color_for_pct(pct, warn, danger)
            inner_w = SIZE - 2 * pad_x - 2
            fill_w = int(inner_w * min(100.0, max(0.0, pct)) / 100.0)
            if fill_w > 0:
                d.rounded_rectangle((pad_x + 1, y + 1,
                                     pad_x + 1 + fill_w, y + bar_h - 1),
                                    radius=4, fill=color)
        # label letter
        f = _font(13)
        d.text((pad_x + 4, y + 2), label, font=f, fill=(255, 255, 255, 230))
        # pct text right-aligned
        if pct is not None:
            txt = f"{int(round(pct))}%"
            f2 = _font(12)
            tw = d.textlength(txt, font=f2)
            d.text((SIZE - pad_x - 3 - tw, y + 3), txt, font=f2,
                   fill=(255, 255, 255, 240))

    draw_bar(top_y, codex_pct, "X")
    draw_bar(bot_y, claude_pct, "C")
    return img


def make_icon_from_snapshot(snap, cfg: dict) -> Image.Image:
    """Build icon directly from a parsers.Snapshot, using the *worst* of the two
    windows per tool so the bar tracks whichever is closer to the limit."""
    t = cfg.get("thresholds", {})
    warn = int(t.get("warn", 50))
    danger = int(t.get("danger", 80))

    codex_pct = None
    if snap.codex.available:
        codex_pct = max(snap.codex.primary_pct, snap.codex.secondary_pct)

    claude_pct = None
    if snap.claude.available:
        claude_pct = max(snap.claude.pct_5h, snap.claude.pct_7d)

    return make_icon(codex_pct, claude_pct, warn=warn, danger=danger)
