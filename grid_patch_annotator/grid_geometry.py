"""ColQwen3 uniform patch grid geometry (same as build_vidore2_patch_annotations.patch_box)."""

from __future__ import annotations

import math

IMAGE_EXTENSIONS = {".bmp", ".gif", ".jpeg", ".jpg", ".png", ".tif", ".tiff", ".webp"}


def patch_box(width: int, height: int, row: int, col: int, rows: int, cols: int) -> tuple[int, int, int, int]:
    left = math.floor(col * width / cols)
    right = math.floor((col + 1) * width / cols)
    upper = math.floor(row * height / rows)
    lower = math.floor((row + 1) * height / rows)
    return left, upper, max(left + 1, right), max(upper + 1, lower)
