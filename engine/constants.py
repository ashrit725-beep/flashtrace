"""Every number the FlashTrace engine uses, each beside the W3C sentence it comes from.

No other module under ``engine/`` may compare against a literal number: every
threshold comparison imports its constant from here, so the whole rule set can
be audited on one page.  Quotations are verbatim from WCAG 2.2 (W3C
Recommendation 12 December 2024) and its Understanding document, as reproduced
in docs/SPEC.md section 3.  Boundaries (inclusive / exclusive) follow the W3C
wording and are stated next to each constant.
"""

from __future__ import annotations

# --------------------------------------------------------------------------- #
# Cells                                                                        #
# --------------------------------------------------------------------------- #

# The frame is averaged into square cells this many CSS pixels on a side before
# any threshold is applied (build brief; docs/SPEC.md 5.2, ADR-002).  Four is
# small enough that a 341x256 px window still holds 5 440 cells, so the 25 %
# area rule is measured finely, and large enough that a 1024x768 frame becomes
# only 49 152 cells, a sixteenth of its pixels, for every per-frame operation.
CELL_PX: int = 4

# --------------------------------------------------------------------------- #
# Relative luminance (WCAG 2.2 glossary "relative luminance", Note 1)          #
# --------------------------------------------------------------------------- #

# WCAG 2.2 glossary: "L = 0.2126 * R + 0.7152 * G + 0.0722 * B"
LUMINANCE_COEFFICIENTS: tuple[float, float, float] = (0.2126, 0.7152, 0.0722)

# WCAG 2.2 glossary: "if RsRGB <= 0.04045 then R = RsRGB/12.92
#                     else R = ((RsRGB+0.055)/1.055) ^ 2.4"
# (Note 2 of the same entry: 0.04045 replaced the older 0.03928 in May 2021.)
SRGB_LINEAR_CUTOFF: float = 0.04045
SRGB_LINEAR_DIVISOR: float = 12.92
SRGB_GAMMA_OFFSET: float = 0.055
SRGB_GAMMA_SCALE: float = 1.055
SRGB_GAMMA: float = 2.4

# The reverse direction (linear light -> encoded sRGB) switches branch at this
# linear value (it is 0.04045 / 12.92).  WCAG prints only the forward formula,
# which it says is "taken from [SRGB]" (IEC 61966-2-1); FlashTrace needs the
# reverse to re-encode a cell's mean linear colour before computing the redness
# ratio on encoded components (ADR-003).
SRGB_ENCODE_CUTOFF: float = 0.0031308

# --------------------------------------------------------------------------- #
# Chromaticity (Understanding SC 2.3.1, note added in WCAG 2.2)                #
# --------------------------------------------------------------------------- #

# Understanding 2.3.1 prints the seven-decimal matrix
#   "X = 0.4124564 * R + 0.3575761 * G + 0.1804375 * B"
#   "Y = 0.2126729 * R + 0.7151522 * G + 0.0721750 * B"
#   "Z = 0.0193339 * R + 0.1191920 * G + 0.9503041 * B".
# FlashTrace uses the four-decimal IEC 61966-2-1 / D65 form of the same matrix
# so that its Y row is bit-identical to LUMINANCE_COEFFICIENTS above (one
# luminance everywhere, docs/SPEC.md 5.3).  The largest per-coefficient
# difference is 1.959e-4 (Z row, blue: 0.9505 against 0.9503041); every other
# coefficient differs by less than 1e-4.  Both are three orders of magnitude
# under the 0.2 chromaticity threshold.
SRGB_TO_XYZ: tuple[tuple[float, float, float], ...] = (
    (0.4124, 0.3576, 0.1805),
    (0.2126, 0.7152, 0.0722),
    (0.0193, 0.1192, 0.9505),
)


def _white_point_uv(matrix: tuple[tuple[float, float, float], ...]) -> tuple[float, float]:
    """CIE 1976 (u', v') of linear white (R = G = B = 1) under ``matrix``.

    The additions run in the same order as engine/color.py, so the white of a
    frame and this constant are the very same floating-point numbers.
    """
    # Each tristimulus value is its matrix row applied to (1, 1, 1).
    x, y, z = ((row[0] * 1.0 + row[1] * 1.0) + row[2] * 1.0 for row in matrix)
    # Understanding 2.3.1: "u' = 4 * X / (X + 15 * Y + 3 * Z)"
    #                      "v' = 9 * Y / (X + 15 * Y + 3 * Z)"
    denominator = (x + 15.0 * y) + 3.0 * z
    return 4.0 * x / denominator, 9.0 * y / denominator


# CIE 1976 (u', v') of the D65 white point, about (0.1978, 0.4683), computed
# here from SRGB_TO_XYZ rather than typed in.  Pure black has X = Y = Z = 0, so
# its chromaticity is 0/0; FlashTrace gives black the white point so that it
# counts as "no colour", exactly like white and grey (ADR-011).
D65_UV: tuple[float, float] = _white_point_uv(SRGB_TO_XYZ)

# --------------------------------------------------------------------------- #
# General flash (WCAG 2.2 glossary "general flash and red flash thresholds")   #
# --------------------------------------------------------------------------- #

# WCAG 2.2 glossary: "a pair of opposing changes in relative luminance of 10% or
# more of the maximum relative luminance (1.0)"  ->  INCLUSIVE: >= 0.10 counts.
GENERAL_MIN_DELTA_L: float = 0.10

# WCAG 2.2 glossary: "where the relative luminance of the darker image is below
# 0.80"  ->  EXCLUSIVE: the darker of the two states must be < 0.80.
GENERAL_DARK_STATE_MAX_L: float = 0.80

# --------------------------------------------------------------------------- #
# Red flash, WCAG 2.2 working definition (glossary Note 3) - the verdict rule   #
# --------------------------------------------------------------------------- #

# WCAG 2.2 glossary Note 3: "one transition is either to or from a state with a
# value R/(R + G + B) that is greater than or equal to 0.8"  ->  INCLUSIVE.
RED_SATURATION_MIN_RATIO: float = 0.8

# WCAG 2.2 glossary Note 3: "the difference between states is more than 0.2
# (unitless) in the CIE 1976 UCS chromaticity diagram"  ->  EXCLUSIVE: > 0.2.
RED_MIN_DELTA_UV: float = 0.2

# A FlashTrace floor, not a W3C number (ADR-020): a cell whose red component is
# below 16 (on the 0-255 scale) gets redness ratio 0, because a red that dark
# has no visible hue.  Without it rgb(0,0,0) <-> rgb(1,0,0), an invisible
# one-step noise on black, would be a red transition (ratio 1.0, chromaticity
# change 0.259).  16 is not arbitrary: it is the smallest red the pre-2022
# working definition could ever count against black, because a change in
# "(R - G - B) x 320" that is "greater than 20" needs R - G - B > 20/320 of
# full scale, i.e. more than 15.94 on the 0-255 scale.  INCLUSIVE: R = 16 counts.
RED_MIN_R8: int = 16

# --------------------------------------------------------------------------- #
# Red flash, pre-2022 working definition (comparison mode only, ADR-001)       #
# --------------------------------------------------------------------------- #

# The build brief's working value, which is the WCAG 2.0 / 2.1 era definition
# still implemented by PEAT and EA IRIS: red value "(R - G - B) x 320", and a
# red transition is a change in that value "greater than 20" (EXCLUSIVE) while
# one state is saturated red.  Never used for a verdict unless the caller asks
# for red_rule="wcag21-legacy".
LEGACY_RED_VALUE_SCALE: int = 320
LEGACY_RED_MIN_DELTA: int = 20

# --------------------------------------------------------------------------- #
# Area (WCAG 2.2 glossary Note 1 and Understanding 2.3.1)                      #
# --------------------------------------------------------------------------- #

# WCAG 2.2 glossary Note 1: "a 341 x 256 pixel rectangle anywhere on the
# displayed screen area when the content is viewed at 1024 x 768 pixels will
# provide a good estimate of a 10 degree visual field".  Understanding 2.3.1:
# "the combined area of flashing becomes 341 x 256 CSS pixels".
REFERENCE_VIEWPORT: tuple[int, int] = (1024, 768)
WINDOW_PX: tuple[int, int] = (341, 256)

# WCAG 2.2 glossary: content passes if the flashing area "occupies no more than
# a total of .006 steradians within any 10 degree visual field on the screen
# (25% of any 10 degree visual field on the screen)"  ->  "no more than" means
# exactly 25 % still passes, so a flash qualifies only when its share of the
# window is STRICTLY GREATER than this fraction.
MAX_AREA_FRACTION: float = 0.25

# --------------------------------------------------------------------------- #
# Rate (Success Criterion 2.3.1 text)                                          #
# --------------------------------------------------------------------------- #

# WCAG 2.2 SC 2.3.1: web pages do not contain "anything that flashes more than
# three times in any one second period"  ->  EXCLUSIVE: 3 per second passes,
# 4 per second fails.
MAX_FLASHES_PER_SECOND: int = 3
# WCAG 2.2 SC 2.3.1: "any one second period" - the sliding window length.
RATE_WINDOW_SECONDS: float = 1.0

# --------------------------------------------------------------------------- #
# Advisory - NOT a WCAG rule (modelled on IRIS "extended failure", ADR-009)    #
# --------------------------------------------------------------------------- #

# Two or more qualifying flashes in every trailing second, sustained for longer
# than five seconds, is reported as an advisory.  It never changes the verdict
# because WCAG 2.3.1 contains no such rule; it exists so a reader is warned
# about content that sits just under the limit for a long time.
ADVISORY_MIN_FLASHES_PER_SECOND: int = 2
ADVISORY_MIN_DURATION_SECONDS: float = 5.0  # EXCLUSIVE: the span must be LONGER than 5 s

# --------------------------------------------------------------------------- #
# Engine mechanics (FlashTrace choices, not W3C numbers)                       #
# --------------------------------------------------------------------------- #

# Cells whose flash completions are within this many frames of each other are
# treated as one event (docs/SPEC.md 5.6 steps 1 and 4, ADR-019): an event
# counted at frame t claims the completions of frames t .. t +
# EVENT_FRAME_TOLERANCE, and later frames are scored without them.
EVENT_FRAME_TOLERANCE: int = 1

# A reversal of a cell's signal no larger than this is treated as a plateau,
# not as the end of a monotone run (docs/SPEC.md 5.4, ADR-018).  The values are
# one twentieth of the luminance threshold "10% or more" (0.10 / 20 = 0.005)
# and of the legacy "greater than 20" (20 / 20 = 1.0).  The redness ratio has
# no change threshold of its own (its rule is "ratio >= 0.8 and a chromaticity
# change > 0.2"), so it reuses 0.005, as ADR-018 records.  A wobble this small
# could never be a general or legacy transition itself; it only decides where
# a run ends and, once the signal has settled, whether a small gain moves the
# frame the transition is stamped with (it does not: it is noise).
RUN_REVERSAL_TOLERANCE: dict[str, float] = {"L": 0.005, "ratio": 0.005, "rv": 1.0}

# Names accepted for the ``red_rule`` argument of analyze().
RED_RULES: tuple[str, str] = ("wcag22", "wcag21-legacy")

# Written into every report so a result can be traced to the code that made it.
ENGINE_VERSION: str = "1.0.0"
