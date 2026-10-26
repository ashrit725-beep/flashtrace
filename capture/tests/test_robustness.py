"""Capture robustness: content on clocks strategy A does not control, and pages that never finish loading
(SPEC 6.2, 6.4; the capture review of 2026-10-25).

* A ``<video>`` is driven at its ``playbackRate`` from when the page started it, and honours the page's
  ``play()``, ``pause()``, ``currentTime`` and ``playbackRate`` (strategy A).
* Animated SVG images and Web Workers are uncontrollable: found before frame 0, or, like any animated
  image, during the frame loop when they appear later.
* An HTTP error status of the page is an error, not a page to audit.
* A subresource or a web font that never answers does not hang the capture.

Pages follow the synthetic-page contract of test_page_control.py: the element that flashes is the
400x300 box at (100, 100), read at its centre pixel. ``capture/pages/strobe_2hz.webm.bin`` is a WebM
video (2 s, 24 fps, 400x300, VP8, black for 250 ms then white), kept under that name because ``*.webm`` is
gitignored for the benchmark's generated videos; tests copy it as ``strobe_2hz.webm``. It was made with
the benchmark's encoder: ``bench.ffmpeg.encode_flash_video(find_ffmpeg(), path, 400, 300, (0, 0, 0),
(255, 255, 255), rate_hz=2, fps=24, seconds=2)``.
"""

from __future__ import annotations

import math
import re
import shutil
import threading
import time
from collections.abc import Callable, Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import ClassVar

import pytest
from playwright.sync_api import Browser

from capture import CaptureResult, PageLoadError, capture

FPS = 60
BOX_XY = (300, 250)
BOX_CSS = "#box { position: absolute; left: 100px; top: 100px; width: 400px; height: 300px; }"
BLINK_CSS = ("#box { background: #000; animation: ft-blink 250ms steps(1, end) infinite; } "
             "@keyframes ft-blink { 0% { background: #000; } 50% { background: #fff; } 100% { background: #000; } }")


# ------------------------------------------------------------------------------- helpers
def page_html(body: str, script: str = "", style: str = "") -> str:
    """A white page with the box rule, plus the given markup, script and CSS."""
    return ('<!doctype html><html><head><meta charset="utf-8"><style>html, body { margin: 0; background: #fff; '
            f"overflow: hidden; }} {BOX_CSS} {style}</style></head><body>{body}<script>{script}</script></body></html>")


# Files of capture/pages stored under another name (see the module docstring).
STORED_AS = {"strobe_2hz.webm": "strobe_2hz.webm.bin"}


def write_page(folder: Path, html: str, assets: tuple[str, ...] = ()) -> Path:
    """``page.html`` in ``folder`` with copies of the named files from capture/pages next to it."""
    pages = Path(__file__).resolve().parent.parent / "pages"
    for name in assets:
        shutil.copy(pages / STORED_AS.get(name, name), folder / name)
    page = folder / "page.html"
    page.write_text(html, encoding="utf-8")
    return page


def box_states(result: CaptureResult) -> list[str]:
    """'black', 'white' or 'other[...]' at the box centre in every frame (VP8 keeps black/white within a few steps)."""
    states = []
    for frame in result.frames:
        pixel = frame[BOX_XY[1], BOX_XY[0]]
        states.append("black" if pixel.max() < 16 else "white" if pixel.min() > 239 else f"other{pixel.tolist()}")
    return states


def changes(states: list[str]) -> list[int]:
    return [i for i in range(1, len(states)) if states[i] != states[i - 1]]


def note_matching(notes: list[str], pattern: str) -> str:
    for note in notes:
        if re.search(pattern, note):
            return note
    raise AssertionError(f"no note matches {pattern!r} in {notes}")


def run(target: Path | str, out: Path, browser: Browser, duration_s: float, **options: object) -> CaptureResult:
    return capture(str(target), duration_s=duration_s, fps=FPS, out_dir=out, browser=browser, **options)  # type: ignore[arg-type]


# ------------------------------------------------------------------------------- video (strategy A)
def strobe_state(media_s: float) -> str:
    """capture/pages/strobe_2hz.webm at media time ``media_s``: 250 ms black, 250 ms white, repeating."""
    return "black" if math.floor(media_s * 4 + 1e-9) % 2 == 0 else "white"


def assert_video_shows(states: list[str], media_time: Callable[[float], float]) -> None:
    """Frame i, at (i + 1/4) / FPS s of page time (ADR-029), shows the strobe at ``media_time`` of that
    time; a frame next to a change may show either neighbour, and the number of changes must be exact."""
    expected = [strobe_state(media_time((i + 0.25) / FPS)) for i in range(len(states))]
    boundaries = set(changes(expected))
    for i, (seen, wanted) in enumerate(zip(states, expected, strict=True)):
        if seen != wanted:
            assert i in boundaries or i + 1 in boundaries, f"frame {i}: {seen}, expected {wanted}"
    assert len(changes(states)) == len(boundaries), f"changes at {changes(states)}, expected {sorted(boundaries)}"


VIDEO = '<video id="box" src="strobe_2hz.webm" muted loop playsinline {attributes}></video>'

# name: (video attributes, page script after `const v = ...`, media time at page time t, a note it must leave)
VIDEO_CASES: dict[str, tuple[str, str, Callable[[float], float], str]] = {
    "rate 2": ("autoplay", "v.playbackRate = 2;", lambda t: 2 * t,
               r"^1 video element\(s\) paused, muted and seeked per frame to the media time"),
    "rate 0.5": ("autoplay", "v.playbackRate = 0.5;", lambda t: t / 2,
                 r"from when the page started each one, at its playbackRate\)$"),
    "rate 2 from 500 ms": ("autoplay", "setTimeout(() => { v.playbackRate = 2; }, 500);",
                           lambda t: t if t < 0.5 else 0.5 + 2 * (t - 0.5), r"^1 video playbackRate change\(s\) by the page were applied$"),
    "played at 500 ms": ("", "setTimeout(() => v.play(), 500);", lambda t: max(0.0, t - 0.5),
                         r"^1 video\(s\) started later by the page's play\(\) were driven from that frame$"),
    "paused at 500 ms": ("autoplay", "setTimeout(() => v.pause(), 500);", lambda t: min(t, 0.5),
                         r"^video\(s\) paused by the page were held still at \d+ frame\(s\), first at frame 30$"),
    "seeked at 500 ms": ("autoplay", "setTimeout(() => { v.currentTime = 0.25; }, 500);",
                         lambda t: t if t < 0.5 else 0.25 + (t - 0.5), r"^1 video current time\(s\) assigned by the page were applied$"),
}


@pytest.mark.browser
@pytest.mark.parametrize("case", ["rate 2", "rate 0.5"])
def test_video_plays_at_its_playback_rate_in_every_capture(case: str, tmp_path_factory: pytest.TempPathFactory, browser: Browser) -> None:
    """A 2 Hz video at playbackRate 2 flashes 4 times a second (and 1 Hz at 0.5); both captures agree."""
    attributes, script, media_time, note = VIDEO_CASES[case]
    folder = tmp_path_factory.mktemp("video")
    page = write_page(folder, page_html(VIDEO.format(attributes=attributes), "const v = document.getElementById('box'); " + script),
                      ("strobe_2hz.webm",))
    results = [run(page, tmp_path_factory.mktemp(f"run{n}"), browser, 2) for n in (1, 2)]
    for result in results:
        assert result.meta.strategy == "waapi_step" and result.meta.deterministic is True, result.meta.notes
    assert results[0].meta.frame_hashes == results[1].meta.frame_hashes
    assert_video_shows(box_states(results[0]), media_time)
    note_matching(results[0].meta.notes, note)


@pytest.mark.browser
@pytest.mark.parametrize("case", ["rate 2 from 500 ms", "played at 500 ms", "paused at 500 ms", "seeked at 500 ms"])
def test_video_follows_what_the_page_asks(case: str, tmp_path_factory: pytest.TempPathFactory, browser: Browser) -> None:
    """The page's playbackRate, play(), pause() and currentTime take effect at the frame where they happen."""
    attributes, script, media_time, note = VIDEO_CASES[case]
    folder = tmp_path_factory.mktemp("video")
    page = write_page(folder, page_html(VIDEO.format(attributes=attributes), "const v = document.getElementById('box'); " + script),
                      ("strobe_2hz.webm",))
    result = run(page, tmp_path_factory.mktemp("run"), browser, 1.5)
    assert result.meta.strategy == "waapi_step" and result.meta.deterministic is True, result.meta.notes
    assert_video_shows(box_states(result), media_time)
    note_matching(result.meta.notes, note)


@pytest.mark.browser
def test_video_never_started_keeps_its_first_picture(tmp_path: Path, browser: Browser) -> None:
    """No autoplay and no play(): a viewer sees the first frame for ever, so the capture must too."""
    page = write_page(tmp_path, page_html(VIDEO.format(attributes="")), ("strobe_2hz.webm",))
    result = run(page, tmp_path / "out", browser, 1)
    assert result.meta.strategy == "waapi_step" and result.meta.deterministic is True, result.meta.notes
    assert set(box_states(result)) == {"black"}
    note_matching(result.meta.notes, r"^1 video element\(s\) not started by the page \(no autoplay, no play\(\)\) kept the picture")


# ------------------------------------------------------------------------------- SVG images and workers
SMIL_SVG = ('<svg xmlns="http://www.w3.org/2000/svg" width="400" height="300"><rect width="400" height="300" fill="#000">'
            '<animate attributeName="fill" values="#000;#fff" calcMode="discrete" dur="0.25s" repeatCount="indefinite"/></rect></svg>')


@pytest.mark.browser
def test_animated_svg_image_makes_strategy_a_step_aside(tmp_path: Path, browser: Browser) -> None:
    """Shown through <img>, the SVG animates on the browser's own clock: uncontrollable (SPEC 6.2)."""
    (tmp_path / "strobe.svg").write_text(SMIL_SVG, encoding="utf-8")
    page = write_page(tmp_path, page_html('<img id="box" src="strobe.svg" alt="">'))
    result = run(page, tmp_path / "out", browser, 0.25)
    assert result.meta.strategy != "waapi_step"
    assert result.meta.fallback_reason is not None
    assert result.meta.fallback_reason.startswith("waapi_step skipped: uncontrollable content: animated-svg: "), result.meta.fallback_reason


WORKER = "const w = new Worker(URL.createObjectURL(new Blob(['setInterval(() => postMessage(1), 125)'], { type: 'text/javascript' })));"


@pytest.mark.browser
def test_worker_started_at_load_makes_strategy_a_step_aside(tmp_path: Path, browser: Browser) -> None:
    page = write_page(tmp_path, page_html('<div id="box"></div>', WORKER))
    result = run(page, tmp_path / "out", browser, 0.25)
    assert result.meta.strategy != "waapi_step"
    assert result.meta.fallback_reason is not None
    assert result.meta.fallback_reason.startswith("waapi_step skipped: uncontrollable content: worker: blob:"), result.meta.fallback_reason


# ------------------------------------------------------------------------------- content that appears after frame 0
LATE_GIF = ("setTimeout(() => { const i = new Image(); i.id = 'box'; i.alt = ''; i.src = 'anim.gif'; document.body.append(i); }, 200);"
            "setTimeout(() => document.getElementById('box').remove(), 400);")
LATE_CSS_GIF = "setTimeout(() => document.body.classList.add('on'), 200); setTimeout(() => document.body.classList.remove('on'), 400);"


@pytest.mark.browser
@pytest.mark.parametrize(
    ("body", "script", "style", "entry"),
    [
        ("", LATE_GIF, "", "animated-gif: "),                                       # a new <img> element
        ('<div id="box"></div>', LATE_CSS_GIF, ".on #box { background: url(anim.gif) 0 0 / cover; }", "animated-gif: "),  # its request
        ('<div id="box"></div>', f"setTimeout(() => {{ {WORKER} }}, 200);", "", "worker: blob:"),  # a worker
    ],
    ids=["img element", "css background request", "worker"],
)
def test_content_that_appears_after_frame_0_sends_auto_on(
    tmp_path: Path, browser: Browser, body: str, script: str, style: str, entry: str
) -> None:
    """Shown from 200 ms to 400 ms only, so neither the first scan nor the last frame sees it."""
    page = write_page(tmp_path, page_html(body, script, style), ("anim.gif",))
    result = run(page, tmp_path / "out", browser, 0.75)
    assert result.meta.strategy != "waapi_step"
    reason = result.meta.fallback_reason or ""
    assert re.match(r"waapi_step failed: uncontrollable content appeared after the first scan, at frame 1[23]: " + re.escape(entry), reason), reason


@pytest.mark.browser
def test_late_content_under_forced_strategy_a_is_reported_not_hidden(tmp_path: Path, browser: Browser) -> None:
    """Forced A keeps every frame but says the capture is not deterministic and lists the content."""
    page = write_page(tmp_path, page_html("", LATE_GIF), ("anim.gif",))
    result = run(page, tmp_path / "out", browser, 0.75, strategy="waapi_step")
    meta = result.meta
    assert meta.strategy == "waapi_step" and meta.frame_count == 45
    assert meta.deterministic is False
    assert any(item.startswith("animated-gif: ") for item in meta.uncontrollable), meta.uncontrollable
    note_matching(meta.notes, r"^uncontrollable content appeared after the first scan, at frame 1[23]: animated-gif: ")


@pytest.mark.browser
def test_css_image_without_a_request_is_found_by_the_once_a_second_scan(tmp_path: Path, browser: Browser) -> None:
    """A data: URL GIF as a background from 900 ms to 1200 ms makes no request and is no element of its
    own: the full scan at frame 60 (the first frame of the second second) finds it."""
    import base64

    gif = base64.b64encode((Path(__file__).resolve().parent.parent / "pages" / "anim.gif").read_bytes()).decode("ascii")
    style = f".on #box {{ background: url(data:image/gif;base64,{gif}) 0 0 / cover; }}"
    script = "setTimeout(() => document.body.classList.add('on'), 900); setTimeout(() => document.body.classList.remove('on'), 1200);"
    page = write_page(tmp_path, page_html('<div id="box"></div>', script, style))
    result = run(page, tmp_path / "out", browser, 1.5)
    assert result.meta.strategy != "waapi_step"
    assert "uncontrollable content appeared after the first scan, at frame 60: animated-gif: data:image/gif" in (result.meta.fallback_reason or "")


# ------------------------------------------------------------------------------- pages that do not load cleanly (HTTP)
class _Handler(BaseHTTPRequestHandler):
    """'/<name>' serves what is registered under that name (404 when nothing is), '/hang/...' never answers."""

    pages: ClassVar[dict[str, bytes]] = {}

    def log_message(self, *_args: object) -> None:  # keep the test output clean
        pass

    def do_GET(self) -> None:  # noqa: N802 - the name http.server calls
        path = self.path.partition("?")[0].lstrip("/")
        try:
            if path.startswith("hang/"):
                time.sleep(60)  # longer than any test here; the browser gives up first
                return
            status, body = (200, self.pages[path]) if path in self.pages else (404, b"<!doctype html><h1>Not found</h1>")
            self.send_response(status)
            self.send_header("Content-Type", "image/svg+xml" if path.endswith(".svg") else "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except OSError:  # the browser closed the connection
            pass


@pytest.fixture()
def server() -> Iterator[str]:
    """A loopback-only HTTP server for one test; yields its base URL."""
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}/"
    finally:
        httpd.shutdown()
        httpd.server_close()


@pytest.mark.browser
def test_http_error_status_is_an_error_not_a_page(server: str, tmp_path: Path, browser: Browser) -> None:
    """A mistyped URL must not PASS as the server's error page; no other strategy is tried for it."""
    started = time.monotonic()
    with pytest.raises(PageLoadError, match=r"^the page answered HTTP 404 Not Found: http://127\.0\.0\.1:\d+/nope\.html$"):
        run(server + "nope.html", tmp_path, browser, 1)
    assert time.monotonic() - started < 10


@pytest.mark.browser
def test_animated_svg_document_in_a_same_origin_object_is_driven(server: str, tmp_path_factory: pytest.TempPathFactory, browser: Browser) -> None:
    """Shown through a same-origin <object>, the same SVG is a document whose timeline strategy A drives.
    (Over file:// Chromium gives every file its own origin, so there the object counts as uncontrollable.)"""
    _Handler.pages["strobe.svg"] = SMIL_SVG.encode()
    _Handler.pages["object"] = page_html('<object id="box" data="strobe.svg" type="image/svg+xml"></object>').encode()
    results = [run(server + "object", tmp_path_factory.mktemp(f"run{n}"), browser, 1) for n in (1, 2)]
    for result in results:
        assert result.meta.strategy == "waapi_step" and result.meta.deterministic is True, result.meta.notes
        assert result.meta.uncontrollable == []
    assert results[0].meta.frame_hashes == results[1].meta.frame_hashes
    assert len(changes(box_states(results[0]))) == 7  # 4 Hz for 1 s: 8 half periods


@pytest.fixture()
def short_waits(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every real-time limit that a request which never answers runs into, shortened for the test."""
    from capture import detect
    from capture.strategies import base, network_wait, waapi_step

    monkeypatch.setattr(base, "LOAD_WAIT_MS", 500)
    monkeypatch.setattr(base, "FONT_WAIT_MS", 300)
    monkeypatch.setattr(network_wait, "NETWORK_WAIT_MS", 300)
    monkeypatch.setattr(waapi_step, "MEDIA_READY_TIMEOUT_MS", 300)
    monkeypatch.setattr(detect, "IMAGE_READ_TIMEOUT_MS", 300)


@pytest.mark.browser
@pytest.mark.usefixtures("short_waits")
def test_image_that_never_answers_does_not_stop_the_capture(server: str, tmp_path: Path, browser: Browser) -> None:
    """A stalled pixel holds the load event back for ever; the flash next to it must still be measured."""
    _Handler.pages["stalled"] = page_html('<div id="box"></div><img src="/hang/pixel.png" alt="" width="10" height="10">', style=BLINK_CSS).encode()
    result = run(server + "stalled", tmp_path, browser, 1, strategy="waapi_step")
    meta = result.meta
    assert meta.frame_count == 60 and meta.deterministic is False
    assert len(changes(box_states(result))) == 7  # the 4 Hz box, measured
    note_matching(meta.notes, r"^the page's load event had not fired after 0\.5 s of real time")
    note_matching(meta.notes, r"/hang/pixel\.png")


FONT_STYLE = "@font-face { font-family: Stalled; src: url(/hang/font.woff2) format('woff2'); } p { font-family: Stalled, sans-serif; }"
FONT_SCRIPT = ("window.addEventListener('load', () => { const p = document.createElement('p'); "
               "p.textContent = 'Late headline'; document.body.prepend(p); });")


@pytest.mark.browser
@pytest.mark.usefixtures("short_waits")
@pytest.mark.parametrize("strategy", ["auto", "waapi_step"])
def test_web_font_that_never_answers_does_not_stop_the_capture(server: str, tmp_path: Path, browser: Browser, strategy: str) -> None:
    """The font wait is bounded. page.screenshot waits for fonts before every picture, so strategy A
    cannot capture such a page: "auto" moves on, a forced A returns no frames and says why."""
    _Handler.pages["font"] = page_html('<div id="box"></div>', FONT_SCRIPT, FONT_STYLE + BLINK_CSS).encode()
    started = time.monotonic()
    result = run(server + "font", tmp_path, browser, 0.5, strategy=strategy)
    assert time.monotonic() - started < 25  # the screenshot's own 30 s timeout is never reached
    meta = result.meta
    reason = r"^web fonts were still 'loading' after 0\.3 s of real time; text drawn in them may change when they arrive"
    if strategy == "auto":
        assert meta.strategy != "waapi_step"
        assert re.match(r"waapi_step failed: " + reason[1:] + r"; page\.screenshot waits for web fonts", meta.fallback_reason or ""), meta.fallback_reason
    else:
        assert meta.strategy == "waapi_step" and meta.frame_count == 0 and meta.deterministic is False
        note_matching(meta.notes, reason + r"; page\.screenshot waits for web fonts before every picture, so waapi_step cannot take its frames$")


@pytest.fixture(autouse=True)
def _forget_registered_pages() -> Iterator[None]:
    yield
    _Handler.pages.clear()


# ------------------------------------------------------------------------------- detection budget (ADR-049)
SILENT_HOLD_S = 20  # longer than every limit the budget test runs into


class _SilentHandler(BaseHTTPRequestHandler):
    """Holds every request for SILENT_HOLD_S and then hangs up without an answer. Records the detection
    reads, which ask for an image's head with a Range header (the browser's own image loads do not)."""

    detection_reads: ClassVar[list[str]] = []

    def log_message(self, *_args: object) -> None:
        pass

    def do_GET(self) -> None:  # noqa: N802 - the name http.server calls
        if self.headers.get("Range"):
            _SilentHandler.detection_reads.append(self.path)
        time.sleep(SILENT_HOLD_S)


class _SilentServer(ThreadingHTTPServer):
    block_on_close = False  # closing the server does not wait for the requests it is still holding


@pytest.fixture()
def silent_server() -> Iterator[str]:
    """A loopback server whose answers never come in time; yields its base URL."""
    _SilentHandler.detection_reads.clear()
    httpd = _SilentServer(("127.0.0.1", 0), _SilentHandler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}/"
    finally:
        httpd.shutdown()
        httpd.server_close()


@pytest.mark.browser
@pytest.mark.usefixtures("short_waits")
def test_image_reads_that_never_answer_stay_within_the_detection_budget(
    silent_server: str, tmp_path: Path, browser: Browser, monkeypatch: pytest.MonkeyPatch
) -> None:
    """60 such images, read for up to 5 s each, kept a capture silent for the server watchdog's 300 s before
    frame 0 (ADR-049). All the capture's reads now share DETECTION_BUDGET_S (1 s here, 0.3 s per read); the
    image URLs left unread count as uncontrollable, so the capture finishes and claims no determinism."""
    from capture import detect
    from capture.strategies import virtual_time

    monkeypatch.setattr(detect, "DETECTION_BUDGET_S", 1.0)
    monkeypatch.setattr(virtual_time, "EXPIRY_REAL_CAP_S", 2.0)  # B's virtual clock waits for the held loads
    images = "".join(f'<img src="{silent_server}hang/{n}.png" alt="" width="10" height="10">' for n in range(8))
    page = write_page(tmp_path, page_html('<div id="box"></div>' + images, style=BLINK_CSS))
    started = time.monotonic()
    meta = run(page, tmp_path / "out", browser, 0.5).meta
    assert time.monotonic() - started < 60
    assert meta.deterministic is False and meta.frame_count == 30
    budget_entry = r"images-unverified: [1-7] image URL\(s\) not checked within the 1 s detection budget"
    assert any(re.fullmatch(budget_entry, entry) for entry in meta.uncontrollable), meta.uncontrollable
    # About 1 s / 0.3 s reads for the whole capture (every strategy draws on one budget), never all 8 images.
    assert 1 <= len(_SilentHandler.detection_reads) < 8, _SilentHandler.detection_reads


# ------------------------------------------------------------------------------- strategy B looks again (ADR-046)
@pytest.fixture()
def budget_only_b(monkeypatch: pytest.MonkeyPatch) -> None:
    """Strategy B as on this laptop: no BeginFrameControl, budget-only self-test passed (ADR-023), on any machine."""
    from capture.strategies import virtual_time as vt

    monkeypatch.setattr(vt, "begin_frame_unavailable", lambda _browser: f"{vt.BEGIN_FRAME_UNAVAILABLE}: stand-in for the test")
    monkeypatch.setattr(vt, "budget_mode_unusable", lambda _browser: None)


def insert_gif(at_ms: int, remove_at_ms: int | None = None) -> str:
    """Page script: an animated GIF <img> appended at page time ``at_ms`` (and removed at ``remove_at_ms``)."""
    script = (f"setTimeout(() => {{ const i = new Image(); i.id = 'late'; i.alt = ''; i.src = 'anim.gif'; "
              f"document.body.append(i); }}, {at_ms});")
    if remove_at_ms is not None:
        script += f"setTimeout(() => document.getElementById('late').remove(), {remove_at_ms});"
    return script


@pytest.mark.browser
@pytest.mark.usefixtures("budget_only_b")
@pytest.mark.parametrize("remove_at_ms", [None, 1700], ids=["gif stays", "gif gone before B's frame 0"])
def test_gif_inserted_after_1_5_s_never_ends_in_a_deterministic_capture(
    tmp_path: Path, browser: Browser, remove_at_ms: int | None
) -> None:
    """A finds the GIF at frame 90 and steps aside. B's frames start at page time 2 s (its pre-roll), so its
    scan before frame 0 sees the GIF only while it stays; what A found is carried into B's scan either way,
    so a budget-only B steps aside too and C, which is never deterministic, is used."""
    page = write_page(tmp_path, page_html('<div id="box"></div>', insert_gif(1500, remove_at_ms)), ("anim.gif",))
    meta = run(page, tmp_path / "out", browser, 2).meta
    assert meta.deterministic is False
    assert meta.strategy == "realtime" and meta.frame_count == 120
    note_matching(meta.notes, r"^strategy chain: waapi_step \(failed\) -> virtual_time \(skipped\) -> realtime \(used\)$")
    note_matching(meta.notes, r"^waapi_step failed: uncontrollable content appeared after the first scan, at frame 9[01]: animated-gif: ")
    note_matching(meta.notes, r"^virtual_time skipped: uncontrollable content: animated-gif: ")
    assert any(entry.startswith("animated-gif: ") for entry in meta.uncontrollable), meta.uncontrollable


@pytest.mark.browser
@pytest.mark.usefixtures("budget_only_b")
@pytest.mark.parametrize("mode", ["forced", "auto, B first"])
def test_budget_only_b_looks_again_at_its_checkpoints(tmp_path: Path, browser: Browser, monkeypatch: pytest.MonkeyPatch, mode: str) -> None:
    """B's frame 0 is at page time 2 s, so a GIF inserted at 2.5 s appears at its frame 30; the checkpoint of
    frame 60 (the first frame of the second captured second) finds it. Forced, B keeps its frames and says
    deterministic=false; in auto it steps aside for C. (In the usual order A, whose frames cover page time
    0 to 1.5 s, never sees this GIF and rightly stands behind its frames, so B is put first here.)"""
    from capture import capturer

    page = write_page(tmp_path, page_html('<div id="box"></div>', insert_gif(2500)), ("anim.gif",))
    if mode == "forced":
        meta = run(page, tmp_path / "out", browser, 1.5, strategy="virtual_time").meta
        assert meta.strategy == "virtual_time" and meta.frame_count == 90
        assert meta.deterministic is False
        assert any(entry.startswith("animated-gif: ") for entry in meta.uncontrollable), meta.uncontrollable
        note_matching(meta.notes, r"^uncontrollable content appeared after the first scan, at frame 60: animated-gif: ")
        note_matching(meta.notes, r"^budget-only pictures: clock and double-shot checks at frame 0, every 60th frame")
    else:
        monkeypatch.setattr(capturer, "FALLBACK_ORDER", ("virtual_time", "realtime"))
        meta = run(page, tmp_path / "out", browser, 1.5).meta
        assert meta.strategy == "realtime" and meta.deterministic is False
        note_matching(meta.notes, r"^strategy chain: virtual_time \(failed\) -> realtime \(used\)$")
        reason = meta.fallback_reason or ""
        assert re.search(r"uncontrollable content appeared after the first scan, at frame 60: animated-gif: ", reason), reason
        assert any(entry.startswith("animated-gif: ") for entry in meta.uncontrollable), meta.uncontrollable
