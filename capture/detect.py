"""Detection of page content that strategy A cannot control (SPEC.md section 6.2).

Strategy A drives time through the Web Animations API and Playwright's fake clock. Three kinds of
content ignore both and keep animating on the browser's own clock:

* animated images (GIF, APNG, animated WebP), which the browser animates internally;
* WebGL canvases, whose pixels come from GPU programs we cannot pause or seek;
* cross-origin iframes, whose documents our scripts are not allowed to touch.

Image formats are recognised from their bytes, never from the file extension, because a ``.png`` URL
can serve a GIF. Only the first ``SNIFF_LIMIT`` bytes are inspected.

The pure functions (``is_animated_gif`` etc.) have no browser dependency and are unit-tested with
hand-made byte strings; ``find_uncontrollable`` needs a loaded Playwright page.
"""

from __future__ import annotations

import base64
import struct
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import unquote, urlparse

if TYPE_CHECKING:  # imported for type hints only; the pure functions stay browser-free
    from playwright.sync_api import Page

# How many leading bytes of an image we look at. The animation markers of all three formats live in
# the header area (GIF NETSCAPE2.0 extension, PNG acTL chunk and WebP VP8X/ANIM chunks all come
# before any pixel data), so 64 KB is plenty and keeps the scan cheap on image-heavy pages.
SNIFF_LIMIT = 64 * 1024

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


def is_animated_gif(data: bytes) -> bool:
    """True when ``data`` starts a GIF with more than one image descriptor or a NETSCAPE2.0 loop block.

    The file is walked block by block (image descriptors ``0x2C``, extensions ``0x21``, trailer
    ``0x3B``) so that ``0x2C`` bytes inside compressed pixel data are not mistaken for frames.
    """
    data = data[:SNIFF_LIMIT]
    if not data.startswith(b"GIF8") or len(data) < 13:
        return False
    # The NETSCAPE2.0 application extension carries the loop count and only appears in animations.
    if b"NETSCAPE2.0" in data:
        return True
    pos = 13 + _gif_colour_table_size(data[10])  # header (6) + logical screen descriptor (7) + table
    image_descriptors = 0
    while pos < len(data):
        marker = data[pos]
        if marker == 0x3B:  # trailer: end of file
            break
        if marker == 0x2C:  # image descriptor: 10 bytes, optional local colour table, LZW min code size
            image_descriptors += 1
            if image_descriptors > 1:
                return True
            if pos + 10 > len(data):
                break
            pos += 10 + _gif_colour_table_size(data[pos + 9]) + 1
            pos = _gif_skip_sub_blocks(data, pos)
        elif marker == 0x21:  # extension: introducer, label, then sub-blocks
            pos = _gif_skip_sub_blocks(data, pos + 2)
        else:  # not a GIF block we know; stop rather than guess
            break
    return False


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

# Runs after load. Walks the main window and every same-origin frame (a cross-origin frame throws
# on document access, which is exactly how it is recognised) and returns what detection needs.
SCAN_PAGE_JS = r"""
() => {
  const result = { images: [], webglCanvases: [], offscreenCanvases: [], crossOriginFrames: [] };
  const seenUrls = new Set();

  // A short, human-readable selector: #id when there is one, else the tag path with :nth-of-type.
  const cssPath = (el) => {
    if (el.id) return '#' + CSS.escape(el.id);
    const parts = [];
    for (let node = el; node && node.nodeType === 1 && parts.length < 6; node = node.parentElement) {
      let part = node.tagName.toLowerCase();
      if (node.id) { parts.unshift('#' + CSS.escape(node.id)); break; }
      const siblings = node.parentElement ? Array.from(node.parentElement.children).filter(c => c.tagName === node.tagName) : [];
      if (siblings.length > 1) part += ':nth-of-type(' + (siblings.indexOf(node) + 1) + ')';
      parts.unshift(part);
    }
    return parts.join(' > ');
  };

  const addImage = (raw, resolved, kind, selector) => {
    if (!resolved || seenUrls.has(resolved)) return;
    seenUrls.add(resolved);
    result.images.push({ raw, resolved, kind, selector });
  };

  const visit = (win) => {
    let doc;
    try { doc = win.document; if (!doc) return; } catch (e) { return; }
    for (const img of doc.querySelectorAll('img')) {
      const src = img.currentSrc || img.getAttribute('src') || '';
      if (!src) continue;
      let resolved = src;
      try { resolved = new URL(src, doc.baseURI).href; } catch (e) {}
      addImage(img.getAttribute('src') || src, resolved, 'img', cssPath(img));
    }
    const urlPattern = /url\((['"]?)(.*?)\1\)/g;
    for (const el of doc.querySelectorAll('*')) {
      const bg = win.getComputedStyle(el).backgroundImage;
      if (!bg || bg === 'none') continue;
      let match;
      while ((match = urlPattern.exec(bg)) !== null) {
        let resolved = match[2];
        try { resolved = new URL(match[2], doc.baseURI).href; } catch (e) {}
        addImage(match[2], resolved, 'background-image', cssPath(el));
      }
    }
    for (const canvas of doc.querySelectorAll('canvas')) {
      const types = canvas.__ftContextTypes || [];
      if (types.some(t => t === 'webgl' || t === 'webgl2' || t === 'experimental-webgl')) {
        result.webglCanvases.push(cssPath(canvas));
      }
      if (canvas.__ftOffscreen) result.offscreenCanvases.push(cssPath(canvas));
    }
    for (const frame of doc.querySelectorAll('iframe, frame')) {
      let accessible = false;
      try { accessible = !!frame.contentDocument; } catch (e) { accessible = false; }
      if (!accessible) {
        result.crossOriginFrames.push(frame.getAttribute('src') || frame.src || '(no src)');
      }
    }
    for (let i = 0; i < win.frames.length; i++) {
      try { if (win.frames[i].document) visit(win.frames[i]); } catch (e) { /* cross-origin: listed above */ }
    }
  };
  visit(window);
  return result;
}
"""


def _read_image_head(page: "Page", url: str) -> bytes | None:
    """Fetch at most ``SNIFF_LIMIT`` bytes of ``url`` (local file, data: URL or over the page's network)."""
    parsed = urlparse(url)
    try:
        if parsed.scheme == "file":
            with open(unquote(parsed.path), "rb") as handle:
                return handle.read(SNIFF_LIMIT)
        if parsed.scheme == "data":
            header, _, payload = url.partition(",")
            if ";base64" in header:
                return base64.b64decode(payload + "=" * (-len(payload) % 4))[:SNIFF_LIMIT]
            return unquote(payload).encode("latin-1")[:SNIFF_LIMIT]
        if parsed.scheme in ("http", "https"):
            # A Range header asks the server for the head only; servers that ignore it send the whole
            # body, which is sliced below, so the result is the same either way.
            response = page.request.get(
                url,
                headers={"Range": f"bytes=0-{SNIFF_LIMIT - 1}"},
                timeout=5000,
                fail_on_status_code=False,
            )
            return response.body()[:SNIFF_LIMIT]
    except Exception:  # unreadable image: nothing to classify, reported by the caller as a note
        return None
    return None


def find_uncontrollable(page: "Page") -> tuple[list[str], list[str]]:
    """Scan a loaded page and return ``(uncontrollable, notes)``.

    ``uncontrollable`` entries look like ``"animated-gif: /x.gif"``, ``"webgl-canvas: #c"`` or
    ``"cross-origin-iframe: https://..."``; ``notes`` records anything the scan could not verify.
    """
    scan = page.evaluate(SCAN_PAGE_JS)
    uncontrollable: list[str] = []
    notes: list[str] = []

    images = scan["images"]
    if len(images) > MAX_IMAGE_URLS:
        notes.append(f"only the first {MAX_IMAGE_URLS} of {len(images)} image URLs were checked for animation")
        images = images[:MAX_IMAGE_URLS]
    for image in images:
        head = _read_image_head(page, image["resolved"])
        if head is None:
            notes.append(f"could not read image bytes to check for animation: {image['raw']}")
            continue
        kind = sniff_animated_image(head)
        if kind is not None:
            uncontrollable.append(f"{kind}: {image['raw']}")

    for selector in scan["webglCanvases"]:
        uncontrollable.append(f"webgl-canvas: {selector}")
    for selector in scan["offscreenCanvases"]:
        uncontrollable.append(f"offscreen-canvas: {selector}")
    for src in scan["crossOriginFrames"]:
        uncontrollable.append(f"cross-origin-iframe: {src}")
    return uncontrollable, notes


def local_path_from_url(url: str) -> Path | None:
    """Return the filesystem path for a ``file://`` URL, else ``None`` (helper shared with capturer)."""
    parsed = urlparse(url)
    if parsed.scheme != "file":
        return None
    return Path(unquote(parsed.path))
