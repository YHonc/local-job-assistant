"""Regenerate the Chrome extension icons (16/48/128 px) with the stdlib only.

Design: forest-green rounded square with three white list bars, matching the
popup's --forest palette. Run from the project root:

    .venv/Scripts/python.exe scripts/make_extension_icons.py
"""
from __future__ import annotations

import struct
import zlib
from pathlib import Path

ICONS_DIR = Path(__file__).parents[1] / "extension" / "icons"
BACKGROUND = (23, 107, 77, 255)    # --forest
FOREGROUND = (255, 255, 255, 255)  # white
CORNER_RATIO = 0.18


def _png_chunk(tag: bytes, data: bytes) -> bytes:
    return (
        struct.pack(">I", len(data))
        + tag
        + data
        + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    )


def write_png(path: Path, pixels: list[list[tuple[int, int, int, int]]]) -> None:
    size = len(pixels)
    raw = b"".join(
        b"\x00" + bytes(component for pixel in row for component in pixel)
        for row in pixels
    )
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
        + _png_chunk(b"IDAT", zlib.compress(raw, 9))
        + _png_chunk(b"IEND", b"")
    )


def _inside_rounded_square(x: int, y: int, size: int, radius: int) -> bool:
    margin = max(1, size // 32)
    if not (margin <= x < size - margin and margin <= y < size - margin):
        return False
    inner_left, inner_top = margin + radius, margin + radius
    inner_right, inner_bottom = size - margin - radius - 1, size - margin - radius - 1
    anchor_x = min(max(x, inner_left), inner_right)
    anchor_y = min(max(y, inner_top), inner_bottom)
    return (x - anchor_x) ** 2 + (y - anchor_y) ** 2 <= radius * radius


def _inside_capsule(x: float, y: float, x1: float, x2: float, cy: float, radius: float) -> bool:
    if not (min(x1, x2) - radius <= x <= max(x1, x2) + radius):
        return False
    t = min(max(x, min(x1, x2)), max(x1, x2))
    return (x - t) ** 2 + (y - cy) ** 2 <= radius * radius


def render(size: int) -> list[list[tuple[int, int, int, int]]]:
    radius = max(2, round(size * CORNER_RATIO))
    bar_height = max(2, round(size * 0.11))
    bar_radius = bar_height / 2
    centers = (0.32, 0.5, 0.68)
    widths = (0.56, 0.56, 0.34)
    rows: list[list[tuple[int, int, int, int]]] = []
    for y in range(size):
        row: list[tuple[int, int, int, int]] = []
        for x in range(size):
            pixel = (0, 0, 0, 0)
            if _inside_rounded_square(x, y, size, radius):
                pixel = BACKGROUND
                middle = size / 2
                for center, width in zip(centers, widths, strict=True):
                    cy = size * center
                    half = size * width / 2
                    if _inside_capsule(x + 0.5, y + 0.5, middle - half + bar_radius, middle + half - bar_radius, cy, bar_radius):
                        pixel = FOREGROUND
                        break
            row.append(pixel)
        rows.append(row)
    return rows


def main() -> None:
    ICONS_DIR.mkdir(parents=True, exist_ok=True)
    for size in (16, 48, 128):
        path = ICONS_DIR / f"icon{size}.png"
        write_png(path, render(size))
        print(f"wrote {path} ({path.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
