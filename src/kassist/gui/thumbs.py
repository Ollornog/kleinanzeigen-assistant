"""Small cached copies of the item photos for the GUI (the originals stay untouched).

Photos are often several MB and may sit on a cloud mount; the GUI shows 160-480 px copies,
made once and kept under data_dir/thumbs, keyed by path, size and modification time.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

from PIL import Image, ImageOps

SIZES = (160, 480)


def thumb(source: Path, size: int, cache: Path) -> Path:
    size = min(SIZES, key=lambda s: abs(s - size))
    stat = source.stat()
    key = hashlib.sha1(f"{source.resolve()}|{stat.st_mtime_ns}|{stat.st_size}|{size}".encode()).hexdigest()
    target = cache / f"{key}.jpg"
    if target.is_file():
        return target
    cache.mkdir(parents=True, exist_ok=True)
    with Image.open(source) as img:
        img = ImageOps.exif_transpose(img)
        img.thumbnail((size, size))
        tmp = target.with_suffix(".tmp")
        img.convert("RGB").save(tmp, "JPEG", quality=82, optimize=True)
    tmp.replace(target)
    return target
