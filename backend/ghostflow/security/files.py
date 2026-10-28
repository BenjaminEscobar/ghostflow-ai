"""Bounded local artifact access. Every untrusted path component is opened without symlinks.

Directory descriptors pin the checked directories; replacing a name after validation cannot
redirect a later read, write or retention operation outside the configured artifact root.
"""

import os
import stat
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def artifact_dir(root: Path, *parts: str, create: bool = False):
    root = Path(root)
    if create:
        root.mkdir(parents=True, exist_ok=True)
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in parts:
            if not part or part in {".", ".."} or "/" in part or "\\" in part:
                raise ValueError("Invalid artifact path")
            if create:
                try:
                    os.mkdir(part, mode=0o700, dir_fd=fd)
                except FileExistsError:
                    pass
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        yield fd
    finally:
        os.close(fd)


def read_artifact(root: Path, parts: tuple[str, ...], limit: int) -> bytes:
    with artifact_dir(root, *parts[:-1]) as parent:
        fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        with os.fdopen(fd, "rb") as fh:
            info = os.fstat(fh.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > limit:
                raise ValueError("Invalid or oversized artifact")
            data = fh.read(limit + 1)
            if len(data) > limit:
                raise ValueError("Oversized artifact")
            return data


def jpeg_dimensions(data: bytes) -> tuple[int, int]:
    """Read JPEG frame dimensions without decoding attacker-controlled pixels."""
    if not data.startswith(b"\xff\xd8\xff"):
        raise ValueError("Not a JPEG")
    offset = 2
    while offset < len(data):
        if data[offset] != 0xFF:
            break
        while offset < len(data) and data[offset] == 0xFF:
            offset += 1
        if offset >= len(data):
            break
        marker = data[offset]
        offset += 1
        if marker in {0xD9, 0xDA}:  # end or pixel scan before a frame header
            break
        if marker in {0x01, *range(0xD0, 0xD9)}:
            continue
        if offset + 2 > len(data):
            break
        size = int.from_bytes(data[offset:offset + 2], "big")
        if size < 2 or offset + size > len(data):
            break
        if marker in {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}:
            if size < 8:
                break
            height = int.from_bytes(data[offset + 3:offset + 5], "big")
            width = int.from_bytes(data[offset + 5:offset + 7], "big")
            if width and height:
                return width, height
            break
        offset += size
    raise ValueError("Missing JPEG frame dimensions")
