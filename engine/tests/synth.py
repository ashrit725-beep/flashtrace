"""Synthetic frame sequences for the SPEC section 5.10 acceptance tests.

Every acceptance test needs a short "video" of a page (1024x768 unless a test asks for another
size) in which some rectangle switches colour at a known rate.  This module builds those videos
lazily, one ``uint8 [768, 1024, 3]`` frame at a time, so a test never holds a whole sequence in
memory (90 frames of 1024x768 RGB would already be 212 MB).

Nothing here touches the engine: the generator is plain NumPy arithmetic, so a failing
acceptance test always points at the engine, never at the fixture.

Vocabulary
----------
scene   a background colour plus a list of regions
region  a rectangle (x, y, w, h in CSS px) and a function ``colour_at(t_seconds)`` that
        returns the region's (r, g, b) at animation time ``t_seconds``
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable, Iterable, Iterator, Sequence

import numpy as np

RGB = tuple[int, int, int]
ColourAt = Callable[[float], RGB]

# WCAG 2.2 glossary, Note 1 of "general flash and red flash thresholds": "a 341 x 256 pixel
# rectangle anywhere on the displayed screen area when the content is viewed at 1024 x 768
# pixels".  Every scene is exactly this size so the engine's window needs no scaling.
VIEWPORT_W: int = 1024
VIEWPORT_H: int = 768

# Frames per second of animation time (SPEC 5.1 default).  Frame i is at t = i / FPS.
FPS: int = 60

# The engine averages 4x4 px cells (SPEC 5.2).  Regions are aligned to this grid unless a test
# asks otherwise (grid_aligned=False): an unaligned region half covers the cells along its
# edges, which is exactly what the translation-invariance tests of the pixel-exact area need.
CELL_PX: int = 4

# ---------------------------------------------------------------------------------------------
# Fixture colours.  Relative luminance per the WCAG 2.2 glossary, Note 1:
#   "L = 0.2126 * R + 0.7152 * G + 0.0722 * B" where each channel is first linearised as
#   "if RsRGB <= 0.04045 then R = RsRGB/12.92 else R = ((RsRGB+0.055)/1.055) ^ 2.4"
#   with "RsRGB = R8bit/255".
# The numbers in the comments were computed with exactly that formula (wcag_relative_luminance
# below) and the test module re-checks them, so none of them is typed from memory.
# ---------------------------------------------------------------------------------------------
BLACK: RGB = (0, 0, 0)            # L = 0
WHITE: RGB = (255, 255, 255)      # L = 0.2126 + 0.7152 + 0.0722 = 1
RED: RGB = (255, 0, 0)            # L = 0.2126 (only the R term); R/(R+G+B) = 1.0 (saturated red);
                                  # CIE 1976 (u', v') = (0.4508, 0.5229)
GREY_127: RGB = (127, 127, 127)   # 127/255 = 0.4980 -> ((0.4980 + 0.055)/1.055)^2.4 = 0.2122 -> L = 0.2122
                                  # Delta L to RED = 0.0004: the equal-luminance partner for red.
                                  # A grey is achromatic, so its (u', v') is the D65 white point
                                  # (0.1978, 0.4683); distance to RED = hypot(0.2530, 0.0546) = 0.2588 > 0.2.
DARK_RED: RGB = (128, 0, 0)       # 128/255 = 0.5020 -> ((0.5020 + 0.055)/1.055)^2.4 = 0.2159
                                  # -> L = 0.2126 * 0.2159 = 0.0459.  Same chromaticity as RED (R only),
                                  # R/(R+G+B) = 1.0, Delta L to RED = 0.1667 >= 0.10.
GREY_124: RGB = (124, 124, 124)   # 124/255 = 0.4863 -> ((0.4863 + 0.055)/1.055)^2.4 = 0.2016 -> L = 0.2016
GREY_147: RGB = (147, 147, 147)   # 147/255 = 0.5765 -> ((0.5765 + 0.055)/1.055)^2.4 = 0.2918 -> L = 0.2918
                                  # Delta L between GREY_147 and GREY_124 = 0.0902 < 0.10 (item 4).
GREY_235: RGB = (235, 235, 235)   # 235/255 = 0.9216 -> ((0.9216 + 0.055)/1.055)^2.4 = 0.8308 -> L = 0.8308
                                  # >= 0.80, so GREY_235 <-> WHITE (Delta L = 0.169) has no darker
                                  # image "below 0.80" (item 5).


def wcag_relative_luminance(rgb: RGB) -> float:
    """Relative luminance of an 8-bit sRGB colour, straight from the WCAG 2.2 glossary.

    Used only to double-check the fixture colours above; the engine has its own vectorised
    implementation and this one is deliberately independent of it.
    """

    def linearise(c8: int) -> float:
        c = c8 / 255  # WCAG 2.2 glossary: "RsRGB = R8bit/255"
        # WCAG 2.2 glossary: "if RsRGB <= 0.04045 then R = RsRGB/12.92
        #                     else R = ((RsRGB+0.055)/1.055) ^ 2.4"
        if c <= 0.04045:
            return c / 12.92
        return ((c + 0.055) / 1.055) ** 2.4

    r, g, b = (linearise(c) for c in rgb)
    # WCAG 2.2 glossary: "L = 0.2126 * R + 0.7152 * G + 0.0722 * B"
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def constant(colour: RGB) -> ColourAt:
    """A region that keeps one colour for the whole capture."""

    def colour_at(t_seconds: float) -> RGB:
        return colour

    return colour_at


def square_wave(hz: float, colour_a: RGB, colour_b: RGB) -> ColourAt:
    """Toggle between two colours ``hz`` times per second, ``colour_a`` first.

    One full cycle (a -> b -> a) lasts 1/hz seconds, so the colour flips every 1/(2*hz)
    seconds: ``state = floor(t * 2 * hz) % 2``.  At 4 Hz and 60 fps the colour changes at
    frames 8, 15, 23, 30, ... (every 7.5 frames); the engine sees 8 transitions and completes
    4 flashes per second (SPEC 5.5).  At 3 Hz the colour changes every 10 frames.

    Frame times are ``i / fps``.  For the rates the tests use (3 Hz and 4 Hz at 60 fps) the
    product ``t * 2 * hz`` was compared with exact rational arithmetic for every frame below
    600: ``floor`` never lands on the wrong side of an integer, so no rounding guard is needed.
    """

    def colour_at(t_seconds: float) -> RGB:
        state = math.floor(t_seconds * 2 * hz) % 2
        return colour_a if state == 0 else colour_b

    return colour_at


def square_wave_state(frame: int, hz: int, fps: int = FPS) -> int:
    """0 or 1: which colour an exact ``hz`` square wave shows at frame ``frame``.

    Same rule as square_wave() (state = floor(t * 2 * hz) % 2 with t = frame / fps) but in
    whole numbers, floor(frame * 2 * hz / fps), so no rate can land on the wrong side of an
    integer.  Used for the high rates (15-30 Hz) of SPEC 5.10 item 11.
    """
    return (frame * 2 * hz // fps) % 2


def square_wave_exact(hz: int, colour_a: RGB, colour_b: RGB, fps: int = FPS) -> ColourAt:
    """A square wave like square_wave(), computed from the frame number in integer arithmetic.

    scene_frames() calls colour_at(i / fps); round(t * fps) recovers i exactly for any frame
    number a test uses, and square_wave_state() does the rest without floating point.
    """

    def colour_at(t_seconds: float) -> RGB:
        frame = round(t_seconds * fps)
        return colour_a if square_wave_state(frame, hz, fps) == 0 else colour_b

    return colour_at


@dataclass(frozen=True)
class Region:
    """A rectangle of the page with its own colour schedule.  Units are CSS px."""

    x: int
    y: int
    w: int
    h: int
    colour_at: ColourAt
    grid_aligned: bool = True  # False lets x, y, w, h fall anywhere, off the 4 px cell grid

    def __post_init__(self) -> None:
        if self.grid_aligned:
            for name in ("x", "y", "w", "h"):
                if getattr(self, name) % CELL_PX != 0:
                    raise ValueError(f"Region.{name} must be a multiple of {CELL_PX} px")
        if self.x < 0 or self.y < 0 or self.w <= 0 or self.h <= 0:
            raise ValueError("Region must start inside the page and have a positive width and height")
        if self.x + self.w > VIEWPORT_W or self.y + self.h > VIEWPORT_H:
            raise ValueError("Region does not fit inside the 1024x768 viewport")

    @property
    def cells(self) -> int:
        """How many 4x4 px cells the region covers (exact only for a grid-aligned region)."""
        if not self.grid_aligned:
            raise ValueError("an unaligned region covers fractions of cells; use .pixels")
        return (self.w // CELL_PX) * (self.h // CELL_PX)

    @property
    def pixels(self) -> int:
        """How many pixels the region covers."""
        return self.w * self.h


def full_screen(colour_at: ColourAt) -> Region:
    """A region covering the whole viewport."""
    return Region(0, 0, VIEWPORT_W, VIEWPORT_H, colour_at)


def frame_count(duration_s: float, fps: int = FPS) -> int:
    """Number of frames in a capture of ``duration_s`` seconds (1.5 s at 60 fps -> 90)."""
    return round(duration_s * fps)


def scene_frames(
    background: RGB,
    regions: Sequence[Region],
    duration_s: float,
    fps: int = FPS,
    size: tuple[int, int] = (VIEWPORT_W, VIEWPORT_H),
) -> Iterator[np.ndarray]:
    """Yield the scene's frames one at a time as ``uint8 [height, width, 3]`` arrays.

    ``size`` is (width, height) in px, 1024x768 by default; a shorter page (SPEC 5.10 item 14)
    passes e.g. (1024, 100), and every region must then fit inside it.  Frame ``i`` is rendered
    at ``t = i / fps`` (SPEC 5.1).  Regions are painted in list order, so a later region covers
    an earlier one where they overlap.  Each frame is a fresh array: the engine may keep a
    reference to the previous frame and must not see it change.
    """
    width, height = size
    for region in regions:
        if region.x + region.w > width or region.y + region.h > height:
            raise ValueError(f"region {region.x},{region.y} {region.w}x{region.h} does not fit {width}x{height}")
    base = np.empty((height, width, 3), dtype=np.uint8)
    base[...] = background
    for i in range(frame_count(duration_s, fps)):
        t_seconds = i / fps
        frame = base.copy()
        for region in regions:
            frame[region.y:region.y + region.h, region.x:region.x + region.w] = region.colour_at(t_seconds)
        yield frame


def cosine_pulse(frame: int, hz: float, trough_frame: float, fps: int = FPS) -> float:
    """0 at ``trough_frame`` (and every 1/hz s after it), 1 half a period later: a smooth eased pulse.

    0.5 - 0.5 * cos(2 * pi * hz * (frame - trough_frame) / fps) - the shape of CSS ease-in-out
    swinging back and forth, which is how a smooth CSS pulse looks when sampled frame by frame.
    """
    return 0.5 - 0.5 * math.cos(2 * math.pi * hz * (frame - trough_frame) / fps)


def split_pulse_frames(
    hz: float, trough_frame: float, n_frames: int, peaks: tuple[int, int] = (120, 255)
) -> Iterator[np.ndarray]:
    """Grey frames whose left half pulses up to byte ``peaks[0]`` and right half up to ``peaks[1]``.

    Both halves follow the same cosine_pulse, but each is rounded to whole bytes, so the dim half
    and the bright half reach their extreme byte on frames one apart: one flash whose cells
    complete on two neighbouring frames.
    """
    for i in range(n_frames):
        level = cosine_pulse(i, hz, trough_frame)
        frame = np.zeros((VIEWPORT_H, VIEWPORT_W, 3), dtype=np.uint8)
        frame[:, : VIEWPORT_W // 2] = round(peaks[0] * level)
        frame[:, VIEWPORT_W // 2 :] = round(peaks[1] * level)
        yield frame


# ADR-029: frame i shows the page a quarter of the way into its slot, at (i + 1/4) / fps seconds.
SAMPLE_PHASE: float = 0.25


def eased_pulse_level(t_seconds: float, hz: float, phase: float, easing: str) -> float:
    """0..1 level of a smooth pulse: 0 at the start of each period, 1 half a period later.

    ``phase`` shifts the pulse by that fraction of a period.  ``easing`` is "cosine"
    (0.5 - 0.5 cos, CSS ease-in-out swinging back and forth) or "quadratic" (a triangle wave
    0 -> 1 -> 0 passed through the quadratic ease-in-out 2x^2 / 1 - 2(1-x)^2).
    """
    x = (t_seconds * hz + phase) % 1.0
    if easing == "cosine":
        return 0.5 - 0.5 * math.cos(2 * math.pi * x)
    if easing != "quadratic":
        raise ValueError(f"unknown easing {easing!r}")
    tri = 2 * x if x < 0.5 else 2 - 2 * x
    return 2 * tri * tri if tri < 0.5 else 1 - 2 * (1 - tri) ** 2


def eased_pulse_strip_frames(
    hz: float, phase: float, n_frames: int, easing: str = "cosine", peak: RGB = (255, 255, 255),
    size: tuple[int, int] = (VIEWPORT_W, 80), fps: int = FPS,
) -> Iterator[np.ndarray]:
    """Frames of a whole page pulsing smoothly between black and ``peak``, sampled as captures are.

    Frame i is the page at (i + SAMPLE_PHASE) / fps (ADR-029), each channel rounded to whole
    bytes.  The default page is 1024 x 80 px: 20 cell rows, so the clipped 85 x 20-cell window
    holds 1700 cells, 31% of the full 5440-cell window, and the pulse qualifies on area while a
    600-frame (10 s) capture stays quick to analyse.
    """
    width, height = size
    for i in range(n_frames):
        level = eased_pulse_level((i + SAMPLE_PHASE) / fps, hz, phase, easing)
        frame = np.empty((height, width, 3), dtype=np.uint8)
        frame[...] = tuple(round(c * level) for c in peak)
        yield frame


def noisy_square_region_frames(
    region: tuple[int, int, int, int], hz: int, greys: tuple[int, int], n_frames: int, seed: int
) -> Iterator[np.ndarray]:
    """A grey square wave inside ``region`` (x, y, w, h px, cell aligned) on black, with noise.

    Every 4x4 cell of the region gets its own +0 or +1 byte on every frame, drawn from a
    generator seeded with ``seed`` (so the frames are the same on every run).  On the dark grey
    that changes L by far less than the 0.005 reversal tolerance - the kind of wobble lossy
    video leaves on a flat colour.
    """
    x, y, w, h = region
    rng = np.random.default_rng(seed)
    for i in range(n_frames):
        grey = greys[square_wave_state(i, hz)]
        cells = grey + rng.integers(0, 2, size=(h // CELL_PX, w // CELL_PX))
        block = np.repeat(np.repeat(cells, CELL_PX, axis=0), CELL_PX, axis=1).astype(np.uint8)
        frame = np.zeros((VIEWPORT_H, VIEWPORT_W, 3), dtype=np.uint8)
        frame[y : y + h, x : x + w] = block[..., None]
        yield frame


def frames_to_array(frames: Iterable[np.ndarray]) -> np.ndarray:
    """Stack a SHORT sequence of frames into ``uint8 [T, H, W, 3]`` (for the array-input tests)."""
    return np.stack(list(frames), axis=0)
