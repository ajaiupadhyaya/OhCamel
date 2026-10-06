"""ULIDs: 48 bits of millisecond time and 80 random bits, in 26 Crockford base32 characters.

Lexicographic order is time order, so job ids sort by submission.
"""

from __future__ import annotations

import os
import time

_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def new_ulid(now_ms: int | None = None) -> str:
    ms = int(time.time() * 1000) if now_ms is None else int(now_ms)
    if not 0 <= ms < 2**48:
        raise ValueError(f"ULID timestamp out of range: {ms}")
    n = (ms << 80) | int.from_bytes(os.urandom(10), "big")
    return "".join(_ALPHABET[(n >> (5 * i)) & 31] for i in reversed(range(26)))
