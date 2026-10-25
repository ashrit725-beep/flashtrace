"""CSS fixes for the culprits of a failing audit, and their verification by re-capture (SPEC 7, M6).

``propose_fixes`` turns measured data into CSS override strings, at most three per culprit, in the
order rate, luminance, red/area. Every number in a fix comes from this audit: the violation's flash
rate, the animation's duration, and the two colours the culprit showed in the frames on disk just
before and at a qualifying flash. The report keeps only an element's first animation, so before
proposing, ``read_element_animations`` reads every animation of each culprit from the page (at the
frame attribution inspected), and the fixes change only the animation that explains the flashing.
``verify_fix`` injects a fix as a <style> element (together with one fix per other member of a
minimal culprit set), re-captures the page with the same settings and the same capture strategy,
re-runs the engine and stores the result as the fix's ``after`` measurement. A fix is "verified" only
when that re-capture PASSes under deterministic capture. Area fixes change the layout, so they are
never applied automatically.

Targets, each with the W3C wording it keeps a margin from (docs/SPEC.md section 3):

* rate: SC 2.3.1 "flashes more than three times in any one second period" -> 2.5 flashes per second;
* luminance: general flash "a pair of opposing changes in relative luminance of 10% or more"
  -> a difference of 0.09;
* red: Note 3 "a value R/(R + G + B) that is greater than or equal to 0.8" -> 0.75 for CSS colours,
  just under 0.8 for a filter;
* area: "25% of any 10 degree visual field" -> 95% of the largest size that does not qualify.

Analysis aid, not a compliance certification.
"""

from __future__ import annotations

import copy
import dataclasses
import json
import math
import re
import shutil
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from capture.frames import png_to_rgb
from capture.inspect import SELF_PAINTING_TAGS, InspectStrategy, InspectTarget
from capture.page_scripts import PAGE_HELPERS_JS
from cli.jobs import FRAMES_DIR_NAME, REPORT_JSON_NAME, AuditSettings
from engine.cells import window_cells_full
from engine.color import redness_ratio, relative_luminance, srgb8_from_linear, srgb8_to_linear
from engine.constants import CELL_PX, GENERAL_MIN_DELTA_L, MAX_AREA_FRACTION, RED_SATURATION_MIN_RATIO
from engine.types import AnalysisResult, Violation

RGB8 = tuple[int, int, int]

# SC 2.3.1: "flashes more than three times in any one second period" (4 fails, 3 passes).  A rate fix
# aims for 2.5 per second: evenly spaced flashes 0.4 s apart put at most 3 in any one-second window.
TARGET_FLASHES_PER_SECOND = 2.5
# General flash: "a pair of opposing changes in relative luminance of 10% or more" (engine constant
# GENERAL_MIN_DELTA_L = 0.10).  A luminance fix aims for 0.09, a margin below it.
TARGET_DELTA_L = 0.09
assert TARGET_DELTA_L < GENERAL_MIN_DELTA_L, "the luminance target must stay below the WCAG threshold"
# Red flash, Note 3: "a value R/(R + G + B) that is greater than or equal to 0.8".  A replacement CSS
# colour aims for 0.75, a margin below 0.8.
TARGET_RED_RATIO = 0.75
# Area: the scale factor keeps the culprit at 95% of the largest flashing size that does not qualify.
AREA_MARGIN = 0.95

# Which culprits get fixes is FlashTrace's own rule (SPEC 7 only caps the fixes per culprit at 3):
# * bbox ranking: a culprit whose box overlaps the flashing cells with an IoU of at least 0.5.  Then at
#   least half of the box's cells flashed and the box holds at least half of the flashing cells, because
#   |box AND flashing| / |box| >= |box AND flashing| / |box OR flashing|.  So a fix on it reaches at least
#   half of what flashed.
MIN_OVERLAP_FOR_FIX = 0.5
# * bbox ranking: fixes for at most 3 culprits (so at most 9 fixes), a design choice that keeps the list
#   short enough to read and to verify one fix at a time.  A minimal culprit set found by ablation has no
#   such cap: every member is needed for a PASS (capture/attribute.py), so every member gets fixes.
MAX_BBOX_CULPRITS_TO_FIX = 3
MAX_FIXES_PER_CULPRIT = 3      # SPEC 7: "Fixes (<= 3 per culprit, ...)"
# A culprit's two colours are measured on its first 12 event frames (event frames where this element
# did not change are skipped among them).  A design choice: at most 24 PNG reads per culprit.
MAX_STATE_PAIRS = 12
SEARCH_STEPS = 40              # bisection halvings: far finer than one step of a 0-255 colour value
VERIFY_DIR_NAME = "verify"     # <job>/verify/fix-<n>/frames holds each verification capture
JOB_SCRIPT_NAME = "script.js"  # copy of the interaction script that cli.audit keeps in the job
FIX_INSPECT_DIR_NAME = "fix-inspect"  # scratch folder of the page read that lists every culprit's animations

# Keyframe properties that change what an element paints, besides any value that holds a colour.
PAINT_PROPERTIES = frozenset({"opacity", "visibility", "display", "filter"})
# Properties that let the page behind the element show through in one of its states.
SEE_THROUGH_PROPERTIES = frozenset({"opacity", "visibility", "display"})

AREA_NOT_APPLIED = ("Not applied automatically: an area fix changes the page layout, so FlashTrace "
                    "never verifies it by re-capture.")
KEYFRAME_META_KEYS = frozenset({"offset", "computedOffset", "easing", "composite"})
COLOUR_PATTERN = re.compile(
    r"rgba?\(\s*(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*(\d{1,3})\s*(?:,\s*([0-9.]+)\s*)?\)"
    r"|#([0-9a-fA-F]{6}|[0-9a-fA-F]{3})\b"
)
SAFE_PROPERTY = re.compile(r"-{0,2}[a-z][a-z0-9-]*")
UNSAFE_CSS_TOKENS = ("{", "}", ";", "/*", "*/", "<", "\n")


# --------------------------------------------------------------------------------------- colours
def luminance8(rgb8: RGB8) -> float:
    """Relative luminance of one colour given as whole 0-255 sRGB values (the engine's WCAG formula)."""
    return float(relative_luminance(srgb8_to_linear(np.array(rgb8, dtype=np.uint8))))


def redness8(rgb8: RGB8) -> float:
    """R/(R+G+B) of one colour on whole 0-255 values, exactly as the engine measures a cell."""
    return float(redness_ratio(srgb8_to_linear(np.array(rgb8, dtype=np.uint8))))


def linear_to_rgb8(linear: np.ndarray) -> RGB8:
    """Linear-light RGB -> whole 0-255 sRGB values (rounded the way the engine rounds)."""
    r, g, b = srgb8_from_linear(np.asarray(linear, dtype=np.float64))
    return int(r), int(g), int(b)


def encoded_to_rgb8(encoded: np.ndarray) -> RGB8:
    """sRGB values on 0..1 (a filter's output) -> whole 0-255 values, as a screenshot stores them."""
    r, g, b = np.floor(np.clip(np.asarray(encoded, dtype=np.float64), 0.0, 1.0) * 255.0 + 0.5)
    return int(r), int(g), int(b)


def hex8(rgb8: RGB8) -> str:
    """(255, 0, 0) -> '#ff0000'."""
    return "#{:02x}{:02x}{:02x}".format(*rgb8)


def number_text(value: float) -> str:
    """A CSS number with at most three decimals and no trailing zeros (0.250 -> '0.25')."""
    return f"{value:.3f}".rstrip("0").rstrip(".")


@dataclass(frozen=True)
class ColourState:
    """One visual state of a culprit: its colour and the two numbers WCAG's thresholds look at."""

    rgb8: RGB8
    luminance: float
    redness: float

    @classmethod
    def of(cls, rgb8: RGB8) -> "ColourState":
        return cls(rgb8, luminance8(rgb8), redness8(rgb8))

    def describe(self) -> str:
        r, g, b = self.rgb8
        return f"rgb({r}, {g}, {b}) (L {self.luminance:.3f}, R/(R+G+B) {self.redness:.3f})"


@dataclass(frozen=True)
class MeasuredStates:
    """The culprit's colour on the frame before a qualifying flash completes and on that frame."""

    before: ColourState
    after: ColourState
    frame: int  # the frame where the flash completes; ``before`` is frame - 1

    @property
    def delta_l(self) -> float:
        return abs(self.after.luminance - self.before.luminance)

    def describe(self) -> str:
        return (f"Measured inside the element's box (per-pixel median): frame {self.frame - 1} "
                f"{self.before.describe()}, frame {self.frame} {self.after.describe()}.")


# --------------------------------------------------------------------------------------- measuring
def read_frame(job_dir: Path, index: int) -> np.ndarray | None:
    """Frame ``index`` of the job as uint8 [H, W, 3], or None when its PNG is missing."""
    path = Path(job_dir) / FRAMES_DIR_NAME / f"{index:06d}.png"
    return png_to_rgb(path.read_bytes()) if path.is_file() else None


def median_colour(frame: np.ndarray, rect: Mapping[str, float]) -> RGB8 | None:
    """Per-channel median of the pixels inside ``rect`` (CSS px = image px at scale 1); None if empty.

    The median, not the mean: a thin border or a label inside the box does not move it.
    """
    height, width = frame.shape[:2]
    x0, y0 = max(0, math.floor(rect["x"])), max(0, math.floor(rect["y"]))
    x1, y1 = min(width, math.ceil(rect["x"] + rect["w"])), min(height, math.ceil(rect["y"] + rect["h"]))
    if x1 <= x0 or y1 <= y0:
        return None
    median = np.median(frame[y0:y1, x0:x1].reshape(-1, 3), axis=0)
    r, g, b = np.floor(median + 0.5)
    return int(r), int(g), int(b)


def colour_distance(a: RGB8, b: RGB8) -> float:
    """Straight-line distance between two colours in linear light (0 when they are equal)."""
    diff = srgb8_to_linear(np.array(a, dtype=np.uint8)) - srgb8_to_linear(np.array(b, dtype=np.uint8))
    return float(np.sqrt(np.sum(diff * diff)))


def measure_states(job_dir: Path, rect: Mapping[str, float], event_frames: Sequence[int]) -> MeasuredStates | None:
    """The culprit's two states around a qualifying flash, read from the frames on disk.

    For each of the first event frames f, the median colour inside the box on frame f-1 and on frame
    f; the pair that differs most is kept (an event frame where this element did not change is
    skipped that way).  None when no frame pair could be read.
    """
    best: tuple[float, RGB8, RGB8, int] | None = None
    for frame_index in list(event_frames)[:MAX_STATE_PAIRS]:
        earlier = read_frame(job_dir, frame_index - 1) if frame_index >= 1 else None
        later = read_frame(job_dir, frame_index)
        if earlier is None or later is None:
            continue
        a, b = median_colour(earlier, rect), median_colour(later, rect)
        if a is None or b is None:
            continue
        distance = colour_distance(a, b)
        if best is None or distance > best[0]:
            best = (distance, a, b, frame_index)
    if best is None:
        return None
    return MeasuredStates(ColourState.of(best[1]), ColourState.of(best[2]), best[3])


def cells_in_rect(mask: np.ndarray, rect: Mapping[str, float], cell_px: int = CELL_PX) -> int:
    """How many True cells of ``mask`` [cells_y, cells_x] lie under ``rect`` (CSS px)."""
    rows, cols = mask.shape
    x0, y0 = max(0, math.floor(rect["x"] / cell_px)), max(0, math.floor(rect["y"] / cell_px))
    x1 = min(cols, math.ceil((rect["x"] + rect["w"]) / cell_px))
    y1 = min(rows, math.ceil((rect["y"] + rect["h"]) / cell_px))
    if x1 <= x0 or y1 <= y0:
        return 0
    return int(mask[y0:y1, x0:x1].sum())


# --------------------------------------------------------------------------------------- fix maths
def rate_fix_duration(rate_per_sec: float, duration_ms: float) -> int:
    """New iteration length in ms: the old one stretched by the factor rate / 2.5, rounded up.

    ``rate_per_sec`` is the engine's count for the busiest one-second window (a whole number that can
    include a flash right on the window's edge), not an average.  For evenly spaced flashes that count
    is at least 1 / spacing, so stretching by count / 2.5 leaves them at least 0.4 s apart: at most 3
    in any one second.  Rounded to 6 decimals before ceil so float noise (400.00000000000006) cannot
    add a millisecond.
    """
    scale_factor = rate_per_sec / TARGET_FLASHES_PER_SECOND
    return math.ceil(round(duration_ms * scale_factor, 6))


def scale_to_luminance(rgb8: RGB8, max_luminance: float) -> RGB8:
    """The brightest version of ``rgb8`` whose relative luminance is at most ``max_luminance``.

    The colour's linear-light RGB is multiplied by one factor k, which keeps its chromaticity (hue
    and saturation) and scales its luminance by k.  k is found by bisection on the whole-number
    colour the browser will actually show, so the stored hex value meets the limit.
    """
    if luminance8(rgb8) <= max_luminance:
        return rgb8
    linear = srgb8_to_linear(np.array(rgb8, dtype=np.uint8))
    low, high = 0.0, 1.0  # k = 0 (black, L = 0) always fits; k = 1 (the colour itself) does not
    for _ in range(SEARCH_STEPS):
        middle = (low + high) / 2.0
        if luminance8(linear_to_rgb8(linear * middle)) <= max_luminance:
            low = middle
        else:
            high = middle
    return linear_to_rgb8(linear * low)


def lowered_colours(colours: Sequence[RGB8]) -> dict[RGB8, RGB8]:
    """Map every colour brighter than (darkest + 0.09) to its lowered, same-hue version.

    Afterwards no two of the colours differ by more than 0.09 in relative luminance.
    """
    darkest = min(luminance8(colour) for colour in colours)
    ceiling = darkest + TARGET_DELTA_L
    return {c: scale_to_luminance(c, ceiling) for c in colours if luminance8(c) > ceiling}


def contrast_srgb(encoded: np.ndarray, amount: float) -> np.ndarray:
    """CSS ``contrast(amount)`` on sRGB values 0..1, clamped to 0..1.

    Filter Effects Module Level 1 defines contrast() as feComponentTransfer with type "linear",
    slope = amount and intercept = -(0.5 * amount) + 0.5 on each of R, G and B.
    """
    values = np.asarray(encoded, dtype=np.float64)
    return np.clip(values * amount + (-(0.5 * amount) + 0.5), 0.0, 1.0)


def saturate_matrix(amount: float) -> np.ndarray:
    """The 3x3 feColorMatrix type="saturate" matrix of Filter Effects Module Level 1 for ``amount``."""
    s = amount
    return np.array([
        [0.213 + 0.787 * s, 0.715 - 0.715 * s, 0.072 - 0.072 * s],
        [0.213 - 0.213 * s, 0.715 + 0.285 * s, 0.072 - 0.072 * s],
        [0.213 - 0.213 * s, 0.715 - 0.715 * s, 0.072 + 0.928 * s],
    ], dtype=np.float64)


def saturate_srgb(encoded: np.ndarray, amount: float) -> np.ndarray:
    """CSS ``saturate(amount)`` on sRGB values 0..1 (the matrix above), clamped to 0..1."""
    return np.clip(saturate_matrix(amount) @ np.asarray(encoded, dtype=np.float64), 0.0, 1.0)


def _filtered(rgb8: RGB8, kind: str, amount: float) -> RGB8:
    """``rgb8`` after contrast() or saturate() at ``amount``, as whole 0-255 values."""
    encoded = np.array(rgb8, dtype=np.float64) / 255.0
    result = contrast_srgb(encoded, amount) if kind == "contrast" else saturate_srgb(encoded, amount)
    return encoded_to_rgb8(result)


def _largest_amount(fits: Callable[[float], bool]) -> float:
    """Largest filter amount in [0, 1], rounded down to 3 decimals, for which ``fits`` holds.

    ``fits(0)`` must be true (amount 0 turns a colour grey).  Bisection first; then, because colours
    are whole numbers, step down 0.001 at a time until the rounded amount itself fits.
    """
    low, high = 0.0, 1.0
    for _ in range(SEARCH_STEPS):
        middle = (low + high) / 2.0
        if fits(middle):
            low = middle
        else:
            high = middle
    amount = math.floor(low * 1000.0) / 1000.0
    while amount > 0.0 and not fits(amount):
        amount = round(amount - 0.001, 3)
    return amount


def find_contrast(a: RGB8, b: RGB8) -> float | None:
    """contrast() amount at which the two measured states differ by at most 0.09 in L; None if already so."""
    def gap(amount: float) -> float:
        return abs(luminance8(_filtered(a, "contrast", amount)) - luminance8(_filtered(b, "contrast", amount)))

    if gap(1.0) <= TARGET_DELTA_L:
        return None
    return _largest_amount(lambda amount: gap(amount) <= TARGET_DELTA_L)


def squeezed_pair(a: RGB8, b: RGB8, factor: float) -> tuple[RGB8, RGB8]:
    """The two colours pulled toward their middle colour by ``factor`` (1 = unchanged, 0 = both the middle).

    Worked on sRGB values 0..1, the values a browser blends when it applies opacity.
    """
    first, second = np.array(a, dtype=np.float64) / 255.0, np.array(b, dtype=np.float64) / 255.0
    middle, half = (first + second) / 2.0, (second - first) / 2.0
    return encoded_to_rgb8(middle - factor * half), encoded_to_rgb8(middle + factor * half)


def find_opacity_squeeze(a: RGB8, b: RGB8) -> float | None:
    """Largest factor at which ``squeezed_pair`` differs by at most 0.09 in L; None if the pair already does."""
    def gap(factor: float) -> float:
        first, second = squeezed_pair(a, b, factor)
        return abs(luminance8(first) - luminance8(second))

    if gap(1.0) <= TARGET_DELTA_L:
        return None
    return _largest_amount(lambda factor: gap(factor) <= TARGET_DELTA_L)


def find_saturate(red: RGB8) -> float | None:
    """saturate() amount at which ``red`` measures R/(R+G+B) < 0.8; None if it already does."""
    def below(amount: float) -> bool:
        return redness8(_filtered(red, "saturate", amount)) < RED_SATURATION_MIN_RATIO

    if below(1.0):
        return None
    return _largest_amount(below)


def red_replacement(rgb8: RGB8) -> RGB8:
    """A colour with (about) the luminance of ``rgb8`` whose redness ratio is at most 0.75.

    The red is mixed in linear light with the grey of equal luminance, (1 - t) x red + t x (L, L, L).
    Luminance is linear in light, so every mix keeps L; t is the smallest mix (bisection) whose
    whole-number colour measures R/(R+G+B) <= 0.75.  The hue stays; only the saturation drops.
    """
    if redness8(rgb8) <= TARGET_RED_RATIO:
        return rgb8
    linear = srgb8_to_linear(np.array(rgb8, dtype=np.uint8))
    grey = np.full(3, luminance8(rgb8), dtype=np.float64)
    low, high = 0.0, 1.0  # t = 1 (the grey, ratio 1/3) is under the target; t = 0 (the red) is not
    for _ in range(SEARCH_STEPS):
        middle = (low + high) / 2.0
        if redness8(linear_to_rgb8((1.0 - middle) * linear + middle * grey)) <= TARGET_RED_RATIO:
            high = middle
        else:
            low = middle
    return linear_to_rgb8((1.0 - high) * linear + high * grey)


def area_limit_cells(viewport_width: int) -> int:
    """Largest flashing cell count that does not qualify: 25% of the full window, rounded down.

    WCAG: "25% of any 10 degree visual field"; a flash qualifies only above it (1 360 cells at 1024 px).
    """
    wy, wx = window_cells_full(viewport_width, CELL_PX)
    return math.floor(wy * wx * MAX_AREA_FRACTION)


def area_scale_factor(flashing_cells: int, limit_cells: int) -> float | None:
    """sqrt(limit / cells) x 0.95, or None when the culprit's flashing cells already fit the limit.

    transform: scale(f) shrinks both sides, so the area shrinks by f squared: cells x f^2 < limit.
    """
    if flashing_cells <= limit_cells:
        return None
    return math.sqrt(limit_cells / flashing_cells) * AREA_MARGIN


# --------------------------------------------------------------------------------------- keyframes
def _parse_colour(match: re.Match[str]) -> tuple[RGB8, float] | None:
    """(rgb8, alpha) of one COLOUR_PATTERN match, or None for values outside 0-255."""
    if match.group(5) is not None:
        digits = match.group(5)
        digits = "".join(ch * 2 for ch in digits) if len(digits) == 3 else digits
        return (int(digits[0:2], 16), int(digits[2:4], 16), int(digits[4:6], 16)), 1.0
    channels = tuple(int(match.group(i)) for i in (1, 2, 3))
    if max(channels) > 255:
        return None
    alpha = float(match.group(4)) if match.group(4) is not None else 1.0
    return (channels[0], channels[1], channels[2]), alpha


def _properties(keyframe: Mapping[str, Any]) -> list[tuple[str, str]]:
    """The animated (property, value) pairs of one keyframe from getKeyframes(), as strings."""
    return [(key, str(value)) for key, value in keyframe.items() if key not in KEYFRAME_META_KEYS]


def keyframe_colours(keyframes: Sequence[Mapping[str, Any]]) -> list[tuple[RGB8, float]]:
    """Every distinct (colour, alpha) written in the keyframes' values, in order of appearance."""
    found: list[tuple[RGB8, float]] = []
    for keyframe in keyframes:
        for _key, value in _properties(keyframe):
            for match in COLOUR_PATTERN.finditer(value):
                parsed = _parse_colour(match)
                if parsed is not None and parsed not in found:
                    found.append(parsed)
    return found


def replace_colours(value: str, mapping: Mapping[RGB8, RGB8]) -> str:
    """``value`` with every colour found in ``mapping`` replaced (alpha kept); others left as written."""
    def swap(match: re.Match[str]) -> str:
        parsed = _parse_colour(match)
        if parsed is None or parsed[0] not in mapping:
            return match.group(0)
        new = mapping[parsed[0]]
        return hex8(new) if match.group(4) is None else f"rgba({new[0]}, {new[1]}, {new[2]}, {match.group(4)})"

    return COLOUR_PATTERN.sub(swap, value)


def css_property_name(key: str) -> str | None:
    """getKeyframes() name -> CSS property name ('backgroundColor' -> 'background-color'); None if odd."""
    special = {"cssFloat": "float", "cssOffset": "offset"}
    name = key if key.startswith("--") else special.get(key) or re.sub(r"[A-Z]", lambda m: "-" + m.group(0).lower(), key)
    return name if SAFE_PROPERTY.fullmatch(name) else None


def _safe_value(text: str) -> bool:
    """True when ``text`` cannot end a declaration or a rule early (no braces, semicolons, comments)."""
    return not any(token in text for token in UNSAFE_CSS_TOKENS)


def plain_number(text: str) -> float | None:
    """'0.5' -> 0.5; None for anything that is not a plain finite number."""
    try:
        value = float(text)
    except ValueError:
        return None
    return value if math.isfinite(value) else None


def keyframe_opacities(keyframes: Sequence[Mapping[str, Any]]) -> list[float] | None:
    """Every distinct opacity value in the keyframes, in order; None when one is not a plain number."""
    found: list[float] = []
    for keyframe in keyframes:
        for key, value in _properties(keyframe):
            if key != "opacity":
                continue
            number = plain_number(value)
            if number is None:
                return None
            if number not in found:
                found.append(number)
    return found


def keyframes_css(name: str, keyframes: Sequence[Mapping[str, Any]], mapping: Mapping[RGB8, RGB8],
                  opacity: Mapping[float, str] | None = None) -> str | None:
    """A complete ``@keyframes <name> {...}`` rule: the original keyframes with colours replaced.

    ``opacity`` (optional) maps old opacity values to the new text to write.  Each keyframe keeps its
    offset, its other properties and its easing (written as the keyframe's animation-timing-function,
    which is how CSS applies easing per keyframe).  None when a property name or value could not be
    written back safely.
    """
    blocks: list[str] = []
    for keyframe in keyframes:
        offset = keyframe.get("offset")
        offset = keyframe.get("computedOffset", 0.0) if offset is None else offset
        declarations: list[str] = []
        for key, value in _properties(keyframe):
            prop = css_property_name(key)
            if prop is None or not _safe_value(value):
                return None
            number = plain_number(value) if key == "opacity" else None
            if opacity and number is not None and number in opacity:
                value = opacity[number]
            declarations.append(f"{prop}: {replace_colours(value, mapping)}")
        easing = str(keyframe.get("easing") or "")
        if easing and _safe_value(easing):
            declarations.append(f"animation-timing-function: {easing}")
        percent = f"{float(offset) * 100.0:.4f}".rstrip("0").rstrip(".")
        blocks.append(f"{percent}% {{ {'; '.join(declarations)}; }}")
    return f"@keyframes {name} {{ {' '.join(blocks)} }}"


# --------------------------------------------------------------------------------------- the element's animations
def animation_kind(animation: Mapping[str, Any]) -> str:
    """'css' (a CSS animation), 'script' (element.animate()) or 'transition'.

    The page read below says so in "kind".  A culprit record does not: capture/inspect.py names a CSS
    animation by its animation-name, a CSS transition 'transition: <property>', and a Web Animation by
    its id or '(unnamed Web Animation)'.
    """
    kind = animation.get("kind")
    if kind in ("css", "script", "transition"):
        return str(kind)
    name = str(animation.get("name") or "")
    if name.startswith("transition: "):
        return "transition"
    return "script" if not name or name.startswith("(unnamed") else "css"


def positive_duration(animation: Mapping[str, Any] | None) -> float | None:
    """The animation's iteration length in ms when it is a finite positive number, else None."""
    duration = animation.get("duration") if animation is not None else None
    if isinstance(duration, (int, float)) and math.isfinite(duration) and duration > 0:
        return float(duration)
    return None


def animates(animation: Mapping[str, Any] | None, properties: frozenset[str]) -> bool:
    """True when one of the animation's keyframes sets one of ``properties`` (getKeyframes() names)."""
    frames = animation.get("keyframes") if animation is not None else None
    keyframes = [frame for frame in frames or [] if isinstance(frame, Mapping)]
    return any(key in properties for frame in keyframes for key, _ in _properties(frame))


@dataclass(frozen=True)
class ElementAnimations:
    """Every animation on one element (the culprit record keeps only the first one the page listed).

    ``names`` and ``durations`` are the element's computed animation-name and animation-duration lists
    ('glow, strobe' -> ('glow', 'strobe'); '3s, 0.25s' -> ('3s', '0.25s')).  CSS pairs them by position
    and repeats the shorter list.  ``animations`` holds each animation as {name, kind, duration, keyframes}.
    """

    names: tuple[str, ...]
    durations: tuple[str, ...]
    animations: tuple[Mapping[str, Any], ...]

    @classmethod
    def from_page(cls, raw: Mapping[str, Any]) -> "ElementAnimations":
        """From one entry of ELEMENT_ANIMATIONS_JS's result."""
        return cls(tuple(str(name) for name in raw.get("names") or ()),
                   tuple(str(duration) for duration in raw.get("durations") or ()),
                   tuple(item for item in raw.get("animations") or () if isinstance(item, Mapping)))

    @classmethod
    def from_culprit(cls, animation: Mapping[str, Any] | None) -> "ElementAnimations":
        """Only the culprit record's animation, taken to be the element's one and only animation."""
        if animation is None:
            return cls((), (), ())
        described = dict(animation, kind=animation_kind(animation))
        duration = positive_duration(animation)
        if described["kind"] != "css" or duration is None:
            return cls((), (), (described,))
        return cls((str(animation.get("name")),), (f"{number_text(duration)}ms",), (described,))


# The page-side half of read_element_animations.  PAGE_HELPERS_JS (capture/page_scripts.py) supplies
# ftScopes, the walk over the document, shadow roots and same-origin frames that capture and inspection use.
_ELEMENT_ANIMATIONS_BODY = r"""
  const scopes = ftScopes(window, true);

  // The first element the selector matches, in the document, a shadow root or a same-origin frame.
  const find = (selector) => {
    for (const { root } of scopes) {
      let el = null;
      try { el = root.querySelector(selector); } catch (e) { return null; }
      if (el) return el;
    }
    return null;
  };

  // 'css' for a CSS animation, 'transition' for a CSS transition, 'script' for element.animate().
  const kindOf = (animation) => {
    if (typeof CSSAnimation !== 'undefined' && animation instanceof CSSAnimation) return 'css';
    if (typeof CSSTransition !== 'undefined' && animation instanceof CSSTransition) return 'transition';
    return 'script';
  };

  // A keyframe as plain JSON: its offset, its easing and every animated property as a string.
  const keyframeJson = (frame) => {
    const out = { offset: frame.computedOffset ?? frame.offset ?? null, easing: String(frame.easing ?? 'linear') };
    for (const [key, value] of Object.entries(frame)) {
      if (!['offset', 'computedOffset', 'easing', 'composite'].includes(key)) out[key] = String(value);
    }
    return out;
  };

  // Name, kind, iteration length (ms; null when infinite or unknown) and keyframes of one animation.
  const describe = (animation) => {
    const kind = kindOf(animation);
    const name = kind === 'css' ? animation.animationName
      : kind === 'transition' ? 'transition: ' + animation.transitionProperty
      : (animation.id || '(unnamed Web Animation)');
    const effect = animation.effect;
    const timing = effect && effect.getComputedTiming ? effect.getComputedTiming() : {};
    const keyframes = effect && effect.getKeyframes ? effect.getKeyframes().map(keyframeJson) : [];
    return { name, kind, duration: Number.isFinite(timing.duration) ? timing.duration : null, keyframes };
  };

  // Per selector: the computed animation-name and animation-duration lists, and every animation of the
  // element (getAnimations() lists an element's CSS animations in animation-name order).
  const found = {};
  for (const selector of selectors) {
    const el = find(selector);
    if (!el) continue;
    const style = el.ownerDocument.defaultView.getComputedStyle(el);
    found[selector] = {
      names: style.animationName.split(',').map((part) => part.trim()),
      durations: style.animationDuration.split(',').map((part) => part.trim()),
      animations: el.getAnimations().map(describe),
    };
  }
  return found;
"""

ELEMENT_ANIMATIONS_JS = "({ selectors }) => {\n" + PAGE_HELPERS_JS + _ELEMENT_ANIMATIONS_BODY + "}\n"


class AnimationListStrategy(InspectStrategy):
    """capture/inspect.py's frame walk, reading every animation of the given selectors at the target frames.

    InspectStrategy advances the fake clock and seeks every animation frame by frame exactly as a strategy A
    capture does, and calls ``_collect`` at each target frame; only what is collected there differs.
    """

    def __init__(self, targets: list[InspectTarget], selectors: list[str]) -> None:
        super().__init__(targets)
        self.selectors = selectors

    def _collect(self, page: Any, target: InspectTarget, settings: Any) -> Any:  # type: ignore[override]
        return page.evaluate(ELEMENT_ANIMATIONS_JS, {"selectors": self.selectors})


def _read_on_page(url: str, analysis: AnalysisResult, selectors: list[str], settings: AuditSettings,
                  job_dir: Path, browser: Any | None) -> list[Any]:
    """One fresh page load with the capture's own set-up, walked to each violation's first event frame.

    The set-up is the one capture/inspect.py's collect_candidates uses (strategy A forced, the job's copy
    of the interaction script).  Returns what ELEMENT_ANIMATIONS_JS found at those frames, in order.
    """
    from capture.attribute import browser_session, capture_settings_for, inspection_targets
    from capture.capturer import _Attempt, _run_attempt, resolve_url

    page = capture_settings_for(url, settings, job_dir)
    page = dataclasses.replace(page, url=resolve_url(url, page.allow_local_files), forced=True)
    strategy = AnimationListStrategy(inspection_targets(analysis), selectors)
    attempt = _Attempt(name=strategy.name, strategy=strategy, may_fall_through=False)
    scratch = job_dir / FIX_INSPECT_DIR_NAME
    try:
        with browser_session(browser) as live:
            _run_attempt(live, attempt, page, scratch, None)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    return [strategy.collected[position] for position in sorted(strategy.collected)]


def read_element_animations(url: str, analysis: AnalysisResult | None, culprits: Sequence[Mapping[str, Any]],
                            settings: AuditSettings, job_dir: Path,
                            browser: Any | None = None) -> dict[str, ElementAnimations]:
    """Every animation of each culprit that will get fixes, by selector, read from the page.

    Only culprits that have an animation are read.  The page is put in the state attribution inspected
    (each violation's first event frame); for each selector the first such frame where the element had
    animations counts.  {} when there is nothing to read or the page could not be read; the fixes that
    depend on the list then say it is missing.  ``browser`` (optional) is a Chromium to reuse.
    """
    selectors = [str(c["selector"]) for c in culprits_to_fix(culprits) if c.get("animation") is not None]
    if analysis is None or not analysis.violations or not selectors:
        return {}
    try:
        readings = _read_on_page(url, analysis, selectors, settings, Path(job_dir), browser)
    except Exception:  # noqa: BLE001 - a failed read must not cost the audit; the fixes say the list is missing
        return {}
    found: dict[str, ElementAnimations] = {}
    for selector in selectors:
        hits = [r[selector] for r in readings if isinstance(r, Mapping) and isinstance(r.get(selector), Mapping)]
        best = next((hit for hit in hits if hit.get("animations")), hits[0] if hits else None)
        if best is not None:
            found[selector] = ElementAnimations.from_page(best)
    return found


# --------------------------------------------------------------------------------------- which animation flashes
def paint_state(keyframe: Mapping[str, Any]) -> dict[str, str]:
    """The values of one keyframe that decide what the element paints: colours, opacity, visibility,
    display and filter."""
    return {key: value for key, value in _properties(keyframe)
            if key in PAINT_PROPERTIES or COLOUR_PATTERN.search(value)}


def paint_changes(animation: Mapping[str, Any]) -> int:
    """How many steps of one iteration change the element's paint: from each keyframe to the next, and
    from the last back to the first (where the next iteration starts)."""
    states = [paint_state(frame) for frame in animation.get("keyframes") or [] if isinstance(frame, Mapping)]
    if len(states) < 2:
        return 0
    return sum(1 for here, after in zip(states, states[1:] + states[:1]) if here != after)


def own_flash_rate(animation: Mapping[str, Any]) -> float:
    """Flashes per second the animation makes by itself.  A flash is a pair of opposing changes, so it
    makes half its paint changes per iteration, and 1000 / duration iterations per second."""
    duration = positive_duration(animation) or math.inf
    return paint_changes(animation) / 2.0 * 1000.0 / duration


def flashing_animation(animations: Sequence[Mapping[str, Any]], rate: float) -> Mapping[str, Any] | None:
    """The element's animation that best explains a measured flash rate of ``rate`` per second.

    Only animations with a finite iteration length that are not CSS transitions count.  Among those whose
    keyframes change colour, opacity, visibility, display or filter, the one whose own flash rate is
    closest to the measured rate (ratio nearest 1) wins; on a tie, the first in list order.  An element
    whose animations change none of these (they only move or resize it) keeps its first one.
    """
    usable = [a for a in animations if animation_kind(a) != "transition" and positive_duration(a) is not None]
    painting = [(position, a) for position, a in enumerate(usable) if paint_changes(a) > 0]
    if not painting:
        return usable[0] if usable else None
    if rate <= 0:
        return painting[0][1]
    return min(painting, key=lambda item: (abs(math.log(own_flash_rate(item[1]) / rate)), item[0]))[1]


# --------------------------------------------------------------------------------------- context
@dataclass(frozen=True)
class CulpritContext:
    """What every fix builder needs to know about one culprit, measured once."""

    culprit: Mapping[str, Any]
    rank: int
    selector: str
    rate: int                    # the engine's busiest one-second flash count among the culprit's violations
    flash_types: frozenset[str]  # "general" and/or "red"
    flashing_cells: int          # cells of those violations' masks inside the culprit's box
    limit_cells: int             # largest flashing cell count that does not qualify
    states: MeasuredStates | None
    before: Mapping[str, Any]    # this audit's measurement, shared by every fix
    element: ElementAnimations   # every animation on the element
    animation: Mapping[str, Any] | None  # the one that best explains the flashing (flashing_animation)
    list_note: str = ""          # ends fixes that rewrite an animation list when that list was not read
    selection_note: str = ""     # ends the first fix when the overlap rule chose the culprit after ablation

    @property
    def animation_kind(self) -> str:
        """animation_kind() of the chosen animation, or 'none'."""
        return animation_kind(self.animation) if self.animation is not None else "none"

    @property
    def keyframes(self) -> list[Mapping[str, Any]]:
        """The keyframes a new @keyframes rule can copy: only a CSS animation's (animation-name can
        replace those; a Web Animation or a transition ignores animation-name)."""
        frames = self.animation.get("keyframes") if self.animation and self.animation_kind == "css" else None
        return [frame for frame in frames or [] if isinstance(frame, Mapping)]

    @property
    def see_through(self) -> bool:
        """True when an animation of the element changes opacity, visibility or display: in one state the
        page behind the element shows through, and a filter on the element cannot change that."""
        return any(animates(animation, SEE_THROUGH_PROPERTIES) for animation in self.element.animations)

    def name_index(self) -> int | None:
        """Position of the chosen CSS animation in the element's animation-name list; None when it is not
        a CSS animation, or its name is missing or repeated there (no entry can be singled out)."""
        name = str(self.animation.get("name")) if self.animation is not None else ""
        if self.animation_kind != "css" or self.element.names.count(name) != 1:
            return None
        return self.element.names.index(name)

    def choice_text(self) -> str:
        """Which animation a rate or keyframe fix changes, said when the element has more than one."""
        count = len(self.element.animations)
        if count < 2 or self.animation is None:
            return ""
        name, changes = self.animation.get("name"), paint_changes(self.animation)
        if changes == 0:
            return (f" None of the element's {count} animations changes colour or opacity; only the first, {name!r}, "
                    "is changed.")
        return (f" Of the element's {count} animations, {name!r} is the one whose colour or opacity changes ({changes} "
                f"per {positive_duration(self.animation) or 0:g} ms iteration) come closest to the measured rate; "
                "only it is changed.")

    def states_text(self) -> str:
        return f" {self.states.describe()}" if self.states else ""


def safe_selector(selector: str) -> bool:
    """A selector that can be pasted into a stylesheet without closing a rule or opening a comment."""
    return bool(selector.strip()) and not any(token in selector for token in ("{", "}", "/*", "*/", "\n"))


def moves_by_itself(culprit: Mapping[str, Any]) -> bool:
    """True for an element with its own animation, or one that paints changing pixels without one.

    The second group is capture/inspect.py's SELF_PAINTING_TAGS (video, canvas, iframe, cross-origin
    iframe), matched against the culprit's nodeType.  An <img> does not count: the culprit record cannot
    say whether an image is animated, and a still picture under a flashing overlay must not take the fixes.
    """
    return culprit.get("animation") is not None or str(culprit.get("nodeType") or "") in SELF_PAINTING_TAGS


def culprits_to_fix(culprits: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    """Which culprits get fixes, by rank (FlashTrace's own rule; SPEC 7 only caps the fixes per culprit).

    1. When ablation found a minimal culprit set: every member, whatever its overlap.
    2. Otherwise (bbox ranking, or ablation that found no set): the culprits whose overlap is at least
       MIN_OVERLAP_FOR_FIX.  Boxes alone cannot tell an element apart from the static picture or wrapper
       that covers the same cells, so among those the ones that move by themselves are kept when there
       are any, else only the top-ranked one; at most MAX_BBOX_CULPRITS_TO_FIX.
    """
    by_rank = sorted(culprits, key=lambda c: int(c["rank"]))
    members = [c for c in by_rank if c.get("inMinimalSet")]
    if members:
        return members
    eligible = [c for c in by_rank if float(c.get("overlap") or 0.0) >= MIN_OVERLAP_FOR_FIX]
    chosen = [c for c in eligible if moves_by_itself(c)] or eligible[:1]
    return chosen[:MAX_BBOX_CULPRITS_TO_FIX]


def selection_note(culprits: Sequence[Mapping[str, Any]]) -> str:
    """Said by each culprit's first fix when ablation ran but found no minimal set (rule 2 chose instead)."""
    ablated = any(c.get("ablationDelta") is not None for c in culprits)
    if not ablated or any(c.get("inMinimalSet") for c in culprits):
        return ""
    return (" Ablation found no set of culprits whose suppression makes the page pass, so this culprit was "
            f"chosen by its box overlap (at least {MIN_OVERLAP_FOR_FIX:g}), as without ablation.")


def unfixed_note(culprits: Sequence[Mapping[str, Any]]) -> str | None:
    """Why a report that names culprits has no fixes, when none met culprits_to_fix's rule; else None."""
    if not culprits or culprits_to_fix(culprits):
        return None
    best = max(float(c.get("overlap") or 0.0) for c in culprits)
    ablated = any(c.get("ablationDelta") is not None for c in culprits)
    hint = (" Ablation found no minimal set either." if ablated
            else " --attribute ablate looks for the set of elements that is responsible.")
    return ("no culprit qualified for a fix. A culprit gets fixes when it is in ablation's minimal set, or when "
            f"its box overlaps the flashing cells with an IoU of at least {MIN_OVERLAP_FOR_FIX:g}; the best overlap "
            f"here is {best:.2f}. Elements that flash together each cover only part of the flashing area." + hint)


def _violation_mask(analysis: AnalysisResult, index: int) -> np.ndarray:
    """Cells that flashed in violation ``index``; the heatmap's non-zero cells if the engine gave none."""
    masks = getattr(analysis, "violation_cells", None) or []
    if index < len(masks):
        return np.asarray(masks[index], dtype=bool)
    return np.asarray(analysis.heatmap) > 0


def belonging_violations(culprit: Mapping[str, Any], analysis: AnalysisResult) -> list[tuple[Violation, int]]:
    """(violation, flashing cells inside the culprit's box) for every violation the culprit takes part in."""
    rect = culprit["rect"]
    found = []
    for index, violation in enumerate(analysis.violations):
        cells = cells_in_rect(_violation_mask(analysis, index), rect)
        if cells > 0:
            found.append((violation, cells))
    return found


def element_for(culprit: Mapping[str, Any],
                animations: Mapping[str, ElementAnimations] | None) -> tuple[ElementAnimations, str]:
    """(the culprit's animations, note): read from the page when available, else the culprit record's one.

    ``animations`` None means nobody read the page (tests, offline use): no note.  A read that has no
    entry for this culprit, although it has an animation, is said in the fixes that depend on the list.
    """
    reported = culprit.get("animation") if isinstance(culprit.get("animation"), Mapping) else None
    read = animations.get(str(culprit.get("selector"))) if animations is not None else None
    if read is not None and (read.animations or reported is None):
        return read, ""
    note = ""
    if animations is not None and reported is not None:
        note = (" FlashTrace could not read this element's list of animations from the page, so this fix assumes "
                f"{reported.get('name')!r} is its only one.")
    return ElementAnimations.from_culprit(reported), note


def build_context(culprit: Mapping[str, Any], analysis: AnalysisResult, job_dir: Path, limit_cells: int,
                  before: Mapping[str, Any], animations: Mapping[str, ElementAnimations] | None = None,
                  note: str = "") -> CulpritContext | None:
    """Measure one culprit; None when its selector is unusable or it took part in no violation."""
    selector = str(culprit.get("selector") or "")
    members = belonging_violations(culprit, analysis)
    if not safe_selector(selector) or not members:
        return None
    event_frames = sorted({f for violation, _ in members for f in violation.frame_indices})
    rate = max(int(violation.max_rate_per_sec) for violation, _ in members)
    element, list_note = element_for(culprit, animations)
    return CulpritContext(
        culprit=culprit,
        rank=int(culprit["rank"]),
        selector=selector,
        rate=rate,
        flash_types=frozenset(violation.type for violation, _ in members),
        flashing_cells=max(cells for _, cells in members),
        limit_cells=limit_cells,
        states=measure_states(job_dir, culprit["rect"], event_frames),
        before=before,
        element=element,
        animation=flashing_animation(element.animations, rate),
        list_note=list_note,
        selection_note=note,
    )


def _fix(ctx: CulpritContext, kind: str, css: str, rationale: str) -> dict[str, Any]:
    """One fix dict, exactly the shape of report.schema.json's ``fixes`` items."""
    return {"culprit": ctx.rank, "kind": kind, "css": css, "rationale": rationale,
            "before": dict(ctx.before), "after": None, "verified": False}


def css_list(entries: Sequence[str], index: int, value: str, count: int) -> str | None:
    """A comma-separated CSS list of ``count`` entries whose entry ``index`` is ``value``.

    The other entries are ``entries``, repeated the way CSS repeats a shorter list.  None when there is
    nothing to repeat or an entry could not be written back safely.
    """
    if count < 1 or not entries:
        return None
    items = [value if position == index else entries[position % len(entries)] for position in range(count)]
    return ", ".join(items) if all(item and _safe_value(item) for item in items) else None


def keyframe_override(ctx: CulpritContext, new_name: str, block: str | None) -> str | None:
    """The fix CSS: the new @keyframes rule, then the element's animation-name list with only the flashing
    animation's entry renamed, so the element's other animations keep running."""
    index = ctx.name_index()
    names = css_list(ctx.element.names, index, new_name, len(ctx.element.names)) if index is not None else None
    if names is None or block is None:
        return None
    return f"{block} {ctx.selector} {{ animation-name: {names} !important; }}"


# --------------------------------------------------------------------------------------- builders
def rate_rationale(ctx: CulpritContext, duration: float, new_duration: int) -> str:
    """Why the rate fix's duration, in the engine's own terms."""
    name = str(ctx.animation.get("name")) if ctx.animation is not None else ""
    return (
        f"The engine counted up to {ctx.rate} flashes in one second in the {' and '.join(sorted(ctx.flash_types))} "
        "violation (its busiest one-second window, a whole count that can include a flash right on the window's "
        f"edge). Stretching each iteration of {name!r} from {duration:g} ms to {new_duration} ms ({ctx.rate} / "
        f"{TARGET_FLASHES_PER_SECOND:g} = {ctx.rate / TARGET_FLASHES_PER_SECOND:.2f} times longer) slows its flashes "
        "by that factor: evenly spaced flashes end up at least 0.4 s apart, so any one second holds at most 3, and "
        "WCAG 2.3.1 fails only content that \"flashes more than three times in any one second period\"."
    )


def rate_fix(ctx: CulpritContext) -> dict[str, Any] | None:
    """animation-duration of the flashing CSS animation only, stretched to at most 2.5 flashes per second."""
    duration, index = positive_duration(ctx.animation), ctx.name_index()
    if duration is None or index is None or ctx.rate <= 0:
        return None
    new_duration = rate_fix_duration(ctx.rate, duration)
    durations = css_list(ctx.element.durations, index, f"{new_duration}ms", len(ctx.element.names))
    if new_duration <= duration or durations is None:
        return None  # already slow enough (the rate comes from somewhere else), or the list cannot be written
    css = f"{ctx.selector} {{ animation-duration: {durations} !important; }}"
    rationale = rate_rationale(ctx, duration, new_duration) + ctx.choice_text() + ctx.list_note
    return _fix(ctx, "rate", css, rationale + ctx.states_text())


def why_no_rate_fix(ctx: CulpritContext) -> str:
    """Why a culprit got no rate fix (said in its first fix's rationale)."""
    name = repr(str(ctx.animation.get("name"))) if ctx.animation is not None else ""
    if ctx.animation_kind == "script":
        return (f" No rate fix: the animation that best matches the flashing, {name}, was started by script "
                "(element.animate()), and a stylesheet cannot change its timing.")
    if ctx.animation_kind == "css" and ctx.name_index() is None:
        return (f" No rate fix: {name} is not listed exactly once in the element's animation-name, so an "
                "animation-duration list cannot single it out.")
    if ctx.animation_kind == "css":
        return f" No rate fix: a longer animation-duration for {name} could not be written."
    return (" No rate fix: the element has no CSS animation or Web Animation with a known, finite duration "
            "(the flashing comes from script, canvas, video or a CSS transition that script keeps restarting), "
            "so CSS cannot slow it down.")


def keyframe_luminance_fix(ctx: CulpritContext) -> dict[str, Any] | None:
    """New @keyframes whose brighter colours are lowered (same hue) to within 0.09 L of the darkest."""
    colours = keyframe_colours(ctx.keyframes)
    if not colours or any(alpha < 1.0 for _, alpha in colours) or animates(ctx.animation, SEE_THROUGH_PROPERTIES):
        return None  # no colours, or see-through states whose visible result depends on what is behind
    mapping = lowered_colours([rgb for rgb, _ in colours])
    name = f"ft-fix-{ctx.rank}-lum"
    css = keyframe_override(ctx, name, keyframes_css(name, ctx.keyframes, mapping)) if mapping else None
    if css is None:
        return None
    final = [mapping.get(rgb, rgb) for rgb, _ in colours]
    spread = max(luminance8(c) for c in final) - min(luminance8(c) for c in final)
    changes = "; ".join(f"{hex8(old)} (L {luminance8(old):.3f}) -> {hex8(new)} (L {luminance8(new):.3f})"
                        for old, new in mapping.items())
    rationale = (
        f"Lowers the brighter animated colour(s), keeping each hue (scaled in linear light, stored as sRGB hex): "
        f"{changes}. The animated colours then differ by at most {spread:.3f} in relative luminance, under the "
        "0.10 of a general flash (WCAG: \"a pair of opposing changes in relative luminance of 10% or more\")."
    )
    return _fix(ctx, "luminance", css, rationale + ctx.choice_text() + ctx.list_note + ctx.states_text())


def opacity_rationale(ctx: CulpritContext, values: Sequence[float], written: Mapping[float, str]) -> str:
    """Why the opacity fix's values, with the two states it predicts."""
    assert ctx.states is not None
    low, high = sorted(values)
    factor = (float(written[high]) - float(written[low])) / (high - low)
    first, second = squeezed_pair(ctx.states.before.rgb8, ctx.states.after.rgb8, factor)
    gap = abs(luminance8(first) - luminance8(second))
    return (
        f"Keeps the blink but narrows it: opacity {number_text(low)} -> {written[low]} and {number_text(high)} -> "
        f"{written[high]}. Opacity mixes the element with what is behind it (composite = backdrop + opacity x "
        "(element - backdrop), simple alpha compositing, on the sRGB values browsers blend), so narrowing the "
        f"opacity range by a factor of {factor:.3f} pulls both measured states toward their middle colour by the "
        f"same factor: predicted {hex8(first)} (L {luminance8(first):.3f}) and {hex8(second)} "
        f"(L {luminance8(second):.3f}), a difference of {gap:.3f}, under the 0.10 of a general flash (WCAG: \"a pair "
        "of opposing changes in relative luminance of 10% or more\")."
    )


def opacity_luminance_fix(ctx: CulpritContext) -> dict[str, Any] | None:
    """New @keyframes whose two opacity values move toward their middle, so the two states differ by at
    most 0.09 in L.  Only for keyframes that switch opacity between two values and change no colour."""
    values = keyframe_opacities(ctx.keyframes)
    if (ctx.states is None or values is None or len(values) != 2 or keyframe_colours(ctx.keyframes)
            or animates(ctx.animation, frozenset({"visibility", "display"}))):
        return None
    squeeze = find_opacity_squeeze(ctx.states.before.rgb8, ctx.states.after.rgb8)
    if squeeze is None:
        return None
    middle = (values[0] + values[1]) / 2.0
    written = {value: number_text(middle + squeeze * (value - middle)) for value in values}
    name = f"ft-fix-{ctx.rank}-lum"
    css = keyframe_override(ctx, name, keyframes_css(name, ctx.keyframes, {}, written))
    if css is None:
        return None
    rationale = opacity_rationale(ctx, values, written) + ctx.choice_text() + ctx.list_note
    return _fix(ctx, "luminance", css, rationale + ctx.states_text())


def filter_luminance_fix(ctx: CulpritContext) -> dict[str, Any] | None:
    """filter: contrast(c), with c chosen so the two measured states differ by at most 0.09 in L.

    Not for a see-through element (see CulpritContext.see_through): the prediction filters both states,
    but in one of them the page behind the element shows, which the element's filter cannot change.
    """
    if ctx.states is None or ctx.see_through:
        return None
    a, b = ctx.states.before.rgb8, ctx.states.after.rgb8
    amount = find_contrast(a, b)
    if amount is None:
        return None
    new_a, new_b = _filtered(a, "contrast", amount), _filtered(b, "contrast", amount)
    rationale = (
        f"filter: contrast({number_text(amount)}) pulls both states toward mid-grey (Filter Effects contrast(), "
        f"applied to sRGB values): predicted {hex8(new_a)} (L {luminance8(new_a):.3f}) and {hex8(new_b)} "
        f"(L {luminance8(new_b):.3f}), a difference of {abs(luminance8(new_a) - luminance8(new_b)):.3f}, under the "
        "0.10 of a general flash (WCAG: \"a pair of opposing changes in relative luminance of 10% or more\"). The "
        "prediction takes both states to be the element's own paint; if script makes the element see-through in "
        "one of them, a filter cannot change what shows through, and verification will show it."
    )
    css = f"{ctx.selector} {{ filter: contrast({number_text(amount)}) !important; }}"
    return _fix(ctx, "luminance", css, rationale + ctx.states_text())


def luminance_fix(ctx: CulpritContext) -> dict[str, Any] | None:
    """Only for general violations: keyframe colours, else keyframe opacity, else a contrast() filter."""
    if "general" not in ctx.flash_types:
        return None
    return keyframe_luminance_fix(ctx) or opacity_luminance_fix(ctx) or filter_luminance_fix(ctx)


def keyframe_red_fix(ctx: CulpritContext) -> dict[str, Any] | None:
    """New @keyframes whose saturated reds are replaced by same-luminance colours of ratio 0.75."""
    if animates(ctx.animation, SEE_THROUGH_PROPERTIES):
        return None  # see-through states: the visible colour also depends on what is behind
    colours = [rgb for rgb, alpha in keyframe_colours(ctx.keyframes) if alpha >= 1.0]
    mapping = {rgb: red_replacement(rgb) for rgb in colours if redness8(rgb) >= RED_SATURATION_MIN_RATIO}
    name = f"ft-fix-{ctx.rank}-red"
    css = keyframe_override(ctx, name, keyframes_css(name, ctx.keyframes, mapping)) if mapping else None
    if css is None:
        return None
    changes = "; ".join(
        f"{hex8(old)} (R/(R+G+B) {redness8(old):.3f}, L {luminance8(old):.3f}) -> {hex8(new)} "
        f"(R/(R+G+B) {redness8(new):.3f}, L {luminance8(new):.3f})" for old, new in mapping.items())
    rationale = (
        f"Replaces each saturated red with a colour of the same relative luminance and hue but less saturation: "
        f"{changes}. The new ratio is below the 0.8 of WCAG 2.2's red flash note "
        "(\"R/(R + G + B) that is greater than or equal to 0.8\")."
    )
    return _fix(ctx, "red", css, rationale + ctx.choice_text() + ctx.list_note + ctx.states_text())


def filter_red_fix(ctx: CulpritContext) -> dict[str, Any] | None:
    """filter: saturate(s), with s chosen so the measured red state's ratio drops below 0.8 (not for a
    see-through element, for the reason given in filter_luminance_fix)."""
    if ctx.states is None or ctx.see_through:
        return None
    red = max((ctx.states.before, ctx.states.after), key=lambda state: state.redness)
    amount = find_saturate(red.rgb8) if red.redness >= RED_SATURATION_MIN_RATIO else None
    if amount is None:
        return None
    new = _filtered(red.rgb8, "saturate", amount)
    rationale = (
        f"filter: saturate({number_text(amount)}) (Filter Effects saturate matrix, applied to sRGB values) turns the "
        f"measured red state {red.describe()} into {hex8(new)} (R/(R+G+B) {redness8(new):.3f}), below the 0.8 of "
        "WCAG 2.2's red flash note (\"R/(R + G + B) that is greater than or equal to 0.8\")."
    )
    css = f"{ctx.selector} {{ filter: saturate({number_text(amount)}) !important; }}"
    return _fix(ctx, "red", css, rationale + ctx.states_text())


def red_fix(ctx: CulpritContext) -> dict[str, Any] | None:
    """Only for culprits in a red violation: CSS keyframe colours first, otherwise a saturate() filter."""
    if "red" not in ctx.flash_types:
        return None
    return keyframe_red_fix(ctx) or filter_red_fix(ctx)


def area_fix(ctx: CulpritContext) -> dict[str, Any] | None:
    """A transform: scale(f) suggestion that shrinks the flashing area below 25% of the window."""
    factor = area_scale_factor(ctx.flashing_cells, ctx.limit_cells)
    if factor is None:
        return None
    shown = math.floor(factor * 1000.0) / 1000.0
    css = (f"/* not auto-applied: changes layout */ {ctx.selector} "
           f"{{ transform: scale({number_text(shown)}); transform-origin: top left; }}")
    rationale = (
        f"{ctx.flashing_cells} of the element's {CELL_PX}x{CELL_PX} px cells flashed; a flash qualifies only above "
        f"{ctx.limit_cells} cells (WCAG: \"25% of any 10 degree visual field\"). Scaling the element by "
        f"{number_text(shown)} (sqrt({ctx.limit_cells} / {ctx.flashing_cells}) x {AREA_MARGIN:g}) brings it below "
        f"that. {AREA_NOT_APPLIED}"
    )
    return _fix(ctx, "area", css, rationale + ctx.states_text())


def first_fix_notes(ctx: CulpritContext, fixes: Sequence[Mapping[str, Any]], rate_made: bool) -> str:
    """Sentences that end a culprit's first fix: how it was chosen, and which kinds of fix are missing."""
    notes = ctx.selection_note + ("" if rate_made else why_no_rate_fix(ctx))
    if ctx.see_through and not any(fix["kind"] in ("luminance", "red") for fix in fixes):
        notes += (" No filter fix: an animation of the element changes opacity, visibility or display, so in one "
                  "state the page behind it shows through, and a filter on the element cannot change that.")
    return notes


def fixes_for_culprit(ctx: CulpritContext) -> list[dict[str, Any]]:
    """Up to three fixes in the order rate, luminance, red, area (later kinds drop out first)."""
    rate = rate_fix(ctx)
    fixes = [fix for fix in (rate, luminance_fix(ctx), red_fix(ctx), area_fix(ctx)) if fix is not None]
    fixes = fixes[:MAX_FIXES_PER_CULPRIT]
    if fixes:
        fixes[0]["rationale"] += first_fix_notes(ctx, fixes, rate is not None)
    return fixes


# --------------------------------------------------------------------------------------- proposing
def measurement(analysis: AnalysisResult | None, capture: Mapping[str, Any], fps: int,
                capture_error: str | None = None) -> dict[str, Any]:
    """The schema's ``measurement`` object: verdict after SPEC 6.4 escalation, rates, counts, determinism."""
    from cli.report import decide_verdict  # cli.report does not import capture, so this is no cycle

    verdict, _reason = decide_verdict(analysis, capture, fps, capture_error)
    return {
        "verdict": verdict,
        "generalMaxRatePerSec": int(analysis.general_max_rate) if analysis is not None else 0,
        "redMaxRatePerSec": int(analysis.red_max_rate) if analysis is not None else 0,
        "qualifyingGeneral": len(analysis.qualifying_general) if analysis is not None else 0,
        "qualifyingRed": len(analysis.qualifying_red) if analysis is not None else 0,
        "deterministic": bool(capture.get("deterministic")) and capture_error is None,
    }


def _capture_from_disk(job_dir: Path, analysis: AnalysisResult) -> dict[str, Any]:
    """The job's report ``capture`` block when report.json exists, else a cautious stand-in."""
    path = Path(job_dir) / REPORT_JSON_NAME
    if path.is_file():
        return dict(json.loads(path.read_text(encoding="utf-8"))["capture"])
    return {"strategy": "none", "deterministic": False, "frameCount": analysis.frame_count}


def propose_fixes(culprits: Sequence[Mapping[str, Any]], analysis: AnalysisResult | None,
                  settings: AuditSettings, job_dir: Path, capture: Mapping[str, Any] | None = None, *,
                  animations: Mapping[str, ElementAnimations] | None = None) -> list[dict[str, Any]]:
    """Fix dicts for the culprits that deserve them (see ``culprits_to_fix``), at most 3 per culprit.

    ``capture`` is the report's capture block (for the ``before`` measurement's verdict and
    determinism); when omitted it is read from the job's report.json.  ``animations`` is what
    ``read_element_animations`` read from the page: every animation of each culprit, by selector.
    Without it (tests, offline use) a culprit's one recorded animation is taken to be its only one.
    """
    if analysis is None or not analysis.violations:
        return []
    capture_info = capture if capture is not None else _capture_from_disk(Path(job_dir), analysis)
    before = measurement(analysis, capture_info, settings.fps)
    limit = area_limit_cells(settings.viewport[0])
    note = selection_note(culprits)
    fixes: list[dict[str, Any]] = []
    for culprit in culprits_to_fix(culprits):
        ctx = build_context(culprit, analysis, Path(job_dir), limit, before, animations, note)
        if ctx is not None:
            fixes.extend(fixes_for_culprit(ctx))
    return fixes


# --------------------------------------------------------------------------------------- verifying
def with_note(text: str, note: str) -> str:
    """``text`` followed by ``note`` (once: a note already present is not repeated)."""
    return text if note in text else f"{text} {note}".strip()


def job_script(job_dir: Path, settings: AuditSettings) -> str | None:
    """The interaction script the audit ran: the job's saved copy, else the file named in the settings."""
    saved = Path(job_dir) / JOB_SCRIPT_NAME
    if saved.is_file():
        return saved.read_text(encoding="utf-8")
    if settings.script is None:
        return None
    return Path(settings.script).read_text(encoding="utf-8")


def recapture(job_dir: Path, n: int, url: str, settings: AuditSettings, strategy: str, css: str,
              progress: Callable[[int, int], None] | None = None,
              browser: Any | None = None) -> tuple[dict[str, Any], str | None]:
    """Capture the page again with ``css`` injected, analyse it; (measurement, error text or None)."""
    from capture import capture
    from cli.report import capture_block
    from engine import analyze

    out_dir = Path(job_dir) / VERIFY_DIR_NAME / f"fix-{n}" / FRAMES_DIR_NAME
    try:
        result = capture(url, duration_s=settings.duration_s, viewport=settings.viewport, fps=settings.fps,
                         interaction_script=job_script(job_dir, settings), strategy=strategy, out_dir=out_dir,
                         extra_css=css, progress=progress, browser=browser)
    except Exception as exc:  # noqa: BLE001 - a failed re-capture is an INCONCLUSIVE "after", not a crash
        text = str(exc).strip()
        error = f"{type(exc).__name__}: {text.splitlines()[0]}" if text else type(exc).__name__
        return measurement(None, capture_block(None, error), settings.fps, error), error
    analysis = None
    if len(result.frames) >= 2:
        analysis = analyze(result.frames, fps=settings.fps, viewport=settings.viewport, red_rule=settings.red_rule)
    return measurement(analysis, capture_block(result.meta), settings.fps), None


def companion_fixes(report: Mapping[str, Any], n: int) -> list[int]:
    """Indexes of the fixes injected together with ``fixes[n]``: one per other minimal-set member.

    A minimal culprit set is minimal because every member is needed: with any one member still flashing,
    the page fails (capture/attribute.py).  So a fix for one member can only be judged together with a
    fix for each other member: that member's fix of the same kind, else its first fix that can be
    applied (area fixes never are).  Empty when fixes[n]'s culprit is not in a set of two or more.
    """
    fixes = report["fixes"]
    own = int(fixes[n]["culprit"])
    members = sorted(int(c["rank"]) for c in report.get("culprits") or [] if c.get("inMinimalSet"))
    if own not in members or len(members) < 2:
        return []
    chosen: list[int] = []
    for rank in members:
        usable = [i for i, fix in enumerate(fixes) if int(fix["culprit"]) == rank and fix["kind"] != "area"]
        same_kind = [i for i in usable if fixes[i]["kind"] == fixes[n]["kind"]]
        if rank != own and usable:
            chosen.append((same_kind or usable)[0])
    return chosen


def companions_note(report: Mapping[str, Any], n: int, companions: Sequence[int]) -> str:
    """What verify_fix says about the other minimal-set members ('' when fixes[n] was injected alone)."""
    fixes = report["fixes"]
    own = int(fixes[n]["culprit"])
    members = sorted(int(c["rank"]) for c in report.get("culprits") or [] if c.get("inMinimalSet"))
    if own not in members or len(members) < 2:
        return ""
    covered = {int(fixes[i]["culprit"]) for i in companions}
    missing = [str(rank) for rank in members if rank != own and rank not in covered]
    text = (f"The minimal culprit set has {len(members)} members, and with any one of them still flashing the page "
            "fails, so this fix is verified together with one fix per other member")
    listed = ", ".join(f"culprit {fixes[i]['culprit']}'s {fixes[i]['kind']} fix (fix {i + 1})" for i in companions)
    text += f": {listed}." if listed else "."
    if missing:
        text += f" Culprit(s) {', '.join(missing)} have no fix that can be applied, so the page may still fail."
    return text


def verify_fix(report: Mapping[str, Any], n: int, job_dir: Path, url: str, settings: AuditSettings, *,
               progress: Callable[[int, int], None] | None = None, browser: Any | None = None) -> dict[str, Any]:
    """The report with ``fixes[n].after`` and ``fixes[n].verified`` set (``n`` counts from 0).

    The page is re-captured with the fix's CSS injected (plus one fix per other member of a minimal
    culprit set, see ``companion_fixes``), using the job's settings and the same strategy as the
    original capture (forced, so no fall-through can change it).  Verified only when the re-capture's
    verdict is PASS and the capture was deterministic.  Area fixes are never applied.
    """
    updated = copy.deepcopy(dict(report))
    fixes = updated["fixes"]
    if not 0 <= n < len(fixes):
        raise IndexError(f"fix {n} does not exist: the report has {len(fixes)} fix(es), numbered from 0")
    fix = fixes[n]
    if fix["kind"] == "area":
        fix["after"], fix["verified"] = None, False
        fix["rationale"] = with_note(fix["rationale"], AREA_NOT_APPLIED)
        return updated
    strategy = str(updated["capture"]["strategy"])
    if strategy == "none":
        raise ValueError("this job's capture failed, so there is no capture strategy to repeat")
    companions = companion_fixes(updated, n)
    css = "\n".join(fixes[i]["css"] for i in [n, *companions])
    after, error = recapture(Path(job_dir), n, url, settings, strategy, css, progress, browser)
    fix["after"] = after
    fix["verified"] = after["verdict"] == "PASS" and bool(after["deterministic"])
    fix["rationale"] = with_note(fix["rationale"], companions_note(updated, n, companions))
    if error is not None:
        fix["rationale"] = with_note(fix["rationale"], f"Verification capture failed: {error}.")
    return updated
