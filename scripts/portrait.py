#!/usr/bin/env python3
"""Turn a photo into the ASCII portrait the man page shows, for the CRT's dark screen.

Usage: scripts/portrait.py <image> <columns> > portrait.txt      (needs ImageMagick's magick)

Dark areas of the photo become dense characters and the light background disappears, like ink on
paper, which reads as a face at a few dozen columns where a photographic mapping turns the
background into noise. The rows follow the screen's cell shape (9.6 by 22 pixels).
"""
from __future__ import annotations

import subprocess
import sys

CELL_ASPECT = 9.6 / 22
RAMP = " .:-=+*#%@"
BACKGROUND_CUT = 0.37
GAMMA = 0.9


def to_ascii(gray: bytes, cols: int, rows: int, cut: float = BACKGROUND_CUT, gamma: float = GAMMA) -> list[str]:
    """Map a row-major 8-bit grayscale image to text: the darker a cell, the denser its character."""
    if len(gray) != cols * rows:
        raise ValueError(f"expected {cols * rows} gray bytes for {cols}x{rows}, got {len(gray)}")
    lines = []
    for r in range(rows):
        line = ""
        for c in range(cols):
            darkness = 1 - gray[r * cols + c] / 255
            if darkness < cut:
                line += " "
                continue
            t = ((darkness - cut) / (1 - cut)) ** gamma
            line += RAMP[min(int(t * len(RAMP)), len(RAMP) - 1)]
        lines.append(line.rstrip())
    return lines


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(__doc__, file=sys.stderr)
        return 2
    source, cols = argv[1], int(argv[2])
    rows = round(cols * CELL_ASPECT)
    gray = subprocess.run(
        ["magick", source, "-colorspace", "Gray", "-resize", f"{cols}x{rows}!", "-depth", "8", "gray:-"],
        check=True, capture_output=True,
    ).stdout
    print("\n".join(to_ascii(gray, cols, rows)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
