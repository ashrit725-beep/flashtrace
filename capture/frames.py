"""Frame storage for captures.

A capture of 10 s at 60 fps and 1024x768 is 600 frames of 2.36 MB each (1.4 GB raw), far too much to
hold in memory. ``FrameSequence`` therefore keeps frames as PNG files named ``000000.png``,
``000001.png``, ... inside one directory and decodes one frame at a time on demand. Tests can also
build a sequence from a plain list of arrays so they do not need a browser or a temp directory.
"""

from __future__ import annotations

import io
import re
from collections.abc import Iterator, Sequence
from pathlib import Path

import numpy as np
from PIL import Image

# Frame files are named with a fixed six-digit index so that a plain sorted directory listing is the
# frame order (000000.png < 000001.png < ... < 999999.png).
FRAME_NAME_PATTERN = re.compile(r"^\d{6}\.png$")


def png_to_rgb(png_bytes: bytes) -> np.ndarray:
    """Decode PNG bytes to a ``uint8 [H, W, 3]`` RGB array (alpha, palette, greyscale all converted)."""
    with Image.open(io.BytesIO(png_bytes)) as image:
        return np.array(image.convert("RGB"), dtype=np.uint8)


def rgb_to_png(rgb: np.ndarray) -> bytes:
    """Encode a ``uint8 [H, W, 3]`` RGB array as PNG bytes (lossless, so a round trip is exact)."""
    if rgb.dtype != np.uint8 or rgb.ndim != 3 or rgb.shape[2] != 3:
        raise ValueError(f"expected uint8 [H, W, 3], got {rgb.dtype} {rgb.shape}")
    buffer = io.BytesIO()
    Image.fromarray(np.ascontiguousarray(rgb), mode="RGB").save(buffer, format="PNG")
    return buffer.getvalue()


class FrameSequence:
    """An ordered, index-able collection of RGB frames.

    Two backings exist:

    * **directory** (normal use): ``paths`` lists PNG files; ``__getitem__`` decodes one file, so only
      one frame is in memory at a time while iterating;
    * **in-memory** (tests): ``arrays`` holds the frames directly and ``paths`` is empty.
    """

    def __init__(
        self,
        directory: str | Path | None = None,
        *,
        arrays: Sequence[np.ndarray] | None = None,
    ) -> None:
        if (directory is None) == (arrays is None):
            raise ValueError("pass exactly one of 'directory' or 'arrays'")
        self._dir: Path | None = Path(directory) if directory is not None else None
        self._arrays: list[np.ndarray] = [np.asarray(a, dtype=np.uint8) for a in arrays] if arrays is not None else []
        self._paths: list[Path] = []
        if self._dir is not None:
            self._dir.mkdir(parents=True, exist_ok=True)
            # Pick up frames already on disk (re-opening an earlier capture) in index order.
            self._paths = sorted(p for p in self._dir.iterdir() if FRAME_NAME_PATTERN.match(p.name))

    # ------------------------------------------------------------------ construction helpers
    @classmethod
    def new_in_dir(cls, directory: str | Path) -> "FrameSequence":
        """Start an empty sequence in ``directory``, deleting frame files left by an earlier run.

        Only files matching the ``NNNNNN.png`` pattern are removed, so a caller's other files survive.
        """
        path = Path(directory)
        path.mkdir(parents=True, exist_ok=True)
        for stale in path.iterdir():
            if FRAME_NAME_PATTERN.match(stale.name):
                stale.unlink()
        return cls(path)

    @classmethod
    def from_arrays(cls, arrays: Sequence[np.ndarray]) -> "FrameSequence":
        """In-memory sequence (tests and small synthetic inputs)."""
        return cls(arrays=arrays)

    @classmethod
    def write_arrays(cls, arrays: Sequence[np.ndarray], directory: str | Path) -> "FrameSequence":
        """Encode ``arrays`` as PNG files in ``directory`` and return the directory-backed sequence."""
        sequence = cls.new_in_dir(directory)
        for array in arrays:
            sequence.append_png(rgb_to_png(np.asarray(array, dtype=np.uint8)))
        return sequence

    # ------------------------------------------------------------------ writing
    def append_png(self, png_bytes: bytes) -> Path:
        """Store already-encoded PNG bytes (for example a browser screenshot) as the next frame."""
        if self._dir is None:
            raise RuntimeError("append_png() needs a directory-backed FrameSequence")
        path = self._dir / f"{len(self._paths):06d}.png"
        path.write_bytes(png_bytes)
        self._paths.append(path)
        return path

    def append_array(self, rgb: np.ndarray) -> None:
        """Store a decoded frame (encoded to PNG when directory-backed, kept as-is when in memory)."""
        if self._dir is None:
            self._arrays.append(np.asarray(rgb, dtype=np.uint8))
        else:
            self.append_png(rgb_to_png(rgb))

    # ------------------------------------------------------------------ reading
    @property
    def dir(self) -> Path | None:
        """Directory holding the PNG files, or ``None`` for an in-memory sequence."""
        return self._dir

    @property
    def paths(self) -> list[Path]:
        """Frame file paths in order (empty for an in-memory sequence)."""
        return list(self._paths)

    def __len__(self) -> int:
        return len(self._paths) if self._dir is not None else len(self._arrays)

    def __getitem__(self, index: int) -> np.ndarray:
        """Return frame ``index`` as ``uint8 [H, W, 3]``; negative indices count from the end."""
        count = len(self)
        if index < 0:
            index += count
        if not 0 <= index < count:
            raise IndexError(f"frame {index} out of range for {count} frames")
        if self._dir is None:
            return self._arrays[index]
        with Image.open(self._paths[index]) as image:
            return np.array(image.convert("RGB"), dtype=np.uint8)

    def __iter__(self) -> Iterator[np.ndarray]:
        for index in range(len(self)):
            yield self[index]

    @property
    def shape(self) -> tuple[int, int]:
        """``(height, width)`` of the first frame; ``(0, 0)`` when empty."""
        if len(self) == 0:
            return (0, 0)
        first = self[0]
        return (int(first.shape[0]), int(first.shape[1]))

    def __repr__(self) -> str:
        where = str(self._dir) if self._dir is not None else "memory"
        return f"FrameSequence({len(self)} frames, {where})"
