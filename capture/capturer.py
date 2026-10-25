"""``capture()``: turn a page into a deterministic frame sequence (SPEC.md section 6).

This module owns everything that is the same for every strategy: launching Chromium, building a
fresh context with the exact viewport, loading the page, waiting for fonts, injecting extra CSS,
running the interaction script, suppressing elements for an ablation (ADR-013), scanning for
uncontrollable content, and choosing the strategy (A ``waapi_step`` -> B ``virtual_time`` -> C
``realtime``). The strategy itself only controls time and takes screenshots.
"""

from __future__ import annotations

import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from playwright.sync_api import Browser, BrowserContext, Page, sync_playwright
from playwright.sync_api import Error as PlaywrightError

from capture.detect import CANVAS_PROBE_JS, find_uncontrollable
from capture.frames import FrameSequence
from capture.hashing import sequence_hash
from capture.page_scripts import APPLY_SUPPRESSION_JS, RECORD_SHADOW_ROOT_FN, SHADOW_ROOT_RECORDER_JS
from capture.strategies import (
    FALLBACK_ORDER,
    REGISTRY,
    CaptureSettings,
    CaptureStrategy,
    FrameSink,
    StrategyOutcome,
)

ProgressCallback = Callable[[int, int], None]

#: Where frames go when the caller does not say (a fresh sub-directory per capture).
DEFAULT_OUTPUT_ROOT = Path(".flashtrace") / "captures"

# Chromium switches every FlashTrace capture runs under (ADR-025). Each one removes a way for real
# time or machine load to reach the pixels; all were found by capturing the same page several times
# at once and comparing frame hashes.
RENDERING_SWITCHES: tuple[str, ...] = (
    "--disable-gpu",                  # draw with the CPU: no GPU driver timing or rounding
    "--disable-gpu-compositing",      # put layers together on the CPU as well
    "--disable-gpu-rasterization",    # turn shapes and text into pixels on the CPU as well
    "--disable-threaded-animation",   # the compositor thread must not keep its own copy of an animation
                                      # that FlashTrace has paused and seeks (the measured root cause)
    "--disable-partial-raster",       # re-draw whole tiles, so a tile never depends on what it held before
)


def launch_chromium(browser_type: Any, extra: tuple[str, ...] | list[str] = ()) -> Browser:
    """Start headless Chromium with FlashTrace's rendering switches plus any strategy-specific ones."""
    return browser_type.launch(headless=True, args=[*RENDERING_SWITCHES, *extra])

#: Milliseconds Playwright may spend loading the page before the capture is an error.
NAVIGATION_TIMEOUT_MS = 30_000


@dataclass
class CaptureMeta:
    """Everything the report needs to know about how the frames were obtained."""

    strategy: str
    deterministic: bool
    frame_hashes: list[str]
    hash: str
    uncontrollable: list[str]
    fallback_reason: str | None
    notes: list[str]
    viewport: tuple[int, int]
    fps: int
    duration_s: float
    frame_count: int
    url: str
    wall_time_s: float

    def to_report_dict(self) -> dict[str, object]:
        """Exactly the ``capture`` object of ``engine/schema/report.schema.json`` (no other keys).

        This is what goes into a report; the schema forbids additional properties there.
        """
        return {
            "strategy": self.strategy,
            "deterministic": self.deterministic,
            "frameCount": self.frame_count,
            "hash": self.hash,
            "frameHashes": list(self.frame_hashes),
            "fallbackReason": self.fallback_reason,
            "uncontrollable": list(self.uncontrollable),
            "notes": list(self.notes),
        }

    def to_report_capture(self) -> dict[str, object]:
        """Older name of ``to_report_dict``, kept so existing callers keep working."""
        return self.to_report_dict()

    def to_dict(self) -> dict[str, object]:
        """The schema's ``capture`` keys plus the capture settings (for logs, not for a report)."""
        data = self.to_report_dict()
        data.update(
            {
                "viewport": {"width": self.viewport[0], "height": self.viewport[1]},
                "fps": self.fps,
                "durationS": self.duration_s,
                "url": self.url,
                "wallTimeS": self.wall_time_s,
            }
        )
        return data


@dataclass
class CaptureResult:
    frames: FrameSequence
    meta: CaptureMeta


@dataclass
class _Attempt:
    """Bookkeeping for one strategy attempt inside ``capture``."""

    name: str
    strategy: CaptureStrategy
    #: True in "auto" mode when a later strategy exists to fall through to.
    may_fall_through: bool
    notes: list[str] = field(default_factory=list)
    uncontrollable: list[str] = field(default_factory=list)
    sink: FrameSink | None = None
    outcome: StrategyOutcome | None = None
    loop_seconds: float = 0.0
    #: ``(kind, reason)`` once the attempt has been given up in favour of the next strategy; ``kind`` is
    #: "skipped" (uncontrollable content), "failed" (the strategy rejected its own result) or "errored".
    abandoned: tuple[str, str] | None = None

    def abandon(self, kind: str, reason: str) -> None:
        self.abandoned = (kind, reason)


@dataclass
class _Chain:
    """The strategies given up so far and why: the source of ``meta.fallbackReason`` (SPEC 6.2)."""

    steps: list[tuple[str, str, str]] = field(default_factory=list)  # (strategy, kind, reason)

    def record(self, attempt: _Attempt) -> None:
        if attempt.abandoned is None:
            raise ValueError("only an abandoned attempt can be recorded in the chain")
        kind, reason = attempt.abandoned
        self.steps.append((attempt.name, kind, reason))

    @property
    def fallback_reason(self) -> str | None:
        """Every reason in order, or ``None`` when the first strategy was used."""
        if not self.steps:
            return None
        return "; ".join(f"{name} {kind}: {reason}" for name, kind, reason in self.steps)

    def notes(self, used: str) -> list[str]:
        """One line naming the whole chain, then one line per abandoned strategy."""
        if not self.steps:
            return []
        chain = " -> ".join(f"{name} ({kind})" for name, kind, _reason in self.steps) + f" -> {used} (used)"
        return [f"strategy chain: {chain}"] + [f"{name} {kind}: {reason}" for name, kind, reason in self.steps]


def _first_line(exc: BaseException) -> str:
    """An exception's message without the multi-line call log Playwright appends."""
    text = str(exc).strip()
    return text.splitlines()[0] if text else type(exc).__name__


# ------------------------------------------------------------------------------------- helpers
def resolve_url(url_or_path: str, allow_local_files: bool = True) -> str:
    """Turn the caller's input into something ``page.goto`` accepts.

    ``http(s)://`` URLs pass through; ``file://`` URLs and existing local paths become ``file://``
    URLs (only when ``allow_local_files`` is true, which a server exposed to others should disable).
    """
    parsed = urlparse(url_or_path)
    if parsed.scheme in ("http", "https"):
        return url_or_path
    if parsed.scheme == "file":
        if not allow_local_files:
            raise ValueError("local files are not allowed for this capture")
        return url_or_path
    if parsed.scheme and parsed.scheme not in ("", "file") and len(parsed.scheme) > 1:
        raise ValueError(f"unsupported URL scheme '{parsed.scheme}' in {url_or_path!r}")
    # Checked before the path is looked at, so a caller without local access gets the same answer
    # whether or not the file exists (the error must not reveal what is on this machine).
    if not allow_local_files:
        raise ValueError("local files are not allowed for this capture")
    path = Path(url_or_path).expanduser()
    if not path.exists():
        raise FileNotFoundError(f"no such file or URL: {url_or_path!r}")
    return path.resolve().as_uri()


def _prepare_out_dir(out_dir: str | Path | None) -> Path:
    """Create (or reuse) the directory that receives the frame PNG files."""
    if out_dir is not None:
        path = Path(out_dir)
        path.mkdir(parents=True, exist_ok=True)
        return path
    DEFAULT_OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(prefix="capture-", dir=DEFAULT_OUTPUT_ROOT))


def _new_context(browser: Browser, viewport: tuple[int, int]) -> BrowserContext:
    """A fresh, isolated browsing context with the exact capture conditions of SPEC 6.2.

    device_scale_factor=1 so one CSS pixel is one image pixel (the WCAG window is in CSS pixels);
    light colour scheme; reduced motion is *not* requested, because the audit must see the motion
    users without that preference see.
    """
    return browser.new_context(
        viewport={"width": viewport[0], "height": viewport[1]},
        device_scale_factor=1,
        color_scheme="light",
        reduced_motion="no-preference",
        java_script_enabled=True,
        ignore_https_errors=True,
    )


def _settle_page(page: Page, attempt: _Attempt, settings: CaptureSettings) -> None:
    """After load: fonts ready, extra CSS in place, interaction script finished."""
    # Fonts arriving after frame 0 would change text pixels mid-capture for no page-authored reason.
    attempt.notes.extend(attempt.strategy.wait_for_fonts(page))
    if settings.extra_css:
        # A <style> element, exactly like a page-authored stylesheet, so ablation and fix overrides
        # take part in the normal cascade (they rely on !important to win).
        page.add_style_tag(content=settings.extra_css)
        attempt.notes.append("extra CSS injected as a <style> element before frame 0")
    if settings.interaction_script:
        attempt.notes.extend(attempt.strategy.run_interaction(page, settings.interaction_script))


def _suppression_note(selectors: tuple[str, ...], counts: dict[str, Any], every_frame: bool) -> str:
    """One readable line saying what an ablation's suppression did (ADR-013)."""
    if every_frame:
        when = "re-applied every frame; their animations are held at their frame-0 state by the frame loop"
        animations = ""
    else:
        when = "applied once before frame 0; animations created later on suppressed elements are not stopped"
        animations = f"{counts['animations']} animation(s)/transition(s) paused where they stood, "
    note = (
        f"ablation: suppressed selectors {list(selectors)} ({when}): "
        f"{counts['elements']} element(s) marked data-ft-suppressed, "
        f"{animations}"
        f"{counts['media']} media element(s) hidden, "
        f"{counts['svgTimelines']} SVG timeline(s) held at 0 s, "
        f"{counts['smilElements']} SMIL animation element(s) removed, "
        f"{counts['scripted']} element(s) without animations hidden as script-driven"
    )
    if counts["unmatchedSelectors"]:
        note += f"; selector(s) matching no element: {counts['unmatchedSelectors']}"
    return note


def _apply_suppression(page: Page, attempt: _Attempt, settings: CaptureSettings) -> None:
    """Ablation set-up (ADR-013), after the page has settled and before detection and frame 0.

    Elements matching ``settings.suppress_selectors`` get ``data-ft-suppressed="1"``. Their animations
    and CSS transitions keep their frame-0 state: strategy A holds them itself every frame, the other
    strategies pause them here. Media on or inside them is hidden, and so is a suppressed element that
    has no animation at all (page script moves it); their SVG timelines are held at 0
    (``capture/page_scripts.py``). An invalid selector is the caller's mistake: ValueError.
    """
    every_frame = attempt.strategy.reapplies_suppression
    counts: dict[str, Any] = page.evaluate(
        APPLY_SUPPRESSION_JS, {"selectors": list(settings.suppress_selectors), "pauseAnimations": not every_frame}
    )
    if counts["invalidSelectors"]:
        raise ValueError(f"suppress_selectors contains invalid CSS selector(s): {counts['invalidSelectors']}")
    attempt.notes.append(_suppression_note(settings.suppress_selectors, counts, every_frame))


def _closed_shadow_root_ids(root: dict[str, Any]) -> list[int]:
    """``backendNodeId`` of every closed shadow root in a ``DOM.getDocument`` tree (walked with a stack,
    because a deep page could exceed Python's recursion limit)."""
    found: list[int] = []
    stack = [root]
    while stack:
        node = stack.pop()
        for shadow in node.get("shadowRoots", []):
            if shadow.get("shadowRootType") == "closed":
                found.append(shadow["backendNodeId"])
            stack.append(shadow)
        stack.extend(node.get("children", []))
        if "contentDocument" in node:
            stack.append(node["contentDocument"])
    return found


def _register_declarative_shadow_roots(page: Page, attempt: _Attempt) -> None:
    """Make closed shadow roots written in HTML (``<template shadowrootmode="closed">``) searchable.

    The HTML parser creates them without calling ``attachShadow``, so ``SHADOW_ROOT_RECORDER_JS`` never
    sees them and no page script can reach them; the DevTools protocol can (``DOM.getDocument`` with
    ``pierce``). Each one is handed to ``RECORD_SHADOW_ROOT_FN``, after which strategy A drives its
    animations and detection scans it like any other recorded root (ADR-022). Runs after load, so a
    closed root that HTML inserted later by script creates is not found (the double-shot check remains).
    """
    try:
        session = page.context.new_cdp_session(page)
        try:
            document = session.send("DOM.getDocument", {"depth": -1, "pierce": True})
            added = 0
            for backend_id in _closed_shadow_root_ids(document["root"]):
                handle = session.send("DOM.resolveNode", {"backendNodeId": backend_id})["object"]
                result = session.send(
                    "Runtime.callFunctionOn", {"objectId": handle["objectId"], "functionDeclaration": RECORD_SHADOW_ROOT_FN}
                )
                added += 1 if result.get("result", {}).get("value") is True else 0
        finally:
            session.detach()
    except PlaywrightError as exc:
        attempt.notes.append(f"could not look for closed shadow roots written in HTML: {_first_line(exc)}")
        return
    if added:
        attempt.notes.append(f"{added} closed shadow root(s) written in HTML found through the DevTools protocol and searched")


# ------------------------------------------------------------------------------------- entry point
def _validate_arguments(
    duration_s: float,
    fps: int,
    viewport: tuple[int, int],
    strategy: str,
    suppress_selectors: list[str] | None,
) -> None:
    """Reject impossible capture arguments before any browser work (raises ValueError)."""
    if duration_s <= 0:
        raise ValueError("duration_s must be positive")
    if fps < 1:
        raise ValueError("fps must be at least 1")
    if int(viewport[0]) < 1 or int(viewport[1]) < 1:
        raise ValueError("viewport must be at least 1x1")
    if strategy != "auto" and strategy not in FALLBACK_ORDER:
        raise ValueError(f"unknown strategy {strategy!r}; expected 'auto' or one of {FALLBACK_ORDER}")
    if suppress_selectors is not None and not all(isinstance(s, str) and s.strip() for s in suppress_selectors):
        raise ValueError("suppress_selectors must be a list of non-empty CSS selector strings")


def capture(
    url_or_path: str,
    duration_s: float = 10,
    viewport: tuple[int, int] = (1024, 768),
    fps: int = 60,
    interaction_script: str | None = None,
    strategy: str = "auto",
    out_dir: str | Path | None = None,
    extra_css: str | None = None,
    progress: ProgressCallback | None = None,
    allow_local_files: bool = True,
    browser: Browser | None = None,
    suppress_selectors: list[str] | None = None,
) -> CaptureResult:
    """Capture ``url_or_path`` as ``round(duration_s * fps)`` frames of animation time.

    ``strategy`` is ``"auto"`` (A, falling through to B then C) or one strategy name. ``browser`` lets
    a caller that captures many times (ablation, fix verification) reuse one Chromium process; each
    capture still gets a fresh context. Frames are written to ``out_dir`` (default: a new directory
    under ``.flashtrace/captures/``). ``suppress_selectors`` is the ablation hook of ADR-013: elements
    matching any of these CSS selectors keep their frame-0 state in the capture, or are hidden when
    only script moves them (see ``_apply_suppression``).
    """
    _validate_arguments(duration_s, fps, viewport, strategy, suppress_selectors)
    started = time.perf_counter()
    url = resolve_url(url_or_path, allow_local_files)
    target_dir = _prepare_out_dir(out_dir)
    settings = CaptureSettings(
        url=url,
        duration_s=float(duration_s),
        viewport=(int(viewport[0]), int(viewport[1])),
        fps=int(fps),
        interaction_script=interaction_script,
        extra_css=extra_css,
        forced=strategy != "auto",
        allow_local_files=allow_local_files,
        suppress_selectors=tuple(suppress_selectors or ()),
    )
    names: tuple[str, ...] = FALLBACK_ORDER if strategy == "auto" else (strategy,)
    auto = strategy == "auto"

    if browser is not None:
        return _capture_with_browser(browser, settings, names, auto, target_dir, progress, started)
    with sync_playwright() as playwright:
        own_browser = launch_chromium(playwright.chromium)
        try:
            return _capture_with_browser(own_browser, settings, names, auto, target_dir, progress, started)
        finally:
            own_browser.close()


def _capture_with_browser(
    browser: Browser,
    settings: CaptureSettings,
    names: tuple[str, ...],
    auto: bool,
    target_dir: Path,
    progress: ProgressCallback | None,
    started: float,
) -> CaptureResult:
    """Try the strategies in ``names`` in order until one produces frames it stands behind.

    SPEC 6.2 fall-through A -> B -> C. A strategy is given up ("abandoned") when the page holds content
    it cannot control, when it rejects its own frames (double-shot or page-clock check), when it cannot
    load the page under its time source, or when it raises; every reason is kept in the chain and
    written to ``meta.fallbackReason`` and ``meta.notes``. The last strategy (or a forced one) is never
    abandoned: its result is returned as it is, and its exceptions propagate.
    """
    chain = _Chain()
    for position, name in enumerate(names):
        strategy_class = REGISTRY.get(name)
        if strategy_class is None:
            reason = f"; fallback reason: {chain.fallback_reason}" if chain.fallback_reason else ""
            raise NotImplementedError(f"capture strategy '{name}' is not implemented yet{reason}")
        attempt = _Attempt(name=name, strategy=strategy_class(), may_fall_through=auto and position < len(names) - 1)
        try:
            _run_attempt(browser, attempt, settings, target_dir, progress)
        except (PlaywrightError, RuntimeError, OSError) as exc:
            if not attempt.may_fall_through:
                raise
            attempt.abandon("errored", f"{type(exc).__name__}: {_first_line(exc)}")
        if attempt.abandoned is not None:
            chain.record(attempt)
            continue
        return _build_result(attempt, chain, settings, started)

    # Unreachable: the loop either returns or raises. Kept so the function has an explicit end.
    raise RuntimeError("no capture strategy produced frames")


def _browser_for(attempt: _Attempt, browser: Browser) -> tuple[Browser, bool]:
    """The browser an attempt runs in, and whether this attempt owns (must close) it.

    A strategy that declares ``launch_args`` gets its own Chromium with those switches so the caller's
    browser, and every other strategy, keeps running under the switches it was validated with.
    """
    switches = attempt.strategy.launch_args
    if not switches or not attempt.strategy.needs_launch_args(browser):
        return browser, False
    try:
        dedicated = launch_chromium(browser.browser_type, switches)
    except PlaywrightError as exc:
        attempt.notes.append(f"could not launch a dedicated Chromium with {' '.join(switches)}: {_first_line(exc)}; using the shared browser")
        return browser, False
    attempt.notes.append(f"dedicated Chromium launched with: {' '.join(switches)}")
    return dedicated, True


def _run_attempt(
    browser: Browser,
    attempt: _Attempt,
    settings: CaptureSettings,
    target_dir: Path,
    progress: ProgressCallback | None,
) -> None:
    """Run one strategy end to end in a fresh context; fills ``attempt`` (or abandons it)."""
    attempt_browser, owned = _browser_for(attempt, browser)
    try:
        context = _new_context(attempt_browser, settings.viewport)
        try:
            _load_and_capture(context, attempt, settings, target_dir, progress)
        finally:
            context.close()
    finally:
        if owned:
            attempt_browser.close()


def _load_and_capture(
    context: BrowserContext,
    attempt: _Attempt,
    settings: CaptureSettings,
    target_dir: Path,
    progress: ProgressCallback | None,
) -> None:
    """Prepare, load, scan and capture inside ``context`` (the per-attempt steps of SPEC 6.2)."""
    strategy = attempt.strategy
    # Both probes must be in place before any page script runs: the canvas probe before a WebGL
    # context is requested, the shadow-root recorder before a closed shadow root is created (ADR-022).
    context.add_init_script(CANVAS_PROBE_JS)
    context.add_init_script(SHADOW_ROOT_RECORDER_JS)
    strategy.prepare_context(context, settings)
    page = strategy.create_page(context, settings)
    page.set_default_navigation_timeout(NAVIGATION_TIMEOUT_MS)
    strategy.prepare_page(page, settings)

    load_failure = strategy.navigate(page, settings)
    if load_failure is not None:
        _record_load_failure(attempt, load_failure, target_dir)
        return

    _register_declarative_shadow_roots(page, attempt)
    _settle_page(page, attempt, settings)
    if settings.suppress_selectors:
        # Before detection, so a suppressed (hidden) animated image no longer counts as uncontrollable.
        _apply_suppression(page, attempt, settings)
    if not _scan_page(page, attempt, settings):
        return

    attempt.sink = FrameSink(FrameSequence.new_in_dir(target_dir))
    loop_started = time.perf_counter()
    attempt.outcome = strategy.run(page, settings, attempt.sink, progress)
    attempt.loop_seconds = time.perf_counter() - loop_started
    # Only a strategy that promises determinism can be wrong about it; C's frames are what they are.
    if attempt.outcome.failure_reason and attempt.may_fall_through and strategy.deterministic_by_design:
        attempt.abandon("failed", attempt.outcome.failure_reason)


def _record_load_failure(attempt: _Attempt, reason: str, target_dir: Path) -> None:
    """The strategy could not load the page: fall through in "auto", else return an empty capture."""
    if attempt.may_fall_through:
        attempt.abandon("failed", reason)
        return
    # Forced strategy that cannot even load the page: an empty capture that says why, so the caller
    # (SPEC 6.4: fewer than 2*fps frames => INCONCLUSIVE) still gets the diagnosis in the notes.
    attempt.sink = FrameSink(FrameSequence.new_in_dir(target_dir))
    notes = attempt.strategy.describe_setup() + [reason]
    attempt.outcome = StrategyOutcome(deterministic=False, notes=notes, failure_reason=reason)


def _scan_page(page: Page, attempt: _Attempt, settings: CaptureSettings) -> bool:
    """Look for uncontrollable content (SPEC 6.2); False when the attempt was abandoned because of it."""
    attempt.uncontrollable, detect_notes = find_uncontrollable(page, settings.allow_local_files)
    attempt.notes.extend(detect_notes)
    if not attempt.uncontrollable or not attempt.strategy.requires_controllable_content:
        return True
    reason = "uncontrollable content: " + "; ".join(attempt.uncontrollable)
    if attempt.may_fall_through:
        attempt.abandon("skipped", reason)
        return False
    # The caller insisted. The frames are still taken, but that content runs on clocks the strategy
    # does not control, so the capture is reported as non-deterministic.
    attempt.notes.append(
        f"{attempt.name} was used although uncontrollable content is present: "
        + "; ".join(attempt.uncontrollable)
        + "; deterministic=false because that content runs on clocks this strategy does not control"
    )
    return True


def _build_result(attempt: _Attempt, chain: _Chain, settings: CaptureSettings, started: float) -> CaptureResult:
    """Assemble ``CaptureResult`` from a completed attempt and the chain that led to it."""
    if attempt.sink is None or attempt.outcome is None:
        raise RuntimeError(f"strategy '{attempt.name}' finished without producing an outcome")
    sink, outcome = attempt.sink, attempt.outcome
    notes = chain.notes(attempt.name) + attempt.notes + outcome.notes
    notes.append(f"frame loop took {attempt.loop_seconds:.2f} s for {len(sink.hashes)} frames")
    forced_over_uncontrollable = bool(attempt.uncontrollable) and attempt.strategy.requires_controllable_content
    meta = CaptureMeta(
        strategy=attempt.strategy.name,
        deterministic=outcome.deterministic and attempt.strategy.deterministic_by_design and not forced_over_uncontrollable,
        frame_hashes=list(sink.hashes),
        hash=sequence_hash(sink.hashes),
        uncontrollable=list(attempt.uncontrollable),
        fallback_reason=chain.fallback_reason,
        notes=notes,
        viewport=settings.viewport,
        fps=settings.fps,
        duration_s=settings.duration_s,
        frame_count=len(sink.hashes),
        url=settings.url,
        wall_time_s=time.perf_counter() - started,
    )
    return CaptureResult(frames=sink.frames, meta=meta)
