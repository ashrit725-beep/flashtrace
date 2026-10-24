"""FlashTrace capture layer: a web page in, a deterministic sequence of still frames out.

Public interface (fixed; the CLI, server and attribution layers call it)::

    from capture import capture, CaptureResult, CaptureMeta, FrameSequence

See docs/SPEC.md section 6 for the strategies and ``capture/capturer.py`` for the entry point.
"""

from capture.capturer import CaptureMeta, CaptureResult, capture, resolve_url
from capture.frames import FrameSequence
from capture.hashing import frame_hash, sequence_hash

__all__ = [
    "CaptureMeta",
    "CaptureResult",
    "FrameSequence",
    "capture",
    "frame_hash",
    "resolve_url",
    "sequence_hash",
]
