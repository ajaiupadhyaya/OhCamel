"""``ru_maxrss`` is KiB on Linux and bytes on macOS."""

from __future__ import annotations

import sys


def maxrss_bytes(ru_maxrss: int) -> int:
    return int(ru_maxrss) if sys.platform == "darwin" else int(ru_maxrss) * 1024
