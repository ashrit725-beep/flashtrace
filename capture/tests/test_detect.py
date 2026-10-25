"""Byte-level sniffers for animated images, exercised with hand-built files (no browser), and the rules
for where image bytes may be read from (a stand-in page object replaces the browser)."""

from __future__ import annotations

import struct
import zlib
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import quote, quote_from_bytes

import pytest

from capture import detect
from capture.detect import (
    GIF_SNIFF_LIMIT,
    SNIFF_LIMIT,
    find_uncontrollable,
    gif_needs_more_bytes,
    is_animated_gif,
    is_animated_png,
    is_animated_webp,
    local_reads_allowed,
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


# ----------------------------------------------------------------------------- tests: SVG
STILL_SVG = b'<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"><rect width="10" height="10"/></svg>'
SMIL_SVG = (b'<?xml version="1.0"?>\n<svg xmlns="http://www.w3.org/2000/svg"><rect width="10" height="10">'
            b'<animate attributeName="fill" values="#000;#fff" dur="0.25s" begin="1.5s" repeatCount="10"/></rect></svg>')
CSS_SVG = (b'<svg xmlns="http://www.w3.org/2000/svg"><style>#r{animation:s .25s steps(1,end) 1.5s 10}'
           b'@keyframes s{50%{fill:#000}}</style><rect id="r" width="10" height="10"/></svg>')


@pytest.mark.parametrize("markup", [SMIL_SVG, CSS_SVG, SMIL_SVG.replace(b"<animate ", b"<set "), b"\xef\xbb\xbf  " + CSS_SVG])
def test_svg_with_smil_or_keyframes_is_animated(markup: bytes) -> None:
    """An SVG image's SMIL and CSS animations run on the browser's own clock (even one that starts late)."""
    assert detect.is_animated_svg(markup) is True
    assert sniff_animated_image(markup) == "animated-svg"


def test_gzipped_svg_is_unzipped_first() -> None:
    packer = zlib.compressobj(wbits=16 + zlib.MAX_WBITS)
    assert detect.is_animated_svg(packer.compress(SMIL_SVG) + packer.flush()) is True


@pytest.mark.parametrize(
    "data",
    [
        STILL_SVG,
        b"<!doctype html><html><body><svg><animate/></svg></body></html>",  # HTML with inline SVG is not an SVG image
        b"<svg><settings-panel/></svg>",                                     # '<set' must be the whole element name
        b"GIF89a<svg><animate/>",                                            # not markup at all
    ],
)
def test_still_or_non_svg_bytes_are_not_animated_svg(data: bytes) -> None:
    assert detect.is_animated_svg(data) is False


def test_long_svg_without_animation_so_far_asks_for_more_bytes() -> None:
    head = (STILL_SVG[:-6] + b"<!--" + b"x" * SNIFF_LIMIT)[:SNIFF_LIMIT]
    assert detect.svg_needs_more_bytes(head, SNIFF_LIMIT) is True
    assert detect.svg_needs_more_bytes(STILL_SVG, SNIFF_LIMIT) is False   # the whole file
    assert detect.svg_needs_more_bytes((SMIL_SVG + b" " * SNIFF_LIMIT)[:SNIFF_LIMIT], SNIFF_LIMIT) is False  # decided


# ----------------------------------------------------------------------------- tests: dispatcher
def test_sniff_labels() -> None:
    assert sniff_animated_image(build_gif(frames=2)) == "animated-gif"
    assert sniff_animated_image(PNG_SIGNATURE + IHDR + ACTL + IDAT + IEND) == "animated-png"
    assert sniff_animated_image(build_webp(VP8X_STILL, ANIM)) == "animated-webp"
    assert sniff_animated_image(build_gif(frames=1)) is None
    assert sniff_animated_image(b"\xff\xd8\xff\xe0JFIF") is None  # JPEG: never animated


def test_unknown_bytes_after_a_large_frame_end_the_walk() -> None:
    # One oversized frame followed by bytes that are no GIF block: the walk stops cleanly ("still")
    # instead of guessing, and does not ask for more bytes.
    huge_pixels = b"\x01" * 200
    body = GIF_HEADER + GIF_GLOBAL_TABLE
    while len(body) < SNIFF_LIMIT + 1000:
        body += GIF_GRAPHIC_CONTROL + b"\x2C" + struct.pack("<HHHH", 0, 0, 1, 1) + b"\x00" + b"\x02"
        body += bytes([len(huge_pixels)]) + huge_pixels + b"\x00"
        break  # one oversized frame only
    body += b"\xff" * (SNIFF_LIMIT + 1000 - len(body))
    assert is_animated_gif(body) is False
    assert gif_needs_more_bytes(body[:SNIFF_LIMIT], SNIFF_LIMIT) is False


# ----------------------------------------------------------------------------- large GIFs (4 MB rule)
def build_play_once_gif_with_large_first_frame() -> bytes:
    """Two frames, no NETSCAPE2.0 block (play once); the first frame's data runs past 64 KB."""
    body = GIF_HEADER + GIF_GLOBAL_TABLE + GIF_GRAPHIC_CONTROL
    body += b"\x2C" + struct.pack("<HHHH", 0, 0, 1, 1) + b"\x00" + b"\x02"  # descriptor + LZW code size
    while len(body) < SNIFF_LIMIT + 1000:
        body += b"\xff" + b"\x01" * 255  # full 255-byte data sub-blocks
    body += b"\x00"  # end of the first frame's data
    return body + GIF_GRAPHIC_CONTROL + gif_frame() + GIF_TRAILER


def test_play_once_gif_with_second_frame_after_64_kb_is_animated() -> None:
    gif = build_play_once_gif_with_large_first_frame()
    assert b"NETSCAPE2.0" not in gif
    assert gif.rindex(b"\x2C") > SNIFF_LIMIT  # the second image descriptor lies beyond 64 KB
    assert is_animated_gif(gif[:SNIFF_LIMIT]) is False  # the old 64 KB view could not tell
    assert gif_needs_more_bytes(gif[:SNIFF_LIMIT], SNIFF_LIMIT) is True
    assert is_animated_gif(gif) is True
    assert len(gif) < GIF_SNIFF_LIMIT


def test_short_truncated_file_does_not_ask_for_more_bytes() -> None:
    # Fewer bytes than the limit means the file ended: reading further cannot help.
    gif = build_play_once_gif_with_large_first_frame()[:1000]
    assert gif_needs_more_bytes(gif, SNIFF_LIMIT) is False


# ----------------------------------------------------------------------------- data: URLs
def test_percent_encoded_data_url_keeps_bytes_above_0x7f() -> None:
    gif = build_gif(frames=2)
    assert max(gif) >= 0x80  # the global colour table flag byte and the white entry
    url = "data:image/gif," + quote_from_bytes(gif)
    head = detect._read_image_head(None, url, SNIFF_LIMIT, local_ok=False)
    assert head == gif
    assert sniff_animated_image(head) == "animated-gif"


def test_base64_data_url_still_decodes() -> None:
    import base64

    gif = build_gif(frames=2)
    url = "data:image/gif;base64," + base64.b64encode(gif).decode("ascii").rstrip("=")
    assert detect._read_image_head(None, url, SNIFF_LIMIT, local_ok=False) == gif


@pytest.mark.parametrize(
    "make_url",
    [
        # ";base64" in any letter case: browsers accept it (WHATWG data: URL processing).
        lambda b64: "data:image/gif;BASE64," + b64,
        lambda b64: "data:image/gif ; Base64 ," + b64,
        # Base64 that went through encodeURIComponent: '+', '/' and '=' arrive as %2B, %2F and %3D.
        lambda b64: "data:image/gif;base64," + quote(b64, safe=""),
        # Line breaks inside the base64 text are ignored.
        lambda b64: "data:image/gif;base64," + b64[:20] + "\n" + b64[20:],
    ],
    ids=["upper-case", "spaced-mixed-case", "percent-encoded", "line-break"],
)
def test_base64_data_url_is_read_the_way_a_browser_reads_it(make_url: Callable[[str], str]) -> None:
    import base64

    gif = build_gif(frames=2, netscape=True) + b"\xfb\xff"  # bytes that give '+' and '/' in base64
    b64 = base64.b64encode(gif).decode("ascii")
    assert "+" in b64 or "/" in b64
    head = detect._read_image_head(None, make_url(b64), SNIFF_LIMIT, local_ok=False)
    assert head == gif
    assert sniff_animated_image(head) == "animated-gif"


class _FailingRequest:
    """A page.request whose every fetch fails, as for a server that never answers."""

    def get(self, *_args: object, **_kwargs: object) -> None:
        raise TimeoutError("no answer")


def test_unreadable_image_counts_as_unverified() -> None:
    """Bytes that cannot be read could be an animation: the image is uncontrollable, not still."""
    page = StandInPage("https://example.test/page.html", ["https://example.test/slow.gif"])
    page.request_object = _FailingRequest()
    uncontrollable, notes = find_uncontrollable(page, allow_local_files=False)
    assert uncontrollable == ["image-unverified: https://example.test/slow.gif"]
    assert notes == ["could not read image bytes to check for animation: https://example.test/slow.gif"]


# ----------------------------------------------------------------------------- where bytes may come from
class StandInPage:
    """Replaces a Playwright page: a fixed URL and a fixed scan result. Any network use fails the test.

    ``later`` lists the results of later scans (``ContentWatch.recheck``), one per call; when they run
    out, a later scan finds nothing new. ``options`` records the options each scan was called with.
    """

    def __init__(self, url: str, image_urls: list[str], workers: list[dict[str, str]] | None = None) -> None:
        self.url = url
        self.request_object: Any = None  # a test may plug in a stand-in for page.request
        self._scan = self.scan_result(image_urls, workers)
        self.later: list[dict[str, Any]] = []
        self.options: list[Any] = []

    @staticmethod
    def scan_result(image_urls: list[str], workers: list[dict[str, str]] | None = None) -> dict[str, Any]:
        return {
            "images": [{"raw": u, "resolved": u, "kind": "img", "selector": "img"} for u in image_urls],
            "webglCanvases": [],
            "offscreenCanvases": [],
            "crossOriginFrames": [],
            "workers": workers or [],
            "videos": 0,
            "shadowRoots": 0,
        }

    def evaluate(self, _script: str, options: Any = None) -> dict[str, Any]:
        self.options.append(options)
        if len(self.options) == 1:
            return self._scan
        return self.later.pop(0) if self.later else self.scan_result([])

    @property
    def request(self) -> Any:
        if self.request_object is None:
            raise AssertionError("no network request expected")
        return self.request_object


def test_local_reads_need_a_local_page_and_permission() -> None:
    assert local_reads_allowed("file:///tmp/page.html", True) is True
    assert local_reads_allowed("file:///tmp/page.html", False) is False
    assert local_reads_allowed("https://example.test/", True) is False
    assert local_reads_allowed("http://localhost:8000/", True) is False


@pytest.mark.parametrize(
    ("page_url", "allow"),
    [("https://example.test/page.html", True), ("file:///tmp/page.html", False)],
)
def test_file_image_is_never_touched_without_local_permission(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, page_url: str, allow: bool
) -> None:
    gif_path = tmp_path / "anim.gif"
    gif_path.write_bytes(build_gif(frames=2))

    def forbidden(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("the local file system must not be touched")

    # Neither a read nor an existence check may happen (patched only for the duration of the scan).
    with monkeypatch.context() as patch:
        patch.setattr(detect, "_read_local_file", forbidden)
        patch.setattr(Path, "is_file", forbidden)
        patch.setattr(Path, "exists", forbidden)
        uncontrollable, notes = find_uncontrollable(StandInPage(page_url, [gif_path.as_uri()]), allow)
    assert uncontrollable == []
    assert any(note.startswith("animation check skipped for a local image URL") for note in notes), notes


# ----------------------------------------------------------------------------- image cap, workers, later checks
def data_gif(frames: int, tag: int = 0) -> str:
    """A data: URL carrying a GIF with ``frames`` frames (no network needed to read it); ``tag`` makes
    URLs with the same bytes distinct (a media-type parameter, which decoding ignores)."""
    import base64

    return f"data:image/gif;n={tag};base64," + base64.b64encode(build_gif(frames=frames)).decode("ascii")


def test_images_past_the_cap_count_as_unverified() -> None:
    """An animated GIF at position 201 is never read: the capture must not call itself deterministic."""
    stills = [data_gif(1, tag=i) for i in range(1, detect.MAX_IMAGE_URLS + 1)]
    page = StandInPage("https://example.test/page.html", stills + [data_gif(2)])
    uncontrollable, notes = find_uncontrollable(page, allow_local_files=False)
    assert uncontrollable == [f"images-unverified: 1 image URL(s) beyond the first {detect.MAX_IMAGE_URLS} were not checked for animation"]
    assert notes == [f"only the first {detect.MAX_IMAGE_URLS} of {detect.MAX_IMAGE_URLS + 1} image URLs were checked for animation"]


def test_workers_are_uncontrollable() -> None:
    workers = [{"kind": "Worker", "url": "https://example.test/w.js"}, {"kind": "SharedWorker", "url": "blob:https://example.test/1"}]
    uncontrollable, _notes = find_uncontrollable(StandInPage("https://example.test/", [], workers), allow_local_files=False)
    assert uncontrollable == ["worker: https://example.test/w.js", "shared-worker: blob:https://example.test/1"]


def test_content_watch_reports_only_what_is_new_and_reuses_verdicts() -> None:
    """Later looks classify new URLs once (requested images included); a shared cache spares re-reading."""
    animated, still = data_gif(2), data_gif(1)
    page = StandInPage("https://example.test/", [still])
    verdicts: detect.ImageVerdicts = {}
    watch = detect.ContentWatch(False, verdicts)
    assert watch.first_scan(page) == ([], [])
    page.later = [StandInPage.scan_result([still, animated])]  # the page scan returns the GIF; the still is old news
    assert watch.recheck(page, full=False) == ([f"animated-gif: {animated}"], [])
    assert watch.recheck(page, full=True, requested_images=[animated]) == ([], [])  # already judged on this page
    assert page.options == [detect.FIRST_SCAN, detect.ELEMENT_SCAN, detect.FULL_SCAN]
    assert (animated, animated) in verdicts and len(verdicts) == 2

    # A later attempt on the same page (a new ContentWatch) takes the verdict from the cache.
    def unreadable(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("a cached image must not be read again")

    second = StandInPage("https://example.test/", [animated])
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(detect, "_read_image_head", unreadable)
        assert detect.ContentWatch(False, verdicts).first_scan(second) == ([f"animated-gif: {animated}"], [])


def test_local_page_with_permission_reads_a_large_gif_up_to_4_mb(tmp_path: Path) -> None:
    gif_path = tmp_path / "big.gif"
    gif_path.write_bytes(build_play_once_gif_with_large_first_frame())
    page = StandInPage((tmp_path / "page.html").as_uri(), [gif_path.as_uri()])
    uncontrollable, notes = find_uncontrollable(page, allow_local_files=True)
    assert uncontrollable == [f"animated-gif: {gif_path.as_uri()}"], notes
