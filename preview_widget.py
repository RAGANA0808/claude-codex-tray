"""PIL-only preview mirroring the live widget layout."""
from PIL import Image, ImageDraw, ImageFont
from pathlib import Path
import time as _t
import config
import parsers
import taskbar_widget as tw

cfg = config.load()
snap = parsers.collect_all(cfg)

W, H = tw.WIDTH, tw.HEIGHT
img = Image.new("RGB", (W, H), tw.BG)
d = ImageDraw.Draw(img)


def font(size, bold=False):
    try:
        return ImageFont.truetype("seguisb.ttf" if bold else "segoeui.ttf", size)
    except OSError:
        return ImageFont.load_default()


WARN = int(cfg.get("thresholds", {}).get("warn", 50))
DANGER = int(cfg.get("thresholds", {}).get("danger", 80))


def color(pct):
    if pct is None:
        return tw.COLOR_NA
    if pct >= DANGER:
        return tw.COLOR_DANGER
    if pct >= WARN:
        return tw.COLOR_WARN
    return tw.COLOR_OK


HERE = Path(__file__).resolve().parent


def load_icon(name, px):
    p = HERE / f"app-{name}.png"
    if not p.exists():
        return None
    ic = Image.open(p).convert("RGBA")
    if ic.size != (px, px):
        ic = ic.resize((px, px), Image.LANCZOS)
    return ic


icon_claude = load_icon("claude", tw.ICON_PX)
icon_codex = load_icon("codex", tw.ICON_PX)

pad_x = 8
gap_icon = 6
half_w = W // 2
bar_h = 12
row_y_top = 6
row_y_bot = H - row_y_top - bar_h

cap_w = 12
pct_w = 22
time_w = 20
bar_w = 36
group_w = cap_w + 2 + bar_w + 3 + pct_w + 2 + time_w
inter_group_gap = 6


def cd(reset):
    if not reset:
        return ""
    r = int(float(reset) - _t.time())
    if r <= 0:
        return "now"
    if r >= 86400:
        return f"{r//86400}d"
    if r >= 3600:
        return f"{r//3600}h"
    if r >= 60:
        return f"{r//60}m"
    return "<1m"


def draw_group(gx, y, cap, pct, r, fill=None):
    d.text((gx + 2, y + 1), cap, font=font(9), fill=tw.FG_DIM)
    bxx = gx + cap_w + 2
    d.rectangle((bxx, y, bxx + bar_w, y + bar_h), fill=tw.BG_BAR)
    if pct is not None:
        fw = int(bar_w * max(0, min(100, pct)) / 100)
        if fw > 0:
            fc = fill if fill else color(pct)
            d.rectangle((bxx, y, bxx + fw, y + bar_h), fill=fc)
    txt = "-" if pct is None else f"{pct:.0f}%"
    d.text((bxx + bar_w + 3, y), txt, font=font(10, True), fill=tw.FG_LABEL)
    cds = cd(r)
    if cds:
        d.text((bxx + bar_w + 3 + pct_w + 2, y + 1), cds, font=font(9), fill=tw.FG_DIM)


def draw_side(x0, ic, fallback, p5h, p7d, r5h, r7d,
              extra_pct=None, extra_reset=None, extra_cap="", extra_color=None,
              anno=""):
    ic_y = (H - tw.ICON_PX) // 2
    if ic is not None:
        img.paste(ic, (x0, ic_y), ic)
    else:
        d.rectangle((x0, ic_y, x0 + tw.ICON_PX, ic_y + tw.ICON_PX),
                    fill="#2a2a32", outline="#454552")
        d.text((x0 + 8, ic_y + 4), fallback, font=font(15, True), fill=tw.FG_LABEL)
    if anno:
        d.text((x0 + tw.ICON_PX - 8, ic_y - 2), anno, font=font(13, True), fill=tw.COLOR_WARN)
    bx = x0 + tw.ICON_PX + gap_icon

    draw_group(bx, row_y_top, "5h", p5h, r5h)
    if extra_pct is not None:
        draw_group(bx + group_w + inter_group_gap, row_y_top,
                   extra_cap or "F", extra_pct, extra_reset, fill=extra_color)
    draw_group(bx, row_y_bot, "7d", p7d, r7d)


# Left: Claude
c = snap.claude
c5, c7 = (c.pct_5h, c.pct_7d) if c.available else (None, None)
cr5, cr7 = (c.block_resets_at, c.week_resets_at) if c.available else (None, None)
claude_anno = "*" if (c.available and c.note.startswith("estimate")) else ""
fable_on = c.available and getattr(c, "fable_available", False)
draw_side(pad_x, icon_claude, "C", c5, c7, cr5, cr7,
          extra_pct=(c.fable_pct if fable_on else None),
          extra_reset=(c.fable_resets_at if fable_on else None),
          extra_cap="F", extra_color=tw.COLOR_FABLE,
          anno=claude_anno)

d.line((half_w, 6, half_w, H - 6), fill="#33333a")

# Right: Codex
cx = snap.codex
x5 = cx.primary_pct if (cx.available and cx.has_primary) else None
x7 = cx.secondary_pct if (cx.available and cx.has_secondary) else None
xr5 = cx.primary_resets_at if (cx.available and cx.has_primary) else None
xr7 = cx.secondary_resets_at if (cx.available and cx.has_secondary) else None
draw_side(half_w + pad_x, icon_codex, "X", x5, x7, xr5, xr7)

img.save("preview-widget.png")
canvas = Image.new("RGB", (W + 240, 110), (12, 12, 14))
canvas.paste(img, ((canvas.width - W) // 2, (canvas.height - H) // 2))
canvas.save("preview-widget-on-taskbar.png")
print("saved preview-widget.png, preview-widget-on-taskbar.png")
