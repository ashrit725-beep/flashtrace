"""Byte-level sniffers for animated images, exercised with hand-built files (no browser)."""

from __future__ import annotations

import struct
import zlib

from capture.detect import (
    SNIFF_LIMIT,
    is_animated_gif,
    is_animated_png,
    is_animated_webp,
    sniff_animated_image,
)

# ----------------------------------------------------------------------------- GIF builders
GIF_HEADER = b"GIF89a" + struct.pack("<HH", 1, 1) + bytes([0x80, 0x00, 0x00])  # 2-entry global table
GIF_GLOBAL_TABLE = bytes([0, 0, 0, 255, 255, 255])
GIF_NETSCAPE = b"\x21\xFF\x0BNETSCAPE2.0\x03\x01\x00\x00\x00"
GIF_GRAPHIC_CONTROL = b"\x21\xF9\x04\x00\x0A\x00\x00\x00"  # 100 ms delay
GIF_TRAILER = b"\x3B"


def gif_frame(pixel_data: bytes = b"\x44\x01") -> bytes:
    """One image descriptor (1x1, no local table) followed by LZW min code size and sub-blocks."""
    descriptor = b"\x2C" + struct.pack("<HHHH", 0, 0, 1, 1) + b"\x00"
    sub_blocks = bytes([len(pixel_data)]) + pixel_data + b"\x00"
    return descriptor + b"\x02" + sub_blocks


def build_gif(frames: int, netscape: bool = False, pixel_data: bytes = b"\x44\x01") -> bytes:
    body = GIF_HEADER + GIF_GLOBAL_TABLE
    if netscape:
        body += GIF_NETSCAPE
    for _ in range(frames):
        body += GIF_GRAPHIC_CONTROL + gif_frame(pixel_data)
    return body + GIF_TRAILER


# ----------------------------------------------------------------------------- PNG builders
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def png_chunk(chunk_type: bytes, data: bytes) -> bytes:
    crc = zlib.crc32(chunk_type + data) & 0xFFFFFFFF
    return struct.pack(">I", len(data)) + chunk_type + data + struct.pack(">I", crc)


IHDR = png_chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
IDAT = png_chunk(b"IDAT", zlib.compress(b"\x00\x00\x00\x00"))
IEND = png_chunk(b"IEND", b"")
ACTL = png_chunk(b"acTL", struct.pack(">II", 2, 0))  # 2 frames, loop forever


# ----------------------------------------------------------------------------- WebP builders
def webp_chunk(fourcc: bytes, payload: bytes) -> bytes:
    return fourcc + struct.pack("<I", len(payload)) + payload + (b"\x00" if len(payload) % 2 else b"")


def build_webp(*chunks: bytes) -> bytes:
    body = b"WEBP" + b"".join(chunks)
    return b"RIFF" + struct.pack("<I", len(body)) + body


VP8X_STILL = webp_chunk(b"VP8X", bytes([0x00]) + b"\x00" * 9)
VP8X_ANIMATED = webp_chunk(b"VP8X", bytes([0x02]) + b"\x00" * 9)  # bit 1 = animation flag
ANIM = webp_chunk(b"ANIM", b"\x00" * 6)
VP8_LOSSY = webp_chunk(b"VP8 ", b"\x00" * 10)


# ----------------------------------------------------------------------------- tests: GIF
def test_single_frame_gif_is_static() -> None:
    assert is_animated_gif(build_gif(frames=1)) is False


def test_two_frame_gif_is_animated_without_netscape_block() -> None:
    assert is_animated_gif(build_gif(frames=2)) is True


def test_netscape_loop_block_marks_gif_animated() -> None:
    assert is_animated_gif(build_gif(frames=1, netscape=True)) is True


def test_image_separator_bytes_inside_pixel_data_do_not_count() -> None:
    # The structural walk skips LZW sub-blocks, so 0x2C bytes in pixel data are not "frames".
    assert is_animated_gif(build_gif(frames=1, pixel_data=b"\x2C\x2C\x2C\x2C")) is False


def test_gif_with_local_colour_table_still_parses() -> None:
    local_table_frame = (
        b"\x2C" + struct.pack("<HHHH", 0, 0, 1, 1) + b"\x80" + bytes([0, 0, 0, 255, 255, 255])
        + b"\x02" + b"\x02\x44\x01\x00"
    )
    one = GIF_HEADER + GIF_GLOBAL_TABLE + local_table_frame + GIF_TRAILER
    two = GIF_HEADER + GIF_GLOBAL_TABLE + local_table_frame + local_table_frame + GIF_TRAILER
    assert is_animated_gif(one) is False
    assert is_animated_gif(two) is True


def test_truncated_or_foreign_bytes_are_not_gifs() -> None:
    assert is_animated_gif(b"GIF8") is False
    assert is_animated_gif(b"") is False
    assert is_animated_gif(b"not an image at all") is False
    assert is_animated_gif(b"GIF89a" + b"\x2C" * 100) is False  # junk after the header


# ----------------------------------------------------------------------------- tests: PNG
def test_plain_png_is_static() -> None:
    assert is_animated_png(PNG_SIGNATURE + IHDR + IDAT + IEND) is False


def test_apng_with_actl_chunk_is_animated() -> None:
    assert is_animated_png(PNG_SIGNATURE + IHDR + ACTL + IDAT + IEND) is True


def test_actl_bytes_inside_idat_do_not_count() -> None:
    fake_idat = png_chunk(b"IDAT", b"acTL" * 4)
    assert is_animated_png(PNG_SIGNATURE + IHDR + fake_idat + IEND) is False


def test_png_requires_signature() -> None:
    assert is_animated_png(IHDR + ACTL) is False


# ----------------------------------------------------------------------------- tests: WebP
def test_still_webp_is_static() -> None:
    assert is_animated_webp(build_webp(VP8_LOSSY)) is False
    assert is_animated_webp(build_webp(VP8X_STILL, VP8_LOSSY)) is False


def test_webp_anim_chunk_is_animated() -> None:
    assert is_animated_webp(build_webp(VP8X_STILL, ANIM)) is True


def test_webp_vp8x_animation_flag_is_animated() -> None:
    assert is_animated_webp(build_webp(VP8X_ANIMATED)) is True


def test_webp_requires_riff_and_webp_markers() -> None:
    assert is_animated_webp(b"RIFF\x00\x00\x00\x00WAVE" + ANIM) is False
    assert is_animated_webp(b"") is False


# ----------------------------------------------------------------------------- tests: dispatcher
def test_sniff_labels() -> None:
    assert sniff_animated_image(build_gif(frames=2)) == "animated-gif"
    assert sniff_animated_image(PNG_SIGNATURE + IHDR + ACTL + IDAT + IEND) == "animated-png"
    assert sniff_animated_image(build_webp(VP8X_STILL, ANIM)) == "animated-webp"
    assert sniff_animated_image(build_gif(frames=1)) is None
    assert sniff_animated_image(b"\xff\xd8\xff\xe0JFIF") is None  # JPEG: never animated


def test_only_the_head_is_inspected() -> None:
    # A second frame that starts beyond SNIFF_LIMIT is out of reach by design (documented limitation);
    # the function must still return cleanly rather than scan the whole blob.
    huge_pixels = b"\x01" * 200
    body = GIF_HEADER + GIF_GLOBAL_TABLE
    while len(body) < SNIFF_LIMIT + 1000:
        body += GIF_GRAPHIC_CONTROL + b"\x2C" + struct.pack("<HHHH", 0, 0, 1, 1) + b"\x00" + b"\x02"
        body += bytes([len(huge_pixels)]) + huge_pixels + b"\x00"
        break  # one oversized frame only
    body += b"\xff" * (SNIFF_LIMIT + 1000 - len(body))
    assert isinstance(is_animated_gif(body), bool)
