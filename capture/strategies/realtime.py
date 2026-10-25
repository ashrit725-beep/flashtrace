"""Strategy C ``realtime``: record a DevTools screencast and resample it (SPEC.md section 6.2).

How it works, in plain words:

1. ``Page.startScreencast`` asks Chromium to send a PNG of every compositor frame it draws, each with
   the wall-clock time at which it was shown. Every frame is acknowledged (``Page.screencastFrameAck``)
   or Chromium stops sending. The page runs on its ordinary real-time clocks; nothing is seeked.
2. Recording lasts ``duration_s`` real seconds plus a small settle so the last frames arrive.
3. The recording is resampled to the capture grid ``t_i = origin + i / fps``: frame ``i`` is the latest
   screencast frame shown at or before ``t_i`` (before the first timestamp the first frame is repeated).
   Chromium only draws when something changes, so a static page produces one screencast frame that is
   simply held.
4. Where the grid starts. Chromium's compositor draws on the ticks of a 60 Hz clock, so every screencast
   timestamp lies on one 1/60 s lattice, give or take a millisecond or two of jitter (measured: three 4 s
   recordings of a 4 Hz page, 35-41 frames each, all within 0.15 of a tick of one common phase). A grid
   that started exactly at the first frame put every sample on a lattice point, so that jitter decided
   whether a sample showed the frame before or after a change: a 4 Hz strobe measured 5 per second and a
   2.999 Hz one failed. ``sample_origin`` therefore starts the grid half a tick away from the lattice
   (``lattice_phase``), where a timestamp would have to be 8 ms off to change the result.
5. ``deterministic`` is always ``False``: real time jitters, frames can be dropped, and a page that is
   idle between two flashes may be sampled on the wrong side of a change. The report shows the warning
   banner and a PASS becomes INCONCLUSIVE (SPEC 6.4). The notes record how many frames arrived and
   the effective source rate so a reader can judge what the recording could have missed, and say that
   a dropped compositor frame can still move one transition by a frame (a rate one higher or lower).
"""

from __future__ import annotations

import base64
import math
import time
from collections.abc import Sequence
from typing import Any, ClassVar

from playwright.sync_api import CDPSession, Page

from capture.frames import png_to_rgb
from capture.strategies.base import (
    SAMPLE_PHASE,
    CaptureSettings,
    CaptureStrategy,
    FrameSink,
    ProgressCallback,
    StrategyOutcome,
)

#: Extra real seconds recorded after ``duration_s`` so frames shown near the end still arrive.
SETTLE_S = 0.25

#: Real seconds to wait for the first screencast frame before giving up on the screencast.
FIRST_FRAME_CAP_S = 5.0

#: Rate of the clock Chromium's compositor draws on (headless Chromium's default begin-frame interval).
COMPOSITOR_HZ = 60

#: How tightly the timestamps must agree on one lattice phase (1 = all on the same phase, 0 = spread
#: evenly) before ``sample_origin`` trusts it. Below this (for example arrival times used because Chromium
#: sent no swap timestamps) the grid starts a quarter of a sample in, as strategies A and B do (ADR-029).
MIN_LATTICE_AGREEMENT = 0.5


def lattice_phase(timestamps: Sequence[float], hz: int = COMPOSITOR_HZ) -> tuple[float, float]:
    """Where the source frames sit on the compositor's ``1/hz`` s lattice, counted from the first frame.

    Returns ``(phase, agreement)``: ``phase`` in ``[0, 1)`` of a tick is the circular mean of every
    frame's position within its tick (circular, so 0.98 and 0.02 average to 0, not 0.5); ``agreement``
    in ``[0, 1]`` is the length of that mean (1 when every frame has the same phase). Pure.
    """
    if not timestamps:
        raise ValueError("lattice_phase needs at least one timestamp")
    angles = [2 * math.pi * (((t - timestamps[0]) * hz) % 1.0) for t in timestamps]
    x = sum(math.cos(a) for a in angles) / len(angles)
    y = sum(math.sin(a) for a in angles) / len(angles)
    return (math.atan2(y, x) / (2 * math.pi)) % 1.0, math.hypot(x, y)


def sample_origin(timestamps: Sequence[float], fps: int) -> tuple[float, str]:
    """Time of sample 0 (seconds, same clock as ``timestamps``) and a note saying how it was chosen.

    Half a compositor tick after the lattice the frames sit on, so jitter of up to half a tick cannot
    move a sample across a frame (module docstring, step 4). Pure.
    """
    phase, agreement = lattice_phase(timestamps)
    if agreement < MIN_LATTICE_AGREEMENT:
        offset = SAMPLE_PHASE / fps
        how = (f"source frames agree on no {COMPOSITOR_HZ} Hz lattice phase (agreement {agreement:.2f}); "
               f"samples sit {offset * 1000:.1f} ms after the first frame plus whole sample periods")
        return timestamps[0] + offset, how
    offset = ((phase + 0.5) % 1.0) / COMPOSITOR_HZ
    how = (f"source frames sit at phase {phase:.2f} of a {COMPOSITOR_HZ} Hz compositor tick (agreement {agreement:.2f}); "
           f"samples sit {offset * 1000:.1f} ms after the first frame plus whole sample periods, half a tick away from them")
    return timestamps[0] + offset, how


def resample_indices(timestamps: Sequence[float], origin: float, fps: int, count: int) -> list[int]:
    """For each capture frame ``i`` (``t_i = origin + i / fps``) return the index of the latest source
    frame whose timestamp is ``<= t_i``; before the first timestamp the first source frame (index 0) is
    used. ``timestamps`` must be sorted ascending.

    Pure and browser-free so it can be unit-tested with hand-made timestamps.
    """
    if not timestamps:
        raise ValueError("resample_indices needs at least one source frame")
    indices: list[int] = []
    source = 0
    for i in range(count):
        target = origin + i / fps
        # Walk forward while the *next* source frame is still not after the target time.
        while source + 1 < len(timestamps) and timestamps[source + 1] <= target:
            source += 1
        indices.append(source if timestamps[source] <= target else 0)
    return indices


class RealtimeStrategy(CaptureStrategy):
    """Strategy C. See the module docstring."""

    name: ClassVar[str] = "realtime"
    deterministic_by_design: ClassVar[bool] = False
    requires_controllable_content: ClassVar[bool] = False

    def __init__(self) -> None:
        super().__init__()
        self._cdp: CDPSession | None = None
        self._received: list[tuple[float, bytes]] = []

    # ------------------------------------------------------------------ setup
    def prepare_page(self, page: Page, settings: CaptureSettings) -> None:
        """Attach DevTools before navigation so the handler is in place when the screencast starts."""
        self._cdp = page.context.new_cdp_session(page)
        self._cdp.on("Page.screencastFrame", self._on_frame)

    def _on_frame(self, event: dict[str, Any]) -> None:
        """Store one screencast frame (shown-at timestamp in seconds, PNG bytes) and acknowledge it."""
        if self._cdp is None:
            return
        metadata = event.get("metadata") or {}
        # ``timestamp`` is the frame swap time as seconds since the epoch; fall back to arrival time
        # when Chromium omits it, and say so in the notes (see ``run``).
        timestamp = metadata.get("timestamp")
        if timestamp is None:
            timestamp = time.time()
            self._missing_timestamps += 1
        self._received.append((float(timestamp), base64.b64decode(event["data"])))
        self._cdp.send("Page.screencastFrameAck", {"sessionId": event["sessionId"]})

    _missing_timestamps = 0

    # ------------------------------------------------------------------ frames
    def run(
        self,
        page: Page,
        settings: CaptureSettings,
        sink: FrameSink,
        progress: ProgressCallback | None,
    ) -> StrategyOutcome:
        if self._cdp is None:
            raise RuntimeError("prepare_page() must run before the screencast starts")
        total = settings.frame_count
        width, height = settings.viewport
        self._received.clear()
        self._missing_timestamps = 0

        # maxWidth/maxHeight equal to the viewport: Chromium then sends frames at CSS-pixel size
        # (device scale factor is 1) instead of scaling them down.
        self._cdp.send(
            "Page.startScreencast",
            {"format": "png", "everyNthFrame": 1, "maxWidth": width, "maxHeight": height},
        )
        started = time.monotonic()
        while not self._received and time.monotonic() - started < FIRST_FRAME_CAP_S:
            page.wait_for_timeout(5)
        if not self._received:
            self._cdp.send("Page.stopScreencast")
            return StrategyOutcome(deterministic=False, notes=[], failure_reason="screencast delivered no frames")
        # Record for the requested duration of *real* time, counted from the first frame, plus settle.
        page.wait_for_timeout(int((settings.duration_s + SETTLE_S) * 1000))
        self._cdp.send("Page.stopScreencast")
        page.wait_for_timeout(50)  # let frames already in flight arrive and be acknowledged

        received = sorted(self._received, key=lambda item: item[0])
        timestamps = [item[0] for item in received]
        origin, origin_note = sample_origin(timestamps, settings.fps)
        indices = resample_indices(timestamps, origin, settings.fps, total)
        notes = self._describe(received, timestamps, indices, settings) + [origin_note]

        failure: str | None = None
        first_shape = png_to_rgb(received[0][1]).shape
        if first_shape[:2] != (height, width):
            failure = f"screencast frames are {first_shape[1]}x{first_shape[0]} px, not the {width}x{height} viewport"
            notes.append(failure)

        for index, source in enumerate(indices):
            sink.add(received[source][1])
            if progress is not None:
                progress(index + 1, total)
        return StrategyOutcome(deterministic=False, notes=notes, failure_reason=failure)

    def _describe(
        self,
        received: list[tuple[float, bytes]],
        timestamps: list[float],
        indices: list[int],
        settings: CaptureSettings,
    ) -> list[str]:
        """Human-readable facts about the recording for ``meta.notes`` (measured, never estimated)."""
        span = timestamps[-1] - timestamps[0]
        notes = [
            "realtime screencast: frames are wall-clock samples, not seeked states; frames may have been missed",
            "a compositor frame drawn late moves a transition by one frame, so a measured rate can be one flash "
            "per second higher or lower than the page's",
            f"{len(received)} screencast frame(s) received over {span:.3f} s of real time",
        ]
        if len(received) >= 2 and span > 0:
            notes.append(f"effective source rate {(len(received) - 1) / span:.1f} frames/s")
        used = len(set(indices))
        held = sum(1 for a, b in zip(indices, indices[1:], strict=False) if a == b)
        notes.append(
            f"resampled to {settings.fps} fps by holding the latest frame at or before each t_i: "
            f"{used} distinct source frame(s) used, {held} output frame(s) repeat their predecessor"
        )
        unused = len(received) - used
        if unused:
            notes.append(f"{unused} source frame(s) fell between sample times and were not used")
        if self._missing_timestamps:
            notes.append(f"{self._missing_timestamps} frame(s) had no swap timestamp; arrival time was used")
        return notes
