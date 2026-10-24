"""Content hashes for captured frames (SPEC.md section 6.1).

Why hash decoded pixels and not PNG files: two PNG encoders (or two versions of the same encoder)
can produce different bytes for identical pixels. The report must say whether two captures showed the
same thing, so the hash is taken over the raw RGB bytes after decoding.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence

import numpy as np


def frame_hash(rgb: np.ndarray) -> str:
    """Return the sha256 hex digest of a frame's raw RGB bytes.

    ``rgb`` must be ``uint8`` with shape ``[H, W, 3]`` (R, G, B order). The bytes are hashed in
    row-major order, so the digest is independent of how the array was produced or stored.
    """
    if rgb.dtype != np.uint8:
        raise ValueError(f"frame must be uint8, got {rgb.dtype}")
    if rgb.ndim != 3 or rgb.shape[2] != 3:
        raise ValueError(f"frame must have shape [H, W, 3], got {rgb.shape}")
    # ascontiguousarray makes sure tobytes() walks the pixels in plain row-major order even when
    # the caller hands us a view (for example a cropped or transposed array).
    return hashlib.sha256(np.ascontiguousarray(rgb).tobytes()).hexdigest()


def sequence_hash(frame_hashes: Sequence[str]) -> str:
    """Return the sha256 hex digest of all frame hashes concatenated, in order.

    SPEC 6.1: ``hash = sha256(concatenation of all frameHashes)``. The hex strings are joined with no
    separator and hashed as ASCII, so the result depends on the frames *and* their order.
    """
    joined = "".join(frame_hashes).encode("ascii")
    return hashlib.sha256(joined).hexdigest()
