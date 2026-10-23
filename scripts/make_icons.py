#!/usr/bin/env python3
"""Render the permitA38 app icons (web/icons/*.png) from the clock mark of web/icons/icon.svg.

Standard library only (own tiny PNG writer, 3x3 supersampling), so the icons can be rebuilt anywhere:

    python scripts/make_icons.py

The geometry below is the SVG's geometry; keep both in sync when changing the mark.
"""

import struct
import sys
import zlib
from pathlib import Path

ACCENT = (0x25, 0x63, 0xEB)
WHITE = (0xFF, 0xFF, 0xFF)
# the clock hands, in the 32x32 viewBox of icon.svg: polyline with round caps/joins
HANDS = [(16.0, 8.0), (16.0, 16.0), (21.0, 19.0)]
HAND_RADIUS = 1.5  # half of stroke-width 3
SS = 3  # supersampling per axis


def _seg_dist2(px: float, py: float, a: tuple[float, float], b: tuple[float, float]) -> float:
    ax, ay = a
    bx, by = b
    dx, dy = bx - ax, by - ay
    t = ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)
    t = max(0.0, min(1.0, t))
    qx, qy = ax + t * dx, ay + t * dy
    return (px - qx) ** 2 + (py - qy) ** 2


def _on_hands(x: float, y: float, scale: float, center: bool) -> bool:
    """x, y in viewBox units; the mark is scaled around the centre (16, 16).

    center=True also moves the hands' bounding box (centre 18.5, 13.5) onto the canvas centre (maskable icons)."""
    dx, dy = (2.5, -2.5) if center else (0.0, 0.0)
    px, py = 16 + (x - 16) / scale + dx, 16 + (y - 16) / scale + dy
    r2 = HAND_RADIUS**2
    return any(_seg_dist2(px, py, HANDS[i], HANDS[i + 1]) <= r2 for i in range(len(HANDS) - 1))


def render(size: int, *, maskable: bool = False, opaque: bool = False) -> bytes:
    """RGBA pixels. regular: blue disc on transparent; maskable/opaque: full-bleed blue square (the OS masks it)."""
    scale = 1.3 if maskable else 1.0  # maskable keeps the mark inside the 80 % safe zone
    unit = 32.0 / size
    rows = bytearray()
    for j in range(size):
        rows.append(0)  # PNG filter: none
        for i in range(size):
            bg = hands = 0
            for sj in range(SS):
                for si in range(SS):
                    x = (i + (si + 0.5) / SS) * unit
                    y = (j + (sj + 0.5) / SS) * unit
                    inside = True if (maskable or opaque) else (x - 16) ** 2 + (y - 16) ** 2 <= 14.0**2
                    if inside:
                        bg += 1
                        if _on_hands(x, y, scale, maskable):
                            hands += 1
            n = SS * SS
            if not bg:
                rows += b"\x00\x00\x00\x00"
                continue
            w = hands / bg  # share of white inside the covered samples
            r = round(ACCENT[0] + (WHITE[0] - ACCENT[0]) * w)
            g = round(ACCENT[1] + (WHITE[1] - ACCENT[1]) * w)
            b = round(ACCENT[2] + (WHITE[2] - ACCENT[2]) * w)
            rows += bytes((r, g, b, round(255 * bg / n)))
    return bytes(rows)


def png(size: int, pixels: bytes) -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)

    header = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)  # 8-bit RGBA
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(pixels, 9)) + chunk(b"IEND", b"")


ICONS = {
    "icon-192.png": dict(size=192),
    "icon-512.png": dict(size=512),
    "icon-maskable-192.png": dict(size=192, maskable=True),
    "icon-maskable-512.png": dict(size=512, maskable=True),
    "apple-touch-icon.png": dict(size=180, opaque=True),
}


def main(out: Path) -> int:
    out.mkdir(parents=True, exist_ok=True)
    for name, spec in ICONS.items():
        size = spec["size"]
        data = png(size, render(**spec))  # type: ignore[arg-type]
        (out / name).write_bytes(data)
        print(f"{out / name}  {size}x{size}  {len(data)} bytes")
    return 0


if __name__ == "__main__":
    root = Path(__file__).resolve().parents[1]
    sys.exit(main(Path(sys.argv[1]) if len(sys.argv) > 1 else root / "web" / "icons"))
