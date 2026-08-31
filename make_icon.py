"""Regenerate the bundled icon set.

Produces:
  app-claude.png / app-codex.png  — neutral 64px tile icons for the widget
  app-icon.png / app-icon.ico     — the exe + shortcut icon

All artwork is drawn here with Pillow; the repository ships no third-party
logos. To use your own icons locally, drop `app-claude-custom.png` /
`app-codex-custom.png` next to the app (they are git-ignored and take
precedence at runtime).
"""
from PIL import Image, ImageDraw, ImageFont
from pathlib import Path

HERE = Path(__file__).resolve().parent

CLAUDE_COLOR = (217, 119, 87, 255)   # warm coral
CODEX_COLOR = (108, 123, 250, 255)   # periwinkle blue
TILE_BG = (28, 28, 34, 255)
TILE_EDGE = (72, 72, 84, 255)


def _font(px: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for name in ("seguisb.ttf", "segoeuib.ttf", "arialbd.ttf"):
        try:
            return ImageFont.truetype(name, px)
        except Exception:
            continue
    return ImageFont.load_default()


def make_tile(letter: str, accent: tuple, size: int = 64) -> Image.Image:
    """Neutral tile: dark rounded square, accent ring, bold letter."""
    s4 = size * 4  # draw at 4x then downscale for smooth edges
    img = Image.new("RGBA", (s4, s4), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    r = s4 // 5
    d.rounded_rectangle((8, 8, s4 - 9, s4 - 9), radius=r, fill=TILE_BG,
                        outline=accent, width=s4 // 22)
    f = _font(int(s4 * 0.58))
    bbox = d.textbbox((0, 0), letter, font=f)
    w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
    d.text(((s4 - w) / 2 - bbox[0], (s4 - h) / 2 - bbox[1]),
           letter, font=f, fill=accent)
    return img.resize((size, size), Image.LANCZOS)


def make_app_icon() -> Image.Image:
    """Exe icon: dark tile, C/X monogram, two usage bars."""
    SIZE = 256
    img = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((4, 4, SIZE - 5, SIZE - 5), radius=52,
                        fill=(26, 26, 32, 255), outline=TILE_EDGE, width=3)

    f = _font(96)
    for letter, color, x in [("C", CLAUDE_COLOR, 52), ("X", CODEX_COLOR, 140)]:
        bbox = d.textbbox((0, 0), letter, font=f)
        d.text((x, 44 - bbox[1]), letter, font=f, fill=color)

    bar_x0, bar_x1 = 46, SIZE - 46
    for y, frac, color in [(176, 0.72, (63, 191, 106, 255)),
                           (206, 0.42, (155, 124, 214, 255))]:
        d.rounded_rectangle((bar_x0, y, bar_x1, y + 20), radius=10, fill=(56, 56, 66, 255))
        w = int((bar_x1 - bar_x0) * frac)
        if w > 0:
            d.rounded_rectangle((bar_x0, y, bar_x0 + w, y + 20), radius=10, fill=color)
    return img


if __name__ == "__main__":
    make_tile("C", CLAUDE_COLOR).save(HERE / "app-claude.png")
    make_tile("X", CODEX_COLOR).save(HERE / "app-codex.png")
    icon = make_app_icon()
    icon.save(HERE / "app-icon.png")
    icon.save(HERE / "app-icon.ico",
              sizes=[(16, 16), (24, 24), (32, 32), (48, 48),
                     (64, 64), (128, 128), (256, 256)])
    print("saved app-claude.png / app-codex.png / app-icon.png / app-icon.ico")
