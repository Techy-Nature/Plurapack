"""Draw the pixel-art Plurapack logo and write every icon made from it.

The logo is drawn on a small square grid: four big overlapping circles
(yellow top, pink right, blue bottom, green left) around a small rainbow
circle, with one-pixel black outlines. Each grid cell becomes one square
"pixel" in the SVG, and the PNGs are scaled up from the same grid with
nearest-neighbour sampling so the edges stay crisp.

Run from the repository root:

    python native/pixel_logo.py

It needs Pillow (`pip install pillow`).
"""

from __future__ import annotations

import colorsys
import math
from pathlib import Path

from PIL import Image

GRID = 48
CENTRE = GRID / 2
BIG_RADIUS = GRID * 0.30
BIG_OFFSET = GRID * 0.19
SMALL_RADIUS = GRID * 0.118
OUTLINE = (17, 17, 17, 255)

# Big circles in the order they are tested, with their own colour.
CIRCLES = {
    "Y": (CENTRE, CENTRE - BIG_OFFSET),
    "P": (CENTRE + BIG_OFFSET, CENTRE),
    "B": (CENTRE, CENTRE + BIG_OFFSET),
    "G": (CENTRE - BIG_OFFSET, CENTRE),
}

# Colour of every region, named by the big circles that cover it.
REGION_COLOURS = {
    "Y": "#f4e526",
    "P": "#e6378f",
    "B": "#62b4ec",
    "G": "#27cf78",
    "GY": "#a9cb1f",
    "PY": "#fb8f2c",
    "BG": "#3ec5d6",
    "BP": "#9a45cc",
    "GPY": "#f8c51c",
    "BGY": "#14b23d",
    "BPY": "#d6205a",
    "BGP": "#2471e6",
}

ROOT = Path(__file__).resolve().parent.parent
NATIVE = ROOT / "native"
ICONS = NATIVE / "src-tauri" / "icons"


def hex_rgba(value: str) -> tuple[int, int, int, int]:
    value = value.lstrip("#")
    return int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16), 255


def rainbow(x: float, y: float) -> tuple[int, int, int, int]:
    """Watercolour-ish centre: hue follows the angle, paler near the middle."""
    dx, dy = x - CENTRE, y - CENTRE
    # Yellow at the top, pink right, blue bottom, green left, like the big circles.
    angle = (math.degrees(math.atan2(dx, -dy)) + 360) % 360
    hues = [(0, 55), (90, 330), (180, 205), (270, 140), (360, 55 + 360)]
    for (a0, h0), (a1, h1) in zip(hues, hues[1:]):
        if a0 <= angle <= a1:
            t = (angle - a0) / (a1 - a0)
            if h1 - h0 > 180:
                h0 += 360
            elif h0 - h1 > 180:
                h1 += 360
            hue = (h0 + (h1 - h0) * t) % 360
            break
    distance = math.hypot(dx, dy) / SMALL_RADIUS
    saturation = 0.15 + 0.6 * min(distance, 1)
    r, g, b = colorsys.hsv_to_rgb(hue / 360, saturation, 0.98)
    return round(r * 255), round(g * 255), round(b * 255), 255


def pixel(col: int, row: int) -> tuple[int, int, int, int]:
    x, y = col + 0.5, row + 0.5
    circles = list(CIRCLES.values()) + [(CENTRE, CENTRE)]
    radii = [BIG_RADIUS] * 4 + [SMALL_RADIUS]
    for (cx, cy), radius in zip(circles, radii):
        if abs(math.hypot(x - cx, y - cy) - radius) < 0.5:
            return OUTLINE
    if math.hypot(x - CENTRE, y - CENTRE) < SMALL_RADIUS:
        return rainbow(x, y)
    inside = "".join(sorted(name for name, (cx, cy) in CIRCLES.items()
                            if math.hypot(x - cx, y - cy) < BIG_RADIUS))
    if not inside:
        return (0, 0, 0, 0)
    if len(inside) == 4:
        return rainbow(x, y)
    return hex_rgba(REGION_COLOURS[inside])


def draw_grid() -> Image.Image:
    image = Image.new("RGBA", (GRID, GRID))
    for row in range(GRID):
        for col in range(GRID):
            image.putpixel((col, row), pixel(col, row))
    return image


def to_svg(grid: Image.Image) -> str:
    """One <rect> per horizontal run of same-coloured pixels."""
    lines = [
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 %d %d" width="512" height="512" '
        'shape-rendering="crispEdges">' % (GRID, GRID),
        "<title>Plurapack</title>",
    ]
    for row in range(GRID):
        col = 0
        while col < GRID:
            colour = grid.getpixel((col, row))
            run = 1
            while col + run < GRID and grid.getpixel((col + run, row)) == colour:
                run += 1
            if colour[3]:
                fill = "#%02x%02x%02x" % colour[:3]
                lines.append(f'<rect x="{col}" y="{row}" width="{run}" height="1" fill="{fill}"/>')
            col += run
    lines.append("</svg>")
    return "\n".join(lines) + "\n"


def scaled(grid: Image.Image, size: int, logo_fraction: float = 1.0) -> Image.Image:
    """The logo at `size` pixels, taking up `logo_fraction` of the canvas."""
    logo_size = max(1, round(size * logo_fraction))
    factor = logo_size // GRID
    if factor and GRID * factor >= logo_size * 0.9:
        # Whole-number scale: every logo pixel stays a sharp square.
        big = grid.resize((GRID * factor, GRID * factor), Image.NEAREST)
    else:
        factor = math.ceil(logo_size / GRID)
        big = grid.resize((GRID * factor, GRID * factor), Image.NEAREST)
        big = big.resize((logo_size, logo_size), Image.LANCZOS)
    logo_size = big.size[0]
    canvas = Image.new("RGBA", (size, size))
    offset = (size - logo_size) // 2
    canvas.alpha_composite(big, (offset, offset))
    return canvas


def save(image: Image.Image, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path, optimize=True)
    print("wrote", path.relative_to(ROOT))


def main() -> None:
    grid = draw_grid()
    svg = to_svg(grid)
    for target in (ROOT / "favicon.svg", NATIVE / "icon-source.svg", NATIVE / "launcher" / "logo.svg"):
        target.write_text(svg, encoding="utf-8")
        print("wrote", target.relative_to(ROOT))

    save(scaled(grid, 1024), NATIVE / "icon-source.png")
    save(scaled(grid, 180), ROOT / "apple-touch-icon.png")

    # Tauri desktop and Windows Store sizes.
    desktop = {
        "32x32.png": 32, "64x64.png": 64, "128x128.png": 128, "128x128@2x.png": 256,
        "icon.png": 512, "StoreLogo.png": 50,
        **{f"Square{n}x{n}Logo.png": n for n in (30, 44, 71, 89, 107, 142, 150, 284, 310)},
    }
    for name, size in desktop.items():
        save(scaled(grid, size), ICONS / name)

    ico_sizes = [16, 24, 32, 48, 64, 128, 256]
    ico_master = scaled(grid, 256)
    for target in (ICONS / "icon.ico", ROOT / "favicon.ico"):
        ico_master.save(target, sizes=[(n, n) for n in ico_sizes],
                        append_images=[scaled(grid, n) for n in ico_sizes])
        print("wrote", target.relative_to(ROOT))
    scaled(grid, 1024).save(ICONS / "icon.icns")
    print("wrote", (ICONS / "icon.icns").relative_to(ROOT))

    # Android launcher icons. Adaptive foregrounds keep the logo at about 62%
    # of the canvas, because launchers crop them to a circle or squircle.
    android = {"mdpi": 48, "hdpi": 72, "xhdpi": 96, "xxhdpi": 144, "xxxhdpi": 192}
    for density, size in android.items():
        folder = ICONS / "android" / f"mipmap-{density}"
        save(scaled(grid, size, 0.84), folder / "ic_launcher.png")
        save(scaled(grid, size, 0.92), folder / "ic_launcher_round.png")
        save(scaled(grid, size * 9 // 4, 0.62), folder / "ic_launcher_foreground.png")

    # The MkDocs theme picks up docs/img/favicon.ico on its own.
    (ROOT / "documentation" / "docs" / "img").mkdir(parents=True, exist_ok=True)
    ico_master.save(ROOT / "documentation" / "docs" / "img" / "favicon.ico",
                    sizes=[(16, 16), (32, 32), (48, 48)])
    print("wrote documentation/docs/img/favicon.ico")


if __name__ == "__main__":
    main()
