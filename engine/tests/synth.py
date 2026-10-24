"""Synthetic frame sequences for the SPEC section 5.10 acceptance tests.

Every acceptance test needs a short "video" of a 1024x768 page in which some rectangle
switches colour at a known rate.  This module builds those videos lazily, one
``uint8 [768, 1024, 3]`` frame at a time, so a test never holds a whole sequence in memory
(90 frames of 1024x768 RGB would already be 212 MB).

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

# The engine averages 4x4 px cells (SPEC 5.2).  Regions are aligned to this grid so that no
# cell is half covered: a half-covered cell would average two colours and produce an unplanned
# third luminance along the region's edge.
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


@dataclass(frozen=True)
class Region:
    """A rectangle of the page with its own colour schedule.  Units are CSS px."""

    x: int
    y: int
    w: int
    h: int
    colour_at: ColourAt

    def __post_init__(self) -> None:
        for name in ("x", "y", "w", "h"):
            if getattr(self, name) % CELL_PX != 0:
                raise ValueError(f"Region.{name} must be a multiple of {CELL_PX} px")
        if self.w <= 0 or self.h <= 0:
            raise ValueError("Region must have a positive width and height")
        if self.x + self.w > VIEWPORT_W or self.y + self.h > VIEWPORT_H:
            raise ValueError("Region does not fit inside the 1024x768 viewport")

    @property
    def cells(self) -> int:
        """How many 4x4 px cells the region covers (exact, because it is grid aligned)."""
        return (self.w // CELL_PX) * (self.h // CELL_PX)


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
) -> Iterator[np.ndarray]:
    """Yield the scene's frames one at a time as ``uint8 [768, 1024, 3]`` arrays.

    Frame ``i`` is rendered at ``t = i / fps`` (SPEC 5.1).  Regions are painted in list order,
    so a later region covers an earlier one where they overlap.  Each frame is a fresh array:
    the engine may keep a reference to the previous frame and must not see it change.
    """
    base = np.empty((VIEWPORT_H, VIEWPORT_W, 3), dtype=np.uint8)
    base[...] = background
    for i in range(frame_count(duration_s, fps)):
        t_seconds = i / fps
        frame = base.copy()
        for region in regions:
            frame[region.y:region.y + region.h, region.x:region.x + region.w] = region.colour_at(t_seconds)
        yield frame


def frames_to_array(frames: Iterable[np.ndarray]) -> np.ndarray:
    """Stack a SHORT sequence of frames into ``uint8 [T, H, W, 3]`` (for the array-input tests)."""
    return np.stack(list(frames), axis=0)
