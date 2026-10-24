"""Colour helpers for the FlashTrace benchmark generator.

Standard-library maths only. The formulas follow the WCAG 2.2 glossary entry
"relative luminance" (quoted in docs/SPEC.md section 3.4 and transcribed as
threshold T1 in section 4). The generator needs two things from colours:

1. the relative luminance L of an 8-bit sRGB colour, which is the quantity the
   general-flash threshold ("10% or more of the maximum relative luminance")
   is about; and
2. the grey whose 8-bit value lands closest to a wanted luminance, followed by
   the luminance that grey ACTUALLY has. The truth file records the actual
   value, never the wanted one, so it never claims a luminance step the pixels
   do not deliver.
"""

# WCAG: "L = 0.2126 * R + 0.7152 * G + 0.0722 * B where R, G and B are ..."
LUMINANCE_COEFFICIENTS = (0.2126, 0.7152, 0.0722)
# WCAG: "if RsRGB <= 0.04045 then R = RsRGB/12.92 else R = ((RsRGB+0.055)/1.055) ^ 2.4"
SRGB_LINEAR_CUTOFF = 0.04045
SRGB_LINEAR_DIVISOR = 12.92
SRGB_GAMMA_OFFSET = 0.055
SRGB_GAMMA_SCALE = 1.055
SRGB_GAMMA = 2.4
# The same curve read backwards changes branch at 0.04045 / 12.92.
LINEAR_ENCODE_CUTOFF = SRGB_LINEAR_CUTOFF / SRGB_LINEAR_DIVISOR

RED = (255, 0, 0)


def channel_to_linear(value_8bit):
    """One 8-bit sRGB channel -> linear light in 0..1 (WCAG T1, per channel).

    WCAG: "RsRGB = R8bit/255" and then
    "if RsRGB <= 0.04045 then R = RsRGB/12.92 else R = ((RsRGB+0.055)/1.055) ^ 2.4".
    """
    encoded = value_8bit / 255
    if encoded <= SRGB_LINEAR_CUTOFF:
        return encoded / SRGB_LINEAR_DIVISOR
    return ((encoded + SRGB_GAMMA_OFFSET) / SRGB_GAMMA_SCALE) ** SRGB_GAMMA


def relative_luminance(rgb):
    """Relative luminance L of an (r, g, b) 8-bit colour, 0 = black, 1 = white."""
    r, g, b = (channel_to_linear(c) for c in rgb)
    kr, kg, kb = LUMINANCE_COEFFICIENTS
    return kr * r + kg * g + kb * b


def linear_to_channel_8bit(linear):
    """Linear light 0..1 -> the nearest 8-bit sRGB channel value (inverse of T1)."""
    if linear <= LINEAR_ENCODE_CUTOFF:
        encoded = linear * SRGB_LINEAR_DIVISOR
    else:
        encoded = SRGB_GAMMA_SCALE * linear ** (1 / SRGB_GAMMA) - SRGB_GAMMA_OFFSET
    # Round half up and clamp, so the result is a legal channel value.
    return max(0, min(255, int(encoded * 255 + 0.5)))


def grey_for_luminance(target_l):
    """The grey (v, v, v) whose relative luminance is closest to `target_l`.

    For a grey all three channels share one linear value, and the three WCAG
    coefficients sum to 1.0, so the linear channel value IS the luminance.
    Rounding to 8 bits can land one step away from the best grey, so the two
    neighbours are checked as well and the closest luminance wins.
    """
    guess = linear_to_channel_8bit(target_l)
    candidates = [v for v in (guess - 1, guess, guess + 1) if 0 <= v <= 255]
    best = min(candidates, key=lambda v: (abs(relative_luminance((v, v, v)) - target_l), v))
    return (best, best, best)


def flash_colours(red, base_luminance, delta_l_target):
    """The two states of a flashing element and the luminance step they really have.

    Returns (colour_a, colour_b, delta_l_actual). State A is the darker state.

    * non-red pages: A = grey at `base_luminance`, B = grey at base + target step.
    * red pages: A = pure red (255, 0, 0), whose luminance is exactly 0.2126
      (only the red channel is lit, and fully), B = grey at 0.2126 + target step.
      Red <-> grey is a red transition under WCAG 2.2 (ADR-001, ADR-011) no
      matter how small the luminance step is.

    `delta_l_actual` is recomputed from the 8-bit colours so the truth file
    reports what the screen will show, not what was asked for.
    """
    if red:
        colour_a = RED
        colour_b = grey_for_luminance(relative_luminance(RED) + delta_l_target)
    else:
        colour_a = grey_for_luminance(base_luminance)
        colour_b = grey_for_luminance(base_luminance + delta_l_target)
    delta_l_actual = relative_luminance(colour_b) - relative_luminance(colour_a)
    return colour_a, colour_b, delta_l_actual


def css_rgb(rgb):
    """CSS colour text for an (r, g, b) tuple, e.g. 'rgb(255, 0, 0)'."""
    r, g, b = rgb
    return f"rgb({r}, {g}, {b})"
