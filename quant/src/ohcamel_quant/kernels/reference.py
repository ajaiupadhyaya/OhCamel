"""The NumPy reference for every kernel (contract II.4). Each function has the
dispatcher's signature and is the semantic definition the Rust kernel is
tested against. Random kernels draw from np.random.Generator(PCG64(seed)) and
ignore ``threads`` (their output does not depend on it). No I/O."""

from __future__ import annotations

import math

#: Rows per chunk, as in Rust (compute plan A2: 16k).
CHUNK = 16_384


def tail_count(n: int, alpha: float) -> int:
    """risk.core.tail_count, verbatim: ``max(1, ceil(n (1 - alpha) - 1e-9))``."""
    return max(1, math.ceil(n * (1.0 - alpha) - 1e-9))
