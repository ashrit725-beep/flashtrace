"""Detection of page content that strategy A cannot control (SPEC.md section 6.2).

Strategy A drives time through the Web Animations API and Playwright's fake clock. Five kinds of
content ignore both and keep animating on the browser's own clock:

* animated images (GIF, APNG, animated WebP, and SVG images with SMIL or CSS animations), which the
  browser animates internally;
* WebGL canvases, whose pixels come from GPU programs we cannot pause or seek, and canvases handed to
  an ``OffscreenCanvas``;
* cross-origin iframes, whose documents our scripts are not allowed to touch;
* Web Workers and shared workers, whose timers run on the real clock and whose messages can change the
  page at any real moment (``WORKER_PROBE_JS`` records every one the page starts).

Images are looked for wherever a page can show one: ``<img>``, SVG ``<image>``, ``<input type=image>``,
``<object>``, ``<embed>`` (unless it shows a same-origin document, whose animations strategy A drives
like the page's own), ``<video poster>``, and every CSS property that paints an image (backgrounds,
``content``, list markers, border images, masks) on each element and on its ``::before``/``::after``
boxes. Image formats are recognised from their bytes, never from the file extension, because a ``.png``
URL can serve a GIF. Only the first ``SNIFF_LIMIT`` bytes are inspected, or up to ``GIF_SNIFF_LIMIT`` for
a GIF whose first frame, or an SVG whose markup, is larger than that. An image whose bytes cannot be
read is reported as ``image-unverified`` (uncontrollable): it could be animated, and a wrong "still"
would let strategy A claim determinism for frames a GIF moved on the real clock. For the same reason
image URLs past the first ``MAX_IMAGE_URLS``, and image URLs not read before the capture's
``DETECTION_BUDGET_S`` of real time for image reads ran out (``ReadBudget``), are reported as
``images-unverified``.

Content can also arrive after the scan before frame 0: an ``<img>`` a timer inserts, a background a
class change brings in, a worker a script starts. ``ContentWatch`` keeps what the first scan saw, and
strategy A asks it at every frame for anything new (SPEC 6.2): the element scan without the costly
computed-style walk every frame, the full scan once per captured second, plus every image URL the page
requested since the last frame. Strategy B, when it takes budget-only pictures, asks for the full scan
at the same once-a-second checkpoints. What one attempt found is carried into the next attempt's
``ContentWatch`` (ADR-046).

The scan looks in the document, in every shadow root (open or closed, recorded by the init script in
``capture/page_scripts.py``; ADR-022) and in every same-origin frame. Elements an ablation hid
(ADR-013; ``ftIsHidden``) are skipped: they cannot reach the screen. A suppressed element that is
still visible (its animations are only held) is scanned like any other.

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
import time
import zlib
from collections.abc import Iterable
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

# How far a GIF is read when its first frame does not end inside the first ``SNIFF_LIMIT`` bytes, and
# an SVG when its markup goes on past them without an animation so far.
GIF_SNIFF_LIMIT = 4 * 1024 * 1024

# Most image URLs whose bytes are read for one page (scan and later checks together), so a page with
# thousands of icons does not turn detection into a crawl. URLs past the cap are reported as
# 'images-unverified' (uncontrollable), the same safe direction as an unreadable image.
MAX_IMAGE_URLS = 200

# Real milliseconds one image read over the network may take before the image counts as unreadable.
IMAGE_READ_TIMEOUT_MS = 5000

# Real seconds that detection's image reads may take in all, for one capture. The scan before frame 0 of
# every strategy and the later looks of strategy A (every frame) and of a budget-only strategy B (its
# checkpoints) share it, as they share the image verdicts (ADR-047). The reads are silent: without this
# limit 60 image URLs that never answer, at IMAGE_READ_TIMEOUT_MS each, kept a capture without a
# progress callback for 300 s, the server watchdog's stall limit (ADR-049). Image URLs not read within
# it count as 'images-unverified', the same safe direction as an unreadable image: "auto" falls through
# and a PASS becomes INCONCLUSIVE.
DETECTION_BUDGET_S = 60.0


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


# ---------------------------------------------------------------------------------------- SVG
# An SVG shown as an image (<img>, CSS background, SVG <image>) runs no script, but its SMIL elements and
# CSS animations play on the browser's own clock, out of reach of strategy A. CSS animations need an
# @keyframes rule inside the file, because an SVG image cannot load an outside stylesheet.
_SVG_ROOT = re.compile(rb"<(?:[A-Za-z_][\w.-]*:)?svg[\s>/]", re.IGNORECASE)
_HTML_START = re.compile(rb"<!doctype\s+html|<html[\s>]", re.IGNORECASE)
_SVG_ANIMATION = re.compile(
    rb"<(?:[A-Za-z_][\w.-]*:)?(?:animate|animateTransform|animateMotion|animateColor|set)[\s/>]"  # SMIL
    rb"|@(?:-[a-z]+-)?keyframes[\s{]",                                                            # CSS
    re.IGNORECASE,
)


def svg_markup(data: bytes) -> bytes | None:
    """The markup of an SVG file (an ``.svgz`` file is gunzipped first), or ``None`` when ``data`` is not SVG.

    Bytes count as SVG when they start with markup (after an optional byte-order mark and white space),
    are not an HTML document and contain an ``<svg`` root. At most ``GIF_SNIFF_LIMIT`` bytes are unzipped.
    """
    if data[:2] == b"\x1f\x8b":  # gzip
        try:
            data = zlib.decompressobj(16 + zlib.MAX_WBITS).decompress(data, GIF_SNIFF_LIMIT)
        except zlib.error:
            return None
    start = data[:512].lstrip(b"\xef\xbb\xbf \t\r\n")
    if not start.startswith(b"<") or _HTML_START.search(start):
        return None
    return data if _SVG_ROOT.search(data) else None


def is_animated_svg(data: bytes) -> bool:
    """True when ``data`` is an SVG file with a SMIL animation element or an ``@keyframes`` rule."""
    markup = svg_markup(data[:GIF_SNIFF_LIMIT])
    return markup is not None and _SVG_ANIMATION.search(markup) is not None


def svg_needs_more_bytes(head: bytes, limit: int) -> bool:
    """True when ``head`` (the first ``limit`` bytes of a file) is an SVG cut off before any animation.

    The rest of the file could still hold one; a shorter ``head`` is the whole file.
    """
    return len(head) >= limit and svg_markup(head) is not None and not is_animated_svg(head)


def sniff_animated_image(data: bytes) -> str | None:
    """``"animated-gif"``, ``"animated-png"``, ``"animated-webp"``, ``"animated-svg"`` or ``None`` for ``data``."""
    if is_animated_gif(data):
        return "animated-gif"
    if is_animated_png(data):
        return "animated-png"
    if is_animated_webp(data):
        return "animated-webp"
    if is_animated_svg(data):
        return "animated-svg"
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

# Installed before any page script runs, in every document. A worker's timers run on the real clock (no
# strategy's clock reaches inside it), so a message it posts can change the page at any real moment.
# Every Worker and SharedWorker the page constructs is recorded with its script URL; the constructors
# otherwise behave exactly as before (instanceof, subclasses and errors included).
WORKER_PROBE_JS = r"""
(() => {
  const workers = [];
  Object.defineProperty(window, '__ftWorkers', { value: workers, configurable: true });
  for (const kind of ['Worker', 'SharedWorker']) {
    const Original = window[kind];
    if (typeof Original !== 'function') continue;
    const Recorded = function (...args) {
      if (!new.target) return Original(...args);  // called without new: throws the browser's own TypeError
      const instance = Reflect.construct(Original, args, new.target);
      let url = String(args[0]);
      try { url = new URL(url, document.baseURI).href; } catch (e) { /* keep the text as given */ }
      workers.push({ kind, url, reported: false });
      return instance;
    };
    Recorded.prototype = Original.prototype;
    Object.setPrototypeOf(Recorded, Original);
    Object.defineProperty(Recorded, 'name', { value: kind });
    window[kind] = Recorded;
  }
})();
"""

# Runs after load (and after an ablation's set-up), and again during the frame loops of strategy A and
# of a budget-only strategy B (``ContentWatch``). Walks the document, every shadow root and every
# same-origin frame (``ftScopes`` from page_scripts.py) and returns what detection needs. A frame whose
# document throws on access is cross-origin, which is exactly how it is recognised. Each image URL,
# canvas, frame and worker is returned once per page: later calls return only what is new. Options:
#   styles  also read the CSS image properties of every element (the costly part);
#   walk    also walk every element for open shadow roots the init script could not see.
_SCAN_PAGE_BODY = r"""
  const { styles, walk } = options || { styles: true, walk: true };
  const result = { images: [], webglCanvases: [], offscreenCanvases: [], crossOriginFrames: [], workers: [],
                   videos: 0, shadowRoots: 0 };
  if (!window.__ftScannedUrls) Object.defineProperty(window, '__ftScannedUrls', { value: new Set(), configurable: true });
  const seenUrls = window.__ftScannedUrls;  // image URLs an earlier call already returned
  // True the first time it is asked about this element and key, false after.
  const firstReport = (el, key) => {
    if (el['__ftReported' + key]) return false;
    el['__ftReported' + key] = true;
    return true;
  };

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

  // An <object> or <embed> showing a same-origin document (an SVG file, say) is not an image: that
  // document is one of the scopes, so strategy A drives its animations like the page's own.
  const showsDocument = (el) => {
    if (el.localName !== 'object' && el.localName !== 'embed') return false;
    try { return Boolean(el.contentDocument || (typeof el.getSVGDocument === 'function' && el.getSVGDocument())); } catch (e) { return false; }
  };

  const scanImages = (win, root, base) => {
    for (const el of root.querySelectorAll(IMAGE_ELEMENTS)) {
      const src = elementImageUrl(el);
      const resolved = src ? resolveUrl(src, base) : '';
      if (!resolved || seenUrls.has(resolved) || ftIsHidden(el) || showsDocument(el)) continue;
      addImage(src, resolved, el.localName, cssPath(el));
    }
    if (!styles) return;
    for (const el of root.querySelectorAll('*')) {
      if (!ftIsHidden(el)) scanStyleImages(win, el, base);
    }
  };

  const scanCanvasesAndFrames = (root) => {
    for (const canvas of root.querySelectorAll('canvas')) {
      if (ftIsHidden(canvas)) continue;
      const types = canvas.__ftContextTypes || [];
      if (types.some(t => t === 'webgl' || t === 'webgl2' || t === 'experimental-webgl') && firstReport(canvas, 'Webgl')) {
        result.webglCanvases.push(cssPath(canvas));
      }
      if (canvas.__ftOffscreen && firstReport(canvas, 'Offscreen')) result.offscreenCanvases.push(cssPath(canvas));
    }
    for (const frame of root.querySelectorAll('iframe, frame')) {
      if (ftIsHidden(frame)) continue;
      let accessible = false;
      try { accessible = !!frame.contentDocument; } catch (e) { accessible = false; }
      if (!accessible && firstReport(frame, 'Frame')) result.crossOriginFrames.push(frame.getAttribute('src') || frame.src || '(no src)');
    }
  };

  const scanWorkers = (win) => {
    for (const worker of win.__ftWorkers || []) {
      if (worker.reported) continue;
      worker.reported = true;
      result.workers.push({ kind: worker.kind, url: worker.url });
    }
  };

  const scopes = ftScopes(window, walk);
  for (const { win, root } of scopes) {
    if (root.nodeType === 11) result.shadowRoots++;  // 11: a shadow root
    const base = (root.nodeType === 9 ? root : root.ownerDocument).baseURI;  // 9: a document
    scanImages(win, root, base);
    scanCanvasesAndFrames(root);
    result.videos += root.querySelectorAll('video').length;
  }
  for (const win of new Set(scopes.map((scope) => scope.win))) scanWorkers(win);
  return result;
"""

SCAN_PAGE_JS = "(options) => {\n" + PAGE_HELPERS_JS + _SCAN_PAGE_BODY + "}\n"

#: Arguments of ``SCAN_PAGE_JS``: the first scan, the per-frame element scan and the once-a-second full scan.
FIRST_SCAN = {"styles": True, "walk": True}
ELEMENT_SCAN = {"styles": False, "walk": False}
FULL_SCAN = {"styles": True, "walk": False}


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


def _read_timeout_ms(deadline: float | None) -> float:
    """Real milliseconds one network read may take: ``IMAGE_READ_TIMEOUT_MS``, or less when ``deadline``
    (a ``time.monotonic()`` instant, where the detection budget runs out) comes sooner."""
    if deadline is None:
        return IMAGE_READ_TIMEOUT_MS
    return min(IMAGE_READ_TIMEOUT_MS, (deadline - time.monotonic()) * 1000)


def _read_image_head(page: Any, url: str, limit: int, local_ok: bool, deadline: float | None = None) -> bytes | None:
    """At most ``limit`` bytes of ``url`` (local file, data: or blob: URL, or over the page's network).

    ``local_ok`` comes from ``local_reads_allowed``; without it a ``file:`` URL is not touched. A network
    read gives up at ``deadline`` (see ``_read_timeout_ms``); ``None`` means it could not be read.
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
            timeout_ms = _read_timeout_ms(deadline)
            if timeout_ms < 1:  # the budget is spent (and Playwright reads a timeout of 0 as "no limit")
                return None
            # A Range header asks the server for the head only; servers that ignore it send the whole
            # body, which is sliced below, so the result is the same either way.
            response = page.request.get(
                url, headers={"Range": f"bytes=0-{limit - 1}"}, timeout=timeout_ms, fail_on_status_code=False
            )
            return response.body()[:limit]
    except Exception:  # unreadable image: nothing to classify, reported by the caller as a note
        return None
    return None


def _unreadable(raw: str) -> tuple[str, str]:
    """The verdict for an image whose bytes could not be read: it could hold an animation (safe direction)."""
    return f"image-unverified: {raw}", f"could not read image bytes to check for animation: {raw}"


def _classify_image(page: Any, image: dict[str, str], local_ok: bool, deadline: float | None = None) -> tuple[str | None, str | None]:
    """``(uncontrollable entry, note)`` for one image found by the scan; ``(None, None)`` when it is still.

    Network reads give up at ``deadline`` (the detection budget's end; ``None``: no budget).
    """
    raw, url = image["raw"], image["resolved"]
    if urlparse(url).scheme == "file" and not local_ok:
        return None, f"animation check skipped for a local image URL (the audited page is not a local file, or local files are not allowed): {raw}"
    head = _read_image_head(page, url, SNIFF_LIMIT, local_ok, deadline)
    if head is None:
        # Safe direction, as for large GIFs below: bytes we could not read could hold an animation.
        return _unreadable(raw)
    if gif_needs_more_bytes(head, SNIFF_LIMIT) or svg_needs_more_bytes(head, SNIFF_LIMIT):
        # The first frame of this GIF, or this SVG's markup, goes on past the first 64 KB: read up to 4 MB.
        longer = _read_image_head(page, url, GIF_SNIFF_LIMIT, local_ok, deadline)
        if not longer:
            # The first 64 KB end in the middle of the first frame or the markup, so they cannot say "still".
            return _unreadable(raw)
        head = longer
        if gif_needs_more_bytes(head, GIF_SNIFF_LIMIT):
            # Safe direction: frames beyond 4 MB could animate, so the image counts as uncontrollable.
            return f"gif-unverified: {raw}", f"GIF frames beyond the first {GIF_SNIFF_LIMIT} bytes were not checked: {raw}"
        if svg_needs_more_bytes(head, GIF_SNIFF_LIMIT):
            return f"svg-unverified: {raw}", f"SVG markup beyond the first {GIF_SNIFF_LIMIT} bytes was not checked for animation: {raw}"
    kind = sniff_animated_image(head)
    return (f"{kind}: {raw}" if kind is not None else None), None


#: Classification results shared by the attempts of one capture, keyed by (URL as written, resolved
#: URL): a strategy that falls through to the next one does not read the same images again.
ImageVerdicts = dict[tuple[str, str], tuple[str | None, str | None]]


class ReadBudget:
    """Real time left for detection's image reads (``DETECTION_BUDGET_S``). A capture makes one and every
    ``ContentWatch`` of its attempts draws on it, so the silent reads of a whole capture stay bounded."""

    def __init__(self, seconds: float | None = None) -> None:
        self.seconds = DETECTION_BUDGET_S if seconds is None else seconds
        self.spent = 0.0  # real seconds the reads have taken so far

    def remaining_s(self) -> float:
        return max(0.0, self.seconds - self.spent)


# ------------------------------------------------------------------------------- entry points
class ContentWatch:
    """Uncontrollable content of one loaded page: the first scan before frame 0, then later checks.

    ``first_scan`` is ``find_uncontrollable``. ``recheck`` looks again during the frame loop and returns
    only what is new since the last look (the page remembers which URLs, canvases, frames and workers it
    has already returned). ``verdicts`` is a classification cache and ``budget`` the time left for image
    reads; a capture shares both between its attempts. At most ``MAX_IMAGE_URLS`` image URLs are read per
    page, and none once the budget is spent; the others count as 'images-unverified'. ``carried`` lists
    uncontrollable content that earlier attempts of the same capture found (some only during their frame
    loops): the first scan reports it as well, so a later strategy that needs controllable content steps
    aside for it too (ADR-046).
    """

    def __init__(
        self,
        allow_local_files: bool = False,
        verdicts: ImageVerdicts | None = None,
        budget: ReadBudget | None = None,
        carried: Iterable[str] = (),
    ) -> None:
        self.allow_local_files = allow_local_files
        self.verdicts: ImageVerdicts = {} if verdicts is None else verdicts
        self.budget = ReadBudget() if budget is None else budget
        self.carried = list(carried)
        self._seen: set[str] = set()  # resolved image URLs already judged on this page
        self._read = 0                # how many of them were read (at most MAX_IMAGE_URLS)

    def first_scan(self, page: "Page") -> tuple[list[str], list[str]]:
        """Scan a freshly loaded page; ``(uncontrollable, notes)`` as for ``find_uncontrollable``, plus the
        carried entries this scan did not find itself."""
        scan = page.evaluate(SCAN_PAGE_JS, FIRST_SCAN)
        uncontrollable, notes = self._judge(page, scan)
        if scan.get("shadowRoots"):
            notes.append(f"detection searched {scan['shadowRoots']} shadow root(s) (open or closed)")
        carried = [entry for entry in self.carried if entry not in uncontrollable]
        if carried:
            notes.append(
                "uncontrollable content an earlier strategy of this capture found counts here too, although "
                "this attempt's first scan did not see it: " + "; ".join(carried)
            )
        return uncontrollable + carried, notes

    def recheck(self, page: "Page", full: bool, requested_images: Iterable[str] = ()) -> tuple[list[str], list[str]]:
        """What became uncontrollable since the last look: ``(new entries, notes)``.

        ``full`` adds the computed-style walk for CSS images (costly: strategy A asks once per captured
        second). ``requested_images`` are image URLs the page fetched meanwhile; they catch a CSS image
        that comes and goes between two full scans.
        """
        scan = page.evaluate(SCAN_PAGE_JS, FULL_SCAN if full else ELEMENT_SCAN)
        scan["images"] = list(scan["images"]) + [
            {"raw": url, "resolved": url, "kind": "request", "selector": ""} for url in requested_images
        ]
        return self._judge(page, scan)

    def _judge(self, page: "Page", scan: dict[str, Any]) -> tuple[list[str], list[str]]:
        """Turn one scan result into uncontrollable entries and notes (images first, as the scan lists them)."""
        uncontrollable, notes = self._judge_images(page, scan["images"])
        uncontrollable.extend(f"webgl-canvas: {selector}" for selector in scan["webglCanvases"])
        uncontrollable.extend(f"offscreen-canvas: {selector}" for selector in scan["offscreenCanvases"])
        uncontrollable.extend(f"cross-origin-iframe: {src}" for src in scan["crossOriginFrames"])
        uncontrollable.extend(
            f"{'shared-worker' if worker['kind'] == 'SharedWorker' else 'worker'}: {worker['url']}" for worker in scan.get("workers", [])
        )
        return uncontrollable, notes

    def _judge_images(self, page: "Page", images: list[dict[str, str]]) -> tuple[list[str], list[str]]:
        """Read and classify each image URL not judged before, up to ``MAX_IMAGE_URLS`` per page and while
        the read budget lasts."""
        local_ok = local_reads_allowed(page.url, self.allow_local_files)
        uncontrollable: list[str] = []
        notes: list[str] = []
        past_cap = out_of_time = 0
        for image in images:
            if image["resolved"] in self._seen:
                continue
            self._seen.add(image["resolved"])
            if self._read >= MAX_IMAGE_URLS:
                past_cap += 1
                continue
            verdict = self._verdict(page, image, local_ok)
            if verdict is None:
                out_of_time += 1
                continue
            self._read += 1
            entry, note = verdict
            uncontrollable.extend([entry] if entry else [])
            notes.extend([note] if note else [])
        # Safe direction, as for an unreadable image: any image URL left unread could be animated.
        if past_cap:
            uncontrollable.append(f"images-unverified: {past_cap} image URL(s) beyond the first {MAX_IMAGE_URLS} were not checked for animation")
            notes.append(f"only the first {MAX_IMAGE_URLS} of {len(self._seen)} image URLs were checked for animation")
        if out_of_time:
            budget = f"{self.budget.seconds:g} s"
            uncontrollable.append(f"images-unverified: {out_of_time} image URL(s) not checked within the {budget} detection budget")
            notes.append(f"detection's image reads used up their {budget} of real time; {out_of_time} image URL(s) were not checked for animation")
        return uncontrollable, notes

    def _verdict(self, page: "Page", image: dict[str, str], local_ok: bool) -> tuple[str | None, str | None] | None:
        """The image's classification, from the shared cache or read now; ``None`` once the budget is spent.

        The time a read takes is charged to the budget, and a read gives up when the budget runs out."""
        key = (image["raw"], image["resolved"])
        if key in self.verdicts:
            return self.verdicts[key]
        remaining = self.budget.remaining_s()
        if remaining <= 0:
            return None
        started = time.monotonic()
        self.verdicts[key] = _classify_image(page, image, local_ok, deadline=started + remaining)
        self.budget.spent += time.monotonic() - started
        return self.verdicts[key]


def find_uncontrollable(page: "Page", allow_local_files: bool = False) -> tuple[list[str], list[str]]:
    """Scan a loaded page and return ``(uncontrollable, notes)``.

    ``uncontrollable`` entries look like ``"animated-gif: /x.gif"``, ``"webgl-canvas: #c"``,
    ``"cross-origin-iframe: https://..."`` or ``"worker: https://.../w.js"``; ``notes`` records anything
    the scan could not verify. ``allow_local_files`` is the capture's switch; see ``local_reads_allowed``
    for how it is used. A capture keeps the ``ContentWatch`` instead, to look again later.
    """
    return ContentWatch(allow_local_files).first_scan(page)


def local_path_from_url(url: str) -> Path | None:
    """Return the filesystem path for a ``file://`` URL, else ``None`` (helper shared with capturer)."""
    parsed = urlparse(url)
    if parsed.scheme != "file":
        return None
    return Path(unquote(parsed.path))
