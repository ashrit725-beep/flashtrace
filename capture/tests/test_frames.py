"""FrameSequence round trip and hash stability (no browser needed)."""

from __future__ import annotations

import hashlib
from pathlib import Path

import numpy as np
import pytest

from capture.frames import FrameSequence, png_to_rgb, rgb_to_png
from capture.hashing import frame_hash, sequence_hash


def _three_small_frames() -> list[np.ndarray]:
    """Three 6x8 RGB frames with fixed pseudo-random content (seeded, so every run is identical)."""
    rng = np.random.default_rng(20261024)
    return [rng.integers(0, 256, size=(6, 8, 3), dtype=np.uint8) for _ in range(3)]


def test_png_round_trip_is_exact(tmp_path: Path) -> None:
    frames = _three_small_frames()
    sequence = FrameSequence.write_arrays(frames, tmp_path)

    assert len(sequence) == 3
    assert [p.name for p in sequence.paths] == ["000000.png", "000001.png", "000002.png"]
    assert sequence.dir == tmp_path
    for original, read_back in zip(frames, sequence, strict=True):
        assert read_back.dtype == np.uint8
        assert read_back.shape == (6, 8, 3)
        assert np.array_equal(original, read_back)
    assert sequence.shape == (6, 8)


def test_hashes_are_stable_across_storage(tmp_path: Path) -> None:
    frames = _three_small_frames()
    on_disk = FrameSequence.write_arrays(frames, tmp_path)
    in_memory = FrameSequence.from_arrays(frames)
    reopened = FrameSequence(tmp_path)  # a fresh object reading the same directory

    disk_hashes = [frame_hash(f) for f in on_disk]
    memory_hashes = [frame_hash(f) for f in in_memory]
    reopened_hashes = [frame_hash(f) for f in reopened]
    assert disk_hashes == memory_hashes == reopened_hashes
    # The hash is over raw RGB bytes, not PNG bytes: hashlib on the array agrees with frame_hash.
    assert disk_hashes[0] == hashlib.sha256(frames[0].tobytes()).hexdigest()
    # And it is not the hash of the PNG file.
    assert disk_hashes[0] != hashlib.sha256(on_disk.paths[0].read_bytes()).hexdigest()
    assert len(sequence_hash(disk_hashes)) == 64
    assert sequence_hash(disk_hashes) == hashlib.sha256("".join(disk_hashes).encode()).hexdigest()


def test_sequence_hash_depends_on_order() -> None:
    hashes = [frame_hash(f) for f in _three_small_frames()]
    assert sequence_hash(hashes) != sequence_hash(list(reversed(hashes)))
    assert sequence_hash([]) == hashlib.sha256(b"").hexdigest()


def test_indexing_and_append(tmp_path: Path) -> None:
    frames = _three_small_frames()
    sequence = FrameSequence.new_in_dir(tmp_path)
    assert len(sequence) == 0
    for frame in frames:
        sequence.append_png(rgb_to_png(frame))
    assert np.array_equal(sequence[-1], frames[2])
    assert np.array_equal(sequence[0], frames[0])
    with pytest.raises(IndexError):
        sequence[3]
    # new_in_dir clears stale frame files from an earlier run but nothing else.
    (tmp_path / "keep.txt").write_text("not a frame")
    fresh = FrameSequence.new_in_dir(tmp_path)
    assert len(fresh) == 0
    assert (tmp_path / "keep.txt").exists()
    assert not (tmp_path / "000000.png").exists()


def test_png_decode_converts_rgba_to_rgb() -> None:
    # A 2x2 RGBA PNG decodes to an RGB array with the alpha channel dropped.
    from PIL import Image
    import io

    rgba = np.zeros((2, 2, 4), dtype=np.uint8)
    rgba[..., 0] = 200
    rgba[..., 3] = 255
    buffer = io.BytesIO()
    Image.fromarray(rgba, mode="RGBA").save(buffer, format="PNG")
    rgb = png_to_rgb(buffer.getvalue())
    assert rgb.shape == (2, 2, 3)
    assert rgb[0, 0].tolist() == [200, 0, 0]


def test_frame_hash_rejects_wrong_dtype_or_shape() -> None:
    with pytest.raises(ValueError):
        frame_hash(np.zeros((2, 2, 3), dtype=np.float32))
    with pytest.raises(ValueError):
        frame_hash(np.zeros((2, 2), dtype=np.uint8))
