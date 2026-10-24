"""Expected outcome of one benchmark page, derived from its parameters.

This is the "answer key" the benchmark scores FlashTrace against. It applies
the WCAG 2.3.1 thresholds as transcribed in docs/SPEC.md section 4 to the
numbers the page was built from. It never looks at pixels.
"""
from bench.params import QUALIFYING_CELLS

GENERAL_MIN_DELTA_L = 0.10          # SPEC T2: "10% or more" (inclusive)
MAX_FLASHES_PER_SECOND = 3          # SPEC T5: "more than three" fails (exclusive)
ADVISORY_MIN_RATE_HZ = 2            # SPEC T7: sustained >= 2 flashes/s for > 5 s

# Boundary bands: pages this close to a limit are reported separately and
# left out of the accuracy score (ADR-006 mentions the +-5% area band).
RATE_BAND_HZ = 0.15
AREA_BAND = 0.0125
AREA_LIMIT = 0.25
DELTA_L_BAND = 0.005
VIDEO_SOFT_DELTA_L = 0.11
VIDEO_SOFT_AREAS = (0.24, 0.26)


def flashes_qualify(total_cells, delta_l_actual, red):
    """Does each on/off cycle count as a qualifying flash at all?

    It needs enough area (more than 25% of a window, i.e. more than 1360
    cells) and either a general luminance step of at least 0.10 or a red
    transition (red <-> grey always is one under WCAG 2.2, see ADR-001).
    """
    large_enough = total_cells > QUALIFYING_CELLS
    general = delta_l_actual >= GENERAL_MIN_DELTA_L
    return large_enough and (general or red)


def boundary_reasons(rate_hz, area_fraction, area_fraction_actual, delta_l_actual,
                     delta_l_target, mechanism):
    """One plain-English reason per boundary rule that applies to the page."""
    reasons = []
    if abs(rate_hz - MAX_FLASHES_PER_SECOND) <= RATE_BAND_HZ:
        reasons.append(f"rate {rate_hz} Hz is within {RATE_BAND_HZ} Hz of the 3 flashes/s limit")
    if (abs(area_fraction - AREA_LIMIT) <= AREA_BAND
            or abs(area_fraction_actual - AREA_LIMIT) <= AREA_BAND):
        reasons.append(f"area fraction {area_fraction} is within {AREA_BAND} of the 25% limit")
    if abs(delta_l_actual - GENERAL_MIN_DELTA_L) <= DELTA_L_BAND:
        reasons.append(f"actual delta L {delta_l_actual:.6f} is within {DELTA_L_BAND} of the 0.10 limit")
    if mechanism == "video":
        if delta_l_target <= VIDEO_SOFT_DELTA_L:
            reasons.append("video: lossy VP8 colours make a luminance step of 0.11 or less uncertain")
        if area_fraction in VIDEO_SOFT_AREAS:
            reasons.append("video: chroma subsampling blurs edges at a near-limit area (0.24 / 0.26)")
    return reasons


def expected_outcome(*, rate_hz, total_cells, delta_l_actual, red, area_fraction,
                     area_fraction_actual, delta_l_target, mechanism):
    """The expected verdict block for one page."""
    qualifying = flashes_qualify(total_cells, delta_l_actual, red)
    fails = rate_hz > MAX_FLASHES_PER_SECOND and qualifying
    types = []
    if fails:
        if delta_l_actual >= GENERAL_MIN_DELTA_L:
            types.append("general")
        if red:
            types.append("red")
    # The page flashes for the whole capture, so 2-3 flashes/s with enough
    # area is sustained flashing: FlashTrace's advisory (SPEC T7), never a FAIL.
    advisory = ADVISORY_MIN_RATE_HZ <= rate_hz <= MAX_FLASHES_PER_SECOND and qualifying
    reasons = boundary_reasons(rate_hz, area_fraction, area_fraction_actual,
                               delta_l_actual, delta_l_target, mechanism)
    return {
        "verdict": "FAIL" if fails else "PASS",
        "types": types,
        "advisory": advisory,
        "boundary": bool(reasons),
        "boundaryReasons": reasons,
    }


def skipped_outcome(reason):
    """The verdict block for a page whose mechanism could not be built."""
    return {
        "verdict": "SKIP",
        "types": [],
        "advisory": False,
        "boundary": False,
        "boundaryReasons": [],
        "skipReason": reason,
    }
