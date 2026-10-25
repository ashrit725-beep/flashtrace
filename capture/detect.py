"""Detection of page content that strategy A cannot control (SPEC.md section 6.2).

Strategy A drives time through the Web Animations API and Playwright's fake clock. Three kinds of
content ignore both and keep animating on the browser's own clock:

* animated images (GIF, APNG, animated WebP), which the browser animates internally;
* WebGL canvases, whose pixels come from GPU programs we cannot pause or seek;
* cross-origin iframes, whose documents our scripts are not allowed to touch.

Images are looked for wherever a page can show one: ``<img>``, SVG ``<image>``, ``<input type=image>``,
``<object>``, ``<embed>``, ``<video poster>``, and every CSS property that paints an image (backgrounds,
``content``, list markers, border images, masks) on each element and on its ``::before``/``::after``
boxes. Image formats are recognised from their bytes, never from the file extension, because a ``.png``
URL can serve a GIF. Only the first ``SNIFF_LIMIT`` bytes are inspected, or up to ``GIF_SNIFF_LIMIT`` for
a GIF whose first frame is larger than that. An image whose bytes cannot be read is reported as
``image-unverified`` (uncontrollable): it could be animated, and a wrong "still" would let strategy A
claim determinism for frames a GIF moved on the real clock.

The scan looks in the document, in every shadow root (open or closed, recorded by the init script in
``capture/page_scripts.py``; ADR-022) and in every same-origin frame. Elements suppressed for an
ablation (ADR-013) are skipped: they are hidden, so they cannot reach the screen.

Local files are read only when the audited page is itself a local file *and* local files are allowed;
for an ``http(s)`` page the local file system is never touched (no read, no existence check).

The pure functions (``is_animated_gif`` etc.) have no browser dependency and are unit-tested with
hand-made byte strings; ``find_uncontrollable`` needs a loaded page (any object with ``url``,
``evaluate`` and ``request``, so it can be tested with a stand-in).
"""

from __future__ import annotations

import base64
import re
import struct
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import unquote, unquote_to_bytes, urlparse

from capture.page_scripts import PAGE_HELPERS_JS

if TYPE_CHECKING:  # imported for type hints only; the pure functions stay browser-free
    from playwright.sync_api import Page

# How many leading bytes of an image we look at. The animation markers of PNG (acTL chunk) and WebP
# (VP8X/ANIM chunks) come before any pixel data, so 64 KB is plenty and keeps the scan cheap on
# image-heavy pages. A GIF is different: a play-once animation has no NETSCAPE2.0 block, and its second
# frame starts only after all of the first frame's pixel data, so GIFs are read further (below).
SNIFF_LIMIT = 64 * 1024

# How far a GIF is read when its first frame does not end inside the first ``SNIFF_LIMIT`` bytes.
GIF_SNIFF_LIMIT = 4 * 1024 * 1024

# Reasonable cap so a page with thousands of icons does not turn detection into a crawl.
MAX_IMAGE_URLS = 200


# ---------------------------------------------------------------------------------------- GIF
def _gif_skip_sub_blocks(data: bytes, pos: int) -> int:
    """Skip a GIF data sub-block chain (size byte followed by that many bytes, ended by a 0 byte)."""
    while pos < len(data):
        size = data[pos]
        pos += 1
        if size == 0:
            return pos
        pos += size
    return pos


def _gif_colour_table_size(flags: int) -> int:
    """Bytes used by a colour table whose presence/size is described by a GIF flags byte."""
    if flags & 0x80 == 0:
        return 0
    return 3 * (2 ** ((flags & 0x07) + 1))


def gif_walk(data: bytes) -> str:
    """Walk the blocks of a GIF and say what was found: ``"animated"``, ``"still"`` or ``"truncated"``.

    The file is walked block by block (image descriptors ``0x2C``, extensions ``0x21``, trailer
    ``0x3B``) so that ``0x2C`` bytes inside compressed pixel data are not mistaken for frames.
    ``"truncated"`` means the bytes ran out before the walk could decide (no trailer, one frame so
    far): the caller should read more of the file. Bytes that are not a GIF give ``"still"``.
    """
    if not data.startswith(b"GIF8") or len(data) < 13:
        return "still"
    # The NETSCAPE2.0 application extension carries the loop count and only appears in animations.
    if b"NETSCAPE2.0" in data:
        return "animated"
    pos = 13 + _gif_colour_table_size(data[10])  # header (6) + logical screen descriptor (7) + table
    image_descriptors = 0
    while pos < len(data):
        marker = data[pos]
        if marker == 0x3B:  # trailer: end of file
            return "still"
        if marker == 0x2C:  # image descriptor: 10 bytes, optional local colour table, LZW min code size
            image_descriptors += 1
            if image_descriptors > 1:
                return "animated"
            if pos + 10 > len(data):
                return "truncated"
            pos += 10 + _gif_colour_table_size(data[pos + 9]) + 1
            pos = _gif_skip_sub_blocks(data, pos)
        elif marker == 0x21:  # extension: introducer, label, then sub-blocks
            pos = _gif_skip_sub_blocks(data, pos + 2)
        else:  # not a GIF block we know; stop rather than guess
            return "still"
    return "truncated"


def is_animated_gif(data: bytes) -> bool:
    """True when ``data`` is a GIF with more than one image descriptor or a NETSCAPE2.0 loop block.

    At most ``GIF_SNIFF_LIMIT`` bytes are walked.
    """
    return gif_walk(data[:GIF_SNIFF_LIMIT]) == "animated"


def gif_needs_more_bytes(head: bytes, limit: int) -> bool:
    """True when ``head`` (the first ``limit`` bytes of a file) is a GIF whose walk ran out of bytes.

    A shorter ``head`` is the whole file, so reading further would not find anything new.
    """
    return len(head) >= limit and gif_walk(head) == "truncated"


# ---------------------------------------------------------------------------------------- PNG
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def is_animated_png(data: bytes) -> bool:
    """True when ``data`` is a PNG whose chunk list contains ``acTL`` (the APNG animation control chunk).

    Chunks are walked properly (4-byte length, 4-byte type, data, 4-byte CRC); ``acTL`` must precede
    the first ``IDAT`` per the APNG specification, so the walk stops there.
    """
    data = data[:SNIFF_LIMIT]
    if not data.startswith(PNG_SIGNATURE):
        return False
    pos = len(PNG_SIGNATURE)
    while pos + 8 <= len(data):
        (length,) = struct.unpack(">I", data[pos : pos + 4])
        chunk_type = data[pos + 4 : pos + 8]
        if chunk_type == b"acTL":
            return True
        if chunk_type in (b"IDAT", b"IEND"):
            return False
        pos += 12 + length  # length + type + data + CRC
    return False


# ---------------------------------------------------------------------------------------- WebP
def is_animated_webp(data: bytes) -> bool:
    """True when ``data`` is a WebP with an ``ANIM`` chunk or a ``VP8X`` chunk whose animation flag is set."""
    data = data[:SNIFF_LIMIT]
    if len(data) < 16 or data[:4] != b"RIFF" or data[8:12] != b"WEBP":
        return False
    pos = 12
    while pos + 8 <= len(data):
        fourcc = data[pos : pos + 4]
        (size,) = struct.unpack("<I", data[pos + 4 : pos + 8])
        if fourcc == b"ANIM":
            return True
        if fourcc == b"VP8X" and pos + 8 < len(data):
            # VP8X flags byte: bit 1 (0x02) is "Animation" in the extended file format.
            if data[pos + 8] & 0x02:
                return True
        pos += 8 + size + (size & 1)  # chunk payloads are padded to an even length
    return False


def sniff_animated_image(data: bytes) -> str | None:
    """Return ``"animated-gif"``, ``"animated-png"``, ``"animated-webp"`` or ``None`` for ``data``."""
    if is_animated_gif(data):
        return "animated-gif"
    if is_animated_png(data):
        return "animated-png"
    if is_animated_webp(data):
        return "animated-webp"
    return None


# ------------------------------------------------------------------------------- browser-side scan
# Installed before any page script runs. It records which rendering context each <canvas> asked for,
# so detection never has to call getContext() itself (calling it would create a context and could
# change how the page renders). transferControlToOffscreen() hands the canvas to a worker or
# OffscreenCanvas, which the fake clock does not reach either, so it is recorded too.
CANVAS_PROBE_JS = r"""
(() => {
  if (typeof HTMLCanvasElement === 'undefined') return;
  const proto = HTMLCanvasElement.prototype;
  const originalGetContext = proto.getContext;
  proto.getContext = function (type, ...rest) {
    const context = originalGetContext.call(this, type, ...rest);
    if (context) {
      const types = this.__ftContextTypes || (this.__ftContextTypes = []);
      if (!types.includes(type)) types.push(type);
    }
    return context;
  };
  if (proto.transferControlToOffscreen) {
    const originalTransfer = proto.transferControlToOffscreen;
    proto.transferControlToOffscreen = function (...rest) {
      this.__ftOffscreen = true;
      return originalTransfer.apply(this, rest);
    };
  }
})();
"""

# Runs after load (and after an ablation's set-up). Walks the document, every shadow root and every
# same-origin frame (``ftScopes`` from page_scripts.py) and returns what detection needs. A frame
# whose document throws on access is cross-origin, which is exactly how it is recognised.
_SCAN_PAGE_BODY = r"""
  const result = { images: [], webglCanvases: [], offscreenCanvases: [], crossOriginFrames: [],
                   videos: 0, shadowRoots: 0 };
  const seenUrls = new Set();

  // A short, human-readable selector: #id when there is one, else the tag path with :nth-of-type.
  // An element inside a shadow root is written as its host's path, '>>', then the path inside.
  const cssPath = (el) => {
    const parts = [];
    for (let node = el; node && node.nodeType === 1 && parts.length < 6; node = node.parentElement) {
      if (node.id) { parts.unshift('#' + CSS.escape(node.id)); break; }
      let part = node.tagName.toLowerCase();
      const siblings = node.parentElement ? Array.from(node.parentElement.children).filter(c => c.tagName === node.tagName) : [];
      if (siblings.length > 1) part += ':nth-of-type(' + (siblings.indexOf(node) + 1) + ')';
      parts.unshift(part);
    }
    const root = el.getRootNode();
    return (root && root.host ? cssPath(root.host) + ' >> ' : '') + parts.join(' > ');
  };

  const addImage = (raw, resolved, kind, selector) => {
    if (!resolved || seenUrls.has(resolved)) return;
    seenUrls.add(resolved);
    result.images.push({ raw, resolved, kind, selector });
  };

  const resolveUrl = (url, base) => {
    try { return new URL(url, base).href; } catch (e) { return url; }
  };

  // The image URL an element names itself: <img> (the source the browser chose), SVG <image>,
  // <input type=image>, <object>, <embed> and a <video> poster.
  const IMAGE_ELEMENTS = 'img, image, input[type=image], object[data], embed[src], video[poster]';
  const elementImageUrl = (el) => {
    if (el.localName === 'img') return el.currentSrc || el.getAttribute('src') || '';
    if (el.localName === 'image') return el.getAttribute('href') || el.getAttribute('xlink:href') || '';
    if (el.localName === 'object') return el.getAttribute('data') || '';
    if (el.localName === 'video') return el.getAttribute('poster') || '';
    return el.getAttribute('src') || '';  // <input type=image>, <embed>
  };

  // Every CSS property that can paint an image. They are read on the element and on its ::before and
  // ::after boxes: an animated GIF shown as generated content is as visible as an <img>.
  const IMAGE_PROPERTIES = ['background-image', 'content', 'list-style-image', 'border-image-source', 'mask-image', '-webkit-mask-image'];
  const urlPattern = /url\((['"]?)(.*?)\1\)/g;
  const scanStyleImages = (win, el, base) => {
    for (const pseudo of [null, '::before', '::after']) {
      const style = win.getComputedStyle(el, pseudo);
      for (const property of IMAGE_PROPERTIES) {
        const value = style.getPropertyValue(property);
        if (!value || !value.includes('url(')) continue;
        for (const match of value.matchAll(urlPattern)) {
          addImage(match[2], resolveUrl(match[2], base), property + (pseudo || ''), cssPath(el));
        }
      }
    }
  };

  const scanImages = (win, root, base) => {
    for (const el of root.querySelectorAll(IMAGE_ELEMENTS)) {
      const src = elementImageUrl(el);
      if (src && !ftIsSuppressed(el)) addImage(src, resolveUrl(src, base), el.localName, cssPath(el));
    }
    for (const el of root.querySelectorAll('*')) {
      if (!ftIsSuppressed(el)) scanStyleImages(win, el, base);
    }
  };

  const scanCanvasesAndFrames = (root) => {
    for (const canvas of root.querySelectorAll('canvas')) {
      if (ftIsSuppressed(canvas)) continue;
      const types = canvas.__ftContextTypes || [];
      if (types.some(t => t === 'webgl' || t === 'webgl2' || t === 'experimental-webgl')) {
        result.webglCanvases.push(cssPath(canvas));
      }
      if (canvas.__ftOffscreen) result.offscreenCanvases.push(cssPath(canvas));
    }
    for (const frame of root.querySelectorAll('iframe, frame')) {
      if (ftIsSuppressed(frame)) continue;
      let accessible = false;
      try { accessible = !!frame.contentDocument; } catch (e) { accessible = false; }
      if (!accessible) result.crossOriginFrames.push(frame.getAttribute('src') || frame.src || '(no src)');
    }
  };

  for (const { win, root } of ftScopes(window, true)) {
    if (root.nodeType === 11) result.shadowRoots++;  // 11: a shadow root
    const base = (root.nodeType === 9 ? root : root.ownerDocument).baseURI;  // 9: a document
    scanImages(win, root, base);
    scanCanvasesAndFrames(root);
    result.videos += root.querySelectorAll('video').length;
  }
  return result;
"""

SCAN_PAGE_JS = "() => {\n" + PAGE_HELPERS_JS + _SCAN_PAGE_BODY + "}\n"


# ------------------------------------------------------------------------------- reading image bytes
def local_reads_allowed(page_url: str, allow_local_files: bool) -> bool:
    """Whether image bytes may be read from the local file system for the page at ``page_url``.

    Only a page that is itself a local file, audited with local files allowed, may cause local reads.
    For an ``http(s)`` page a ``file:`` image is never opened or even checked for existence, so a
    remote page cannot use FlashTrace to learn anything about the machine it runs on.
    """
    return allow_local_files and urlparse(page_url).scheme == "file"


def _read_local_file(path: Path, limit: int) -> bytes | None:
    """The first ``limit`` bytes of a regular file (``None`` for a directory, pipe or device)."""
    if not path.is_file():
        return None
    with open(path, "rb") as handle:
        return handle.read(limit)


# Header of a data: URL whose payload is base64: the media type ends in ";base64", in any letter case,
# with optional spaces around (WHATWG Fetch, "data: URL processor").
_BASE64_HEADER = re.compile(r";\s*base64\s*$", re.IGNORECASE)


def _decode_data_url(url: str) -> bytes:
    """The bytes carried by a ``data:`` URL, decoded the way a browser decodes it.

    The payload is percent-decoded first (``unquote_to_bytes`` keeps every %XX escape as that exact
    byte, bytes >= 0x80 included), then base64-decoded when the header says so. ASCII whitespace inside
    base64 is ignored and missing ``=`` padding is restored, as browsers do.
    """
    header, _, payload = url.partition(",")
    raw = unquote_to_bytes(payload)
    if not _BASE64_HEADER.search(header):
        return raw
    text = re.sub(rb"[\t\n\f\r ]", b"", raw).rstrip(b"=")
    return base64.b64decode(text + b"=" * (-len(text) % 4))


# Reads the first ``limit`` bytes of a URL from inside the page and returns them base64-encoded. A blob:
# URL exists only inside the page that made it, so it cannot be fetched from outside.
READ_IN_PAGE_JS = r"""
async ([url, limit]) => {
  const bytes = new Uint8Array(await (await fetch(url)).arrayBuffer()).subarray(0, limit);
  let text = '';
  for (let i = 0; i < bytes.length; i += 0x8000) text += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  return btoa(text);
}
"""


def _read_image_head(page: Any, url: str, limit: int, local_ok: bool) -> bytes | None:
    """At most ``limit`` bytes of ``url`` (local file, data: or blob: URL, or over the page's network).

    ``local_ok`` comes from ``local_reads_allowed``; without it a ``file:`` URL is not touched.
    """
    parsed = urlparse(url)
    try:
        if parsed.scheme == "file":
            return _read_local_file(Path(unquote(parsed.path)), limit) if local_ok else None
        if parsed.scheme == "data":
            return _decode_data_url(url)[:limit]
        if parsed.scheme == "blob":
            return base64.b64decode(page.evaluate(READ_IN_PAGE_JS, [url, limit]))
        if parsed.scheme in ("http", "https"):
            # A Range header asks the server for the head only; servers that ignore it send the whole
            # body, which is sliced below, so the result is the same either way.
            response = page.request.get(
                url, headers={"Range": f"bytes=0-{limit - 1}"}, timeout=5000, fail_on_status_code=False
            )
            return response.body()[:limit]
    except Exception:  # unreadable image: nothing to classify, reported by the caller as a note
        return None
    return None


def _classify_image(page: Any, image: dict[str, str], local_ok: bool) -> tuple[str | None, str | None]:
    """``(uncontrollable entry, note)`` for one image found by the scan; ``(None, None)`` when it is still."""
    raw, url = image["raw"], image["resolved"]
    if urlparse(url).scheme == "file" and not local_ok:
        return None, f"animation check skipped for a local image URL (the audited page is not a local file, or local files are not allowed): {raw}"
    head = _read_image_head(page, url, SNIFF_LIMIT, local_ok)
    if head is None:
        # Safe direction, as for large GIFs below: bytes we could not read could hold an animation.
        return f"image-unverified: {raw}", f"could not read image bytes to check for animation: {raw}"
    if gif_needs_more_bytes(head, SNIFF_LIMIT):
        # The first frame of this GIF does not end inside the first 64 KB: read up to 4 MB.
        head = _read_image_head(page, url, GIF_SNIFF_LIMIT, local_ok) or head
        if gif_needs_more_bytes(head, GIF_SNIFF_LIMIT):
            # Safe direction: frames beyond 4 MB could animate, so the image counts as uncontrollable.
            return f"gif-unverified: {raw}", f"GIF frames beyond the first {GIF_SNIFF_LIMIT} bytes were not checked: {raw}"
    kind = sniff_animated_image(head)
    return (f"{kind}: {raw}" if kind is not None else None), None


# ------------------------------------------------------------------------------- entry point
def find_uncontrollable(page: "Page", allow_local_files: bool = False) -> tuple[list[str], list[str]]:
    """Scan a loaded page and return ``(uncontrollable, notes)``.

    ``uncontrollable`` entries look like ``"animated-gif: /x.gif"``, ``"webgl-canvas: #c"`` or
    ``"cross-origin-iframe: https://..."``; ``notes`` records anything the scan could not verify.
    ``allow_local_files`` is the capture's switch; see ``local_reads_allowed`` for how it is used.
    """
    scan = page.evaluate(SCAN_PAGE_JS)
    local_ok = local_reads_allowed(page.url, allow_local_files)
    uncontrollable: list[str] = []
    notes: list[str] = []

    images = scan["images"]
    if len(images) > MAX_IMAGE_URLS:
        notes.append(f"only the first {MAX_IMAGE_URLS} of {len(images)} image URLs were checked for animation")
        images = images[:MAX_IMAGE_URLS]
    for image in images:
        entry, note = _classify_image(page, image, local_ok)
        uncontrollable.extend([entry] if entry else [])
        notes.extend([note] if note else [])

    uncontrollable.extend(f"webgl-canvas: {selector}" for selector in scan["webglCanvases"])
    uncontrollable.extend(f"offscreen-canvas: {selector}" for selector in scan["offscreenCanvases"])
    uncontrollable.extend(f"cross-origin-iframe: {src}" for src in scan["crossOriginFrames"])
    if scan.get("shadowRoots"):
        notes.append(f"detection searched {scan['shadowRoots']} shadow root(s) (open or closed)")
    return uncontrollable, notes


def local_path_from_url(url: str) -> Path | None:
    """Return the filesystem path for a ``file://`` URL, else ``None`` (helper shared with capturer)."""
    parsed = urlparse(url)
    if parsed.scheme != "file":
        return None
    return Path(unquote(parsed.path))
