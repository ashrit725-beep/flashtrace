# FlashTrace — Specification (SPEC.md)

**Status:** governing document for the whole repository. Code must follow this file; when code and this
file disagree, this file wins and the code is a bug (or this file is amended together with an ADR in
`docs/DECISIONS.md`).

**Disclaimer (appears on every report and screen):** *Analysis aid, not a compliance certification.*
A PASS from FlashTrace means "no WCAG 2.3.1 violation detected under the tested conditions". FlashTrace never
claims that a page is "WCAG compliant".

Written 2026-10-24 before any code, as required by the build brief (Step 0).

---

## 1. Purpose and scope

FlashTrace audits a web page against WCAG 2.2 Success Criterion **2.3.1 Three Flashes or Below Threshold
(Level A)**. It:

1. captures the page as a deterministic sequence of still frames (default 60 frames per second of
   animation time, 10 s, 1024×768 CSS px);
2. runs a pure-NumPy **engine** that applies the W3C thresholds to those frames and produces a verdict,
   per-frame statistics, violations, a per-cell heatmap and a per-second timeline;
3. **attributes** each failing region to the DOM element / animation that causes it (bounding-box ranking,
   then ablation);
4. proposes CSS **fixes** derived from the measured data and **verifies** them by re-capturing.

Out of scope (recorded as limitations in every report, §10): the fine-pattern exception, spatial
(stripe) pattern hazards, physical display calibration, content outside the captured viewport and
duration, HDR / non-sRGB content, cross-origin iframe internals.

---

## 2. Normative sources

| Ref | Document | Version consulted |
|-----|----------|-------------------|
| [WCAG22] | Web Content Accessibility Guidelines (WCAG) 2.2, https://www.w3.org/TR/WCAG22/ | W3C Recommendation 12 December 2024 (https://www.w3.org/TR/2024/REC-WCAG22-20241212/) |
| [WCAG22-SC] | SC 2.3.1, https://www.w3.org/TR/WCAG22/#three-flashes-or-below-threshold | same |
| [WCAG22-GLOSS] | Glossary: *flash*, *general flash and red flash thresholds*, *relative luminance*, *blinking*, https://www.w3.org/TR/WCAG22/#glossary | same |
| [UNDERSTANDING] | Understanding SC 2.3.1, https://www.w3.org/WAI/WCAG22/Understanding/three-flashes-or-below-threshold.html | fetched 2026-10-24 |
| [WCAG21] | WCAG 2.1 (for the history of the red-flash note only), https://www.w3.org/TR/WCAG21/ | W3C Recommendation 06 May 2025 |
| [ISO 9241-391] | Cited by WCAG for the red-flash working definition. Not consulted directly (paywalled); FlashTrace implements the text as printed in WCAG. | — |
| [IRIS] | Electronic Arts IRIS, https://github.com/electronicarts/IRIS — an open-source implementation used as a reference for conventions (not normative). | main, read 2026-10-24 |

The W3C passages below were extracted programmatically from the downloaded HTML on 2026-10-24 and are
reproduced verbatim; nothing was retyped by hand. `docs/tools/w3c_extract.py` prints the same passages from a fresh download so the quotes can be re-verified.

---

## 3. Verbatim W3C text

### 3.1 Success Criterion 2.3.1 [WCAG22-SC]

> Success Criterion 2.3.1 Three Flashes or Below Threshold
> Understanding Three Flashes or Below Threshold |
> How to Meet Three Flashes or Below Threshold
> (Level A)
> Web pages do not contain anything that flashes more than three times in any one second period,
> or the flash is below the general flash and red flash thresholds.
> Note
> Since any content that does not meet this success criterion can interfere with a user's
> ability to use the whole page, all content on the web page (whether it is used to
> meet other success criteria or not) must meet this success criterion. See Conformance Requirement 5: Non-Interference.

### 3.2 Glossary: *flash* [WCAG22-GLOSS]

> flash
> a pair of opposing changes in relative luminance that can cause seizures in some people if it is large enough and in the right frequency
> range
> Note 1
> See general flash and red flash thresholds for information about types of flash that are not allowed.
> Note 2
> See also blinking.

### 3.3 Glossary: *general flash and red flash thresholds* [WCAG22-GLOSS]

> general flash and red flash thresholds
> a flash or rapidly changing image sequence is below the threshold (i.e., content passes) if any of the following are true:
> there are no more than three general flashes and / or no more than three red flashes within any one-second period; or
> the combined area of flashes occurring concurrently occupies no more than a total
> of .006 steradians within any 10 degree visual field on the screen (25% of any 10
> degree visual field on the screen) at typical viewing distance
> where:
> A general flash is defined as a pair of opposing changes in relative luminance of 10% or more of the maximum relative luminance (1.0) where the relative luminance of
> the darker image is below 0.80; and where "a pair of opposing changes" is an increase
> followed by a decrease, or a decrease followed by an increase, and
> A red flash is defined as any pair of opposing transitions involving a saturated red
> Exception: Flashing that is a fine, balanced, pattern such as white noise or an alternating
> checkerboard pattern with "squares" smaller than 0.1 degree (of visual field at typical
> viewing distance) on a side does not violate the thresholds.
> Note 1
> For general software or web content, using a 341 x 256 pixel rectangle anywhere on the displayed screen area when the content is viewed at 1024 x 768 pixels will provide a good estimate of a 10 degree visual field for standard screen sizes and viewing distances (e.g., 15-17 inch screen at 22-26 inches). This resolution of 75 - 85 ppi is known to be lower, and thus more conservative than the nominal CSS pixel resolution of 96 ppi in CSS specifications. Higher resolutions displays showing the same rendering of the content yield smaller and safer images so it is lower resolutions that are used to define the thresholds.
> Note 2
> A transition is the change in relative luminance (or relative luminance/color for
> red flashing) between adjacent peaks and valleys in a plot of relative luminance (or
> relative luminance/color for red flashing) measurement against time. A flash consists
> of two opposing transitions.
> Note 3
> The new working definition in the field for "pair of opposing transitions involving a saturated red" (from WCAG 2.2) is a pair of opposing transitions where, one transition is either to or from a state with a value R/(R + G + B) that is greater than or equal to 0.8, and the difference between states is more than 0.2 (unitless) in the CIE 1976 UCS chromaticity diagram. [ISO_9241-391]
> Note 4
> Tools are available that will carry out analysis from video screen capture. However,
> no tool is necessary to evaluate for this condition if flashing is less than or equal
> to 3 flashes in any one second. Content automatically passes (see #1 and #2 above).

### 3.4 Glossary: *relative luminance* [WCAG22-GLOSS]

> relative luminance
> the relative brightness of any point in a colorspace, normalized to 0 for darkest
> black and 1 for lightest white
> Note 1
> For the sRGB colorspace, the relative luminance of a color is defined as L = 0.2126
> * R + 0.7152 * G + 0.0722 * B where R, G and B are defined as:
> if RsRGB <= 0.04045 then R = RsRGB/12.92 else R = ((RsRGB+0.055)/1.055) ^ 2.4
> if GsRGB <= 0.04045 then G = GsRGB/12.92 else G = ((GsRGB+0.055)/1.055) ^ 2.4
> if BsRGB <= 0.04045 then B = BsRGB/12.92 else B = ((BsRGB+0.055)/1.055) ^ 2.4
> and RsRGB, GsRGB, and BsRGB are defined as:
> RsRGB = R8bit/255
> GsRGB = G8bit/255
> BsRGB = B8bit/255
> The "^" character is the exponentiation operator. (Formula taken from
> [SRGB].)
> Note 2
> Before May 2021 the value of 0.04045 in the definition was different (0.03928). It was taken from an older version of the specification and has been updated. It has no practical effect on the calculations in the context of these guidelines.
> Note 3
> Almost all systems used today to view web content assume sRGB encoding. Unless it
> is known that another color space will be used to process and display the content,
> authors should evaluate using sRGB colorspace. If using other color spaces, see Understanding Success Criterion 1.4.3.
> Note 4
> If dithering occurs after delivery, then the source color value is used. For colors
> that are dithered at the source, the average values of the colors that are dithered
> should be used (average R, average G, and average B).
> Note 5
> Tools are available that automatically do the calculations when testing contrast and
> flash.
> Note 6
> A separate page giving the relative luminance definition using MathML to display the formulas is available.

### 3.5 Glossary: *blinking* [WCAG22-GLOSS]

> blinking
> switch back and forth between two visual states in a way that is meant to draw attention
> Note
> See also flash. It is possible for something to be large enough and blink brightly enough at the
> right frequency to be also classified as a flash.

### 3.6 Understanding SC 2.3.1 — Intent section [UNDERSTANDING]

> Intent
> The intent of this success criterion is to allow users to access the full content
> of a site without inducing seizures due to photosensitivity.
> Individuals who have photosensitive seizure disorders can have a seizure triggered
> by content that flashes at certain frequencies for more than a few flashes. People
> are even more sensitive to red flashing than to other colors, so a special test is
> provided for saturated red flashing. These guidelines were originally based on guidelines for
> the broadcasting industry as adapted for desktop monitors, where content is viewed
> from a closer distance (using a larger angle of vision).
> Flashing can be caused by the display, the computer rendering the image or by the
> content being rendered. The author has no control of the first two. They can be addressed
> by the design and speed of the display and computer. The intent of this criterion
> is to ensure that flicker that violates the flash thresholds is not caused by the
> content itself. For example, the content could contain a video clip or animated image
> of a series of strobe flashes, or close-ups of rapid-fire explosions.
> This success criterion replaces a much more restrictive criterion in WCAG 1.0 that
> did not allow any flashing (even of a single pixel) within a broad frequency range
> (3 to 50 Hz). This success criterion is based on existing specifications in use in
> the UK and by others for television broadcast and has been adapted for computer display
> viewing. In WCAG 2.0, a 1024 x 768 screen was used as the reference screen resolution for the
> evaluation. The 341 x 256 pixel block represents a 10 degree viewport at a typical
> viewing distance. (The 10 degree field is taken from the original specifications and
> represents the central vision portion of the eye, where people are most susceptible
> to photo stimuli.)
> With the proliferation of devices of varying screen sizes (from small handhelds to large living room displays), as well as the adoption of CSS pixels as a density-independent unit of measurement, the prior assessment criteria may seem outdated. However, an image of a consistent size uses up relatively the same percentage of a user's visual field on any device. On a large screen, the image takes up less size, but the large screen takes up a larger part of the visual field. On a mobile screen, the image may take up most or all of the screen; however, the mobile screen itself takes up a smaller portion of the user's visual field. So the same dimension of the flashing content, represented in CSS pixels can still provide a consistent means of assessment. Substituting CSS pixels for the original pixel block means that the combined area of flashing becomes 341 x 256 CSS pixels, or a flashing area of 87,296 CSS pixels.
> Content should be analyzed at the largest scale at which a user may view the content, and at the standard zoom level of the user agent. For example, with a video that may play in an area of a web page and also at full screen, the video should be analyzed for risks at full screen.
> Where video content is provided in color spaces other than sRGB, the version provided with the highest dynamic range should be tested. In such cases the industry standard definition of a general flash is a change in luminance of 20 cd/m2 or more where the darker image is below 160 cd/m2. (ITU-R BT.1702.) This is applicable for standard dynamic range (SDR) and high dynamic range (HDR) content. For HDR content when the darker state is 160 cd/m2 or more, a general flash is one where the Michelson contrast is 1/17 or greater — where the Michelson contrast is calculated as (LHigh - LLow) / (LHigh + LLow), and where LHigh and LLow are the luminance of the high and low luminance states, respectively.
> For short clips that might be looped (such as GIF animations), the content should be analyzed while looping.
> Note
> The specification cannot account for the actual viewing distance that a person chooses. Users that are closer to their screens than the idealized viewing distance will be affected by flashing areas that normatively pass. The same problem applies to users who rely on zoom or screen magnification. Conversely, users who are further away from the screen than the idealized distance should be able to tolerate flashing areas that are larger than the threshold.
> The combined area of flashes occurring concurrently and contiguously means the total
> area that is actually flashing at the same time. It is calculated by adding up the
> contiguous area that is flashing simultaneously within any 10 degree angle of view.
> Note
> The terms "blinking" and "flashing" can sometimes refer to the same content.
> "Blinking" refers to content that causes a distraction problem. Blinking can be allowed
> for a short time as long as it stops (or can be stopped)
> "Flashing" refers to content that can trigger a seizure (if it is more than 3 per
> second and large and bright enough). This cannot be allowed even for a second or it
> could cause a seizure. And turning the flash off is also not an option since the seizure
> could occur faster than most users could turn it off.
> Blinking usually does not occur at speeds of 3 per second or more, but it can. If
> blinking occurs faster than 3 per second, it would also be considered a flash.
> Note (new in WCAG 2.2)
> The new working definition in the field for "pair of opposing transitions involving a saturated red" is a pair of opposing transitions where, one transition is either to or from a state with a value R/(R + G + B) that is greater than or equal to 0.8, and the difference between states is more than 0.2 (unitless) in the CIE 1976 UCS chromaticity diagram. [ISO 9241-391]
> The chromaticity difference is calculated as:
> SQRT( (u'1 - u'2)^2 + (v'1 - v'2)^2 )
> where u'1 and v'1 are chromaticity coordinates of State 1 and u'2 and v'2 are chromaticity coordinates of State 2. The 1976 UCS chromaticity coordinates of u' and v' are calculated as:
> u' = 4 * X / (X + 15 * Y + 3 * Z)
> v' = 9 * Y / (X + 15 * Y + 3 * Z)
> where X, Y, and Z are the tristimulus values of a color in the CIE XYZ colorspace, which can be calculated as:
> X = 0.4124564 * R + 0.3575761 * G + 0.1804375 * B
> Y = 0.2126729 * R + 0.7151522 * G + 0.0721750 * B
> Z = 0.0193339 * R + 0.1191920 * G + 0.9503041 * B
> where R, G, & B are values that range from 0-1 as specified in “relative luminance” definition.

---

## 4. Thresholds transcribed and how FlashTrace applies them

Each row names the constant used in `engine/constants.py`. Inclusive/exclusive boundaries follow the W3C
wording exactly ("10% or more" is inclusive; "below 0.80" is exclusive; "more than three" is exclusive; "no
more than … 25%" means exactly 25% passes).

| # | W3C wording (see §3) | Constant | FlashTrace rule |
|---|----------------------|----------|-----------------|
| T1 | *relative luminance* Note 1: `L = 0.2126*R + 0.7152*G + 0.0722*B`, with each channel linearised by `if C ≤ 0.04045 then C/12.92 else ((C+0.055)/1.055)^2.4`, `C = C8bit/255` | `LUMINANCE_COEFFICIENTS = (0.2126, 0.7152, 0.0722)`, `SRGB_LINEAR_CUTOFF = 0.04045`, `SRGB_LINEAR_DIVISOR = 12.92`, `SRGB_GAMMA_OFFSET = 0.055`, `SRGB_GAMMA_SCALE = 1.055`, `SRGB_GAMMA = 2.4` | Exactly as written. The constant is 0.04045 (WCAG Note 2 records that 0.03928 was used before May 2021). |
| T2 | general flash: "a pair of opposing changes in relative luminance of 10% or more of the maximum relative luminance (1.0) where the relative luminance of the darker image is below 0.80" | `GENERAL_MIN_DELTA_L = 0.10` (inclusive), `GENERAL_DARK_STATE_MAX_L = 0.80` (exclusive) | A **general transition** is a monotone run of a cell's L whose magnitude `|L_end − L_start| ≥ 0.10` and whose darker end has `min(L_start, L_end) < 0.80`. A **general flash** is two consecutive opposing general transitions (§5.5). |
| T3 | red flash, Note 3 (WCAG 2.2): "a pair of opposing transitions where, one transition is either to or from a state with a value R/(R + G + B) that is greater than or equal to 0.8, and the difference between states is more than 0.2 (unitless) in the CIE 1976 UCS chromaticity diagram" | `RED_SATURATION_MIN_RATIO = 0.8` (inclusive), `RED_MIN_DELTA_UV = 0.2` (exclusive) | A **red transition** is a monotone run of a cell's redness ratio `R/(R+G+B)` (§5.3) whose two end states satisfy: at least one end has ratio ≥ 0.8 **and** the Euclidean distance between the ends in CIE 1976 (u′, v′) is > 0.2. A **red flash** is two consecutive opposing red transitions. **Correction to the build brief:** the brief's working value ("(R−G−B)×320 … change > 20") is the pre-2022 working definition that appeared in WCAG 2.0/2.1; both current Recommendations (WCAG 2.2 of 12 Dec 2024 and WCAG 2.1 as republished 06 May 2025) print the CIE 1976 UCS definition above. FlashTrace therefore uses the UCS rule for verdicts. The legacy rule is kept only as an explicitly labelled comparison mode (`red_rule="wcag21-legacy"`, ADR-001) because reference tools such as PEAT and IRIS still implement it. |
| T4 | area: "25% of any 10 degree visual field"; Note 1: "a 341 x 256 pixel rectangle anywhere on the displayed screen area when the content is viewed at 1024 x 768 pixels"; [UNDERSTANDING]: "341 x 256 CSS pixels, or a flashing area of 87,296 CSS pixels" | `REFERENCE_VIEWPORT = (1024, 768)`, `WINDOW_PX = (341, 256)`, `MAX_AREA_FRACTION = 0.25` (exclusive: > 0.25 qualifies) | A flash **event** is *qualifying* when the cells completing it cover **more than 25%** of at least one 341×256 px window placed anywhere in the viewport (§5.6). For viewports whose width is not 1024 the window is scaled by the width ratio and this is recorded as an assumption in the report (ADR-007). |
| T5 | SC text: "flashes more than three times in any one second period"; glossary: "no more than three general flashes and / or no more than three red flashes within any one-second period" | `MAX_FLASHES_PER_SECOND = 3` (exclusive), `RATE_WINDOW_SECONDS = 1.0` | **FAIL** when the number of qualifying general flashes **or** the number of qualifying red flashes whose completing frame falls inside any window of `fps` consecutive frames is **greater than 3** (§5.7). The two types are counted separately and never summed. |
| T6 | Exception: "a fine, balanced, pattern such as white noise or an alternating checkerboard pattern with 'squares' smaller than 0.1 degree" | — | **Not implemented.** Listed in every report's `limitations[]`. (Averaging into 4×4 px cells already attenuates sub-cell patterns, which makes FlashTrace lenient, not strict, for such content.) |
| T7 | *not WCAG* — advisory modelled on IRIS "extended failure" (2–3 flashes/s sustained over 5 s) | `ADVISORY_MIN_FLASHES_PER_SECOND = 2`, `ADVISORY_MIN_DURATION_SECONDS = 5.0` (exclusive) | Emit an **advisory** (never a FAIL) when, for one type, the trailing-one-second qualifying-flash count is ≥ 2 at every frame of a span longer than 5 s (§5.9). Reports label it "advisory, not a WCAG 2.3.1 criterion". |

Derived numbers (for readers; the code derives them from the constants above, never hard-codes them):
window area 341×256 = 87 296 CSS px; 25% = 21 824 px. In 4×4 px cells the window is 85×64 = 5 440 cells
(341/4 = 85.25 rounded to 85, 256/4 = 64) and the qualifying threshold is > 1 360 cells (ADR-006 records the
0.3% under-estimate caused by rounding).

---

## 5. Engine definition (`engine/`)

Pure functions over NumPy arrays. No browser, no I/O, no randomness, no threads. Same input ⇒ bit-identical
output (float64 throughout, fixed operation order).

### 5.1 Input

* `frames`: `uint8[T, H, W, 3]` sRGB (R, G, B order), T ≥ 2.
* `fps`: frames per second of *animation time* (default 60). Frame `i` is at `t_i = i / fps` seconds.
* `viewport`: `(W_css, H_css)`; `frames` are at device scale factor 1 so `W == W_css`. A frame width that differs
  from the viewport width is rejected with `ValueError` (the window size is derived from the viewport width).
* `red_rule`: `"wcag22"` (default) or `"wcag21-legacy"` (comparison only).

### 5.2 Cells

The frame is divided into 4×4 px cells (`CELL_PX = 4`). Pixels beyond the last full cell (when W or H is
not a multiple of 4) are dropped (ADR-002). Each pixel is linearised per channel (T1) and the cell colour is
the **mean linear RGB** of its 16 pixels (averaging in linear light; ADR-002). 1024×768 ⇒ 256×192 cells.

### 5.3 Per-cell signals (per frame)

* `L[t, y, x]` — relative luminance of the cell's mean linear RGB (T1).
* `ratio[t, y, x] = R8/(R8+G8+B8)` — redness ratio on 0–255 **sRGB-encoded** components of the cell colour
  (the cell's mean linear RGB re-encoded to sRGB and **rounded to whole 0–255 values**, so a colour whose ratio is
  exactly 0.8 measures exactly 0.8), as the brief specifies (ADR-003, ADR-020). The ratio is `0` when
  `R8+G8+B8 = 0` and when `R8 < 16`: a red that dark has no visible hue (ADR-020). `saturated_red = ratio ≥ 0.8`.
* `uv[t, y, x]` — CIE 1976 UCS chromaticity (u′, v′) of the cell: linear RGB → XYZ with the IEC 61966-2-1
  sRGB/D65 matrix (rows: X = 0.4124 R + 0.3576 G + 0.1805 B; Y = 0.2126 R + 0.7152 G + 0.0722 B;
  Z = 0.0193 R + 0.1192 G + 0.9505 B; the Y row is identical to T1), then
  `u′ = 4X/(X+15Y+3Z)`, `v′ = 9Y/(X+15Y+3Z)`. When `X+15Y+3Z = 0` (pure black) the chromaticity is
  undefined; FlashTrace uses the chromaticity of white computed with the same matrix (≈ `(0.1978, 0.4683)`, the
  D65 white point) so that black counts as achromatic (ADR-011).
* Legacy mode only: `RV[t, y, x] = max(0, (R−G−B)) × 320` on 0–1 sRGB-encoded components.

### 5.4 Transitions (per cell, per signal)

Following Note 2 ("the change … between adjacent peaks and valleys in a plot … against time"), a cell's
signal `s[t]` is split into **monotone runs**: a run starts at an extremum and ends at the next extremum.
Plateaus do not end a run, and neither does a reversal smaller than the signal's *reversal tolerance*
(0.005 for L and for the redness ratio, 1.0 for the legacy red value; one twentieth of the threshold, so such a
wobble could never be a transition itself) (ADR-018). The run's end frame is the **first frame at which the
extremum value is reached** (for a square wave: the first frame showing the new state). A run that is still open
at the last frame is evaluated there and stamped at its extremum frame in the same way (ADR-004, ADR-018). A run from state A (frame `a`) to state B
(frame `b`) is a **transition timestamped at frame `b`** when:

* general: signal is `L`; `|L_B − L_A| ≥ 0.10` and `min(L_A, L_B) < 0.80` (T2);
* red (wcag22): signal is `ratio`; `max(ratio_A, ratio_B) ≥ 0.8` and `‖uv_B − uv_A‖₂ > 0.2` (T3);
* red (wcag21-legacy): signal is `RV`; `max(ratio_A, ratio_B) ≥ 0.8` and `|RV_B − RV_A| > 20`.

Runs that do not meet the rule produce no transition but still reset the run start (the next run starts at
their end extremum). Transition sign is the run direction.

### 5.5 Flashes (per cell, per type)

A cell's transitions are paired **non-overlapping, in order** (1st+2nd, 3rd+4th, …), and only opposing
transitions pair. Two same-direction transitions in a row are possible when a run below the threshold sits
between them (e.g. rise, small dip, rise); the newer one then replaces the pending one (ADR-018). Each pair is one **flash, timestamped at the frame of
its second (completing) transition** (ADR-005). A 4 Hz square wave therefore yields 8 transitions and 4
flashes per second, which is the counting convention of PEAT/IRIS ("more than 6 transitions in 1 s" ⇔ "more
than 3 flashes").

### 5.6 Area: qualifying flash events

For each type separately, `M[t]` is the boolean cell mask "completes a flash at frame t".

1. **1-frame tolerance:** `E[t] = M[t−1] ∪ M[t]` (cells whose completions are within one frame of each other
   belong to the same event; `M[−1] = ∅`). Looking backwards keeps the event on the frame where the flash
   completes (ADR-019).
2. **Window scoring:** with a 2-D integral image of `E[t]`, the count of flashing cells inside every
   `(wy × wx)`-cell window position is computed; `area_max[t]` is the maximum fraction
   `count / window_cells` and `window_argmax[t]` its top-left cell (ties → smallest y, then smallest x), where
   `window_cells` is the size of the **full** window (85 × 64 = 5 440 cells at 1024 px wide) even when a short
   viewport forces the scanned window to be clipped to the grid: 25% is an absolute area, 21 824 px (ADR-019).
   This is O(cells) per frame and makes the result independent of where the region sits (translation
   invariance).
3. **Qualifying:** `Q_raw[t] = area_max[t] > 0.25` (T4).
4. **Counting events:** one event spans at most two frames (its completing frame and the next one, the
   1-frame tolerance). So a maximal run of `n` consecutive `Q_raw` frames starting at frame `s` contains
   `ceil(n/2)` events, counted at frames `s, s+2, s+4, …`. A single flash gives a run of 2 frames and is
   counted once, at its completing frame; a 30 Hz strobe at 60 fps keeps `Q_raw` true on every frame and is
   counted 30 times per second. (ADR-019, which replaces the leading-edge rule of ADR-006.)

The window in cells is `wx = round(341·s/4)`, `wy = round(256·s/4)` with `s = W_css/1024` (ADR-007), rounding
halves up; the scanned window is clamped to the cell grid, the area denominator is not (step 2).

### 5.7 Rate and verdict

For each type, `events = {t : Q[t]}`. For every window start `t0 ∈ [0, T−fps]` (when `T < fps` the single
window is the whole capture), `count(t0) = |{t ∈ events : t0 ≤ t ≤ t0+fps−1}|`. `max_rate = max count(t0)`.

* **FAIL** if `max_rate > 3` for general **or** for red (T5).
* Otherwise **PASS** (reported as "no 2.3.1 violation detected under the tested conditions").
* **INCONCLUSIVE** is decided by the layer above the engine (§6.4).

A **Violation** is a maximal run of consecutive failing window starts for one type:
`start = first t0`, `end = last t0 + fps − 1` (frames), `type`, `maxRatePerSec`, `window` (the argmax
window, in CSS px, of the event with the largest `area_max` inside the violation), `frameIndices` (the
event frames inside it).

### 5.8 Outputs

* `FrameStats[t]`: `generalCells`, `redCells` (cells completing a flash), `generalAreaMax`, `redAreaMax`,
  `generalWindow`, `redWindow` (cell coordinates), `generalQualifying`, `redQualifying` (after leading edge),
  `generalTrailing1s`, `redTrailing1s` (count in the window ending at t).
* `Violation[]` (§5.7). `advisory[]` (§5.9).
* `heatmap`: `uint16[cells_y, cells_x]`, per-cell count of completed flashes of either type.
* `timeline[k]`: for each second `k` (`t ∈ [k·fps, (k+1)·fps)`), the number of qualifying general and red
  events. There are `ceil(T/fps)` bins; a trailing partial second gets its own bin with its raw count, so the
  bins always add up to the event totals (ADR-021).

### 5.9 Advisory (non-WCAG)

For each type, let `trailing[t] = count` of events in `[t−fps+1, t]`. A maximal span of consecutive frames
with `trailing[t] ≥ 2` longer than `5·fps` frames produces one advisory
`{type: "sustained-flashing", flashType, start, end, meanRatePerSec}` with the label
"advisory — not a WCAG 2.3.1 criterion" (T7, ADR-009). Spans already inside a FAIL violation are still
reported (they describe different things).

### 5.10 Acceptance tests (`engine/tests/`, hand-built arrays, all must pass before any browser code)

1. 4 Hz full-screen square wave (black ↔ white) ⇒ FAIL, `maxRatePerSec == 4`.
2. 3 Hz full-screen square wave ⇒ PASS.
3. 4 Hz square wave covering 20% of one window ⇒ PASS; 26% ⇒ FAIL.
4. ΔL = 0.09 ⇒ PASS (no transition).
5. Both states above L 0.80 (ΔL ≥ 0.10) ⇒ PASS.
6. Saturated red toggling at 4 Hz against an equal-luminance grey (ΔL < 0.10) ⇒ FAIL via the red rule and no
   general violation.
7. A region split across two window positions that only exceeds 25% when one window contains both parts
   ⇒ FAIL (and each half alone ⇒ PASS).
8. Translation invariance: the same region at two offsets gives identical verdict, counts and area.
9. Determinism: the same input analysed twice yields identical outputs (array equality and identical JSON).
10. Red ↔ dark red (same chromaticity, ΔL ≥ 0.10) ⇒ general flash only, no red flash (wcag22); the
    legacy mode counts it as red (documents the difference between the two rules).
11. Full-screen black ↔ white at 15, 20, 24 and 30 Hz ⇒ FAIL with `maxRatePerSec` equal to the frequency
    (near the 60 fps sampling limit the events must not merge; ADR-019).
12. Colours whose redness ratio is exactly 0.8 on 0–255 values (e.g. (204,51,0), (16,4,0)) count as saturated
    red (inclusive boundary); (205,52,0) does not.
13. Near-black red noise, rgb(0,0,0) ↔ rgb(1,0,0) at 4 Hz full screen ⇒ PASS, no red flash (ADR-020).
14. On a 1024×100 viewport a 4 Hz flash of 600 cells (11% of the full 5 440-cell window) ⇒ PASS; the area
    fraction is measured against the full window, not the clipped one (ADR-019).

---

## 6. Capture (`capture/`)

`capture(url_or_path, duration_s, viewport, fps=60, interaction_script=None) -> Capture{frames, meta}`
with `meta = {strategy, deterministic, frameHashes[], hash, uncontrollable[], fallbackReason, notes[]}`.

### 6.1 Frame hashing

`frameHashes[i] = sha256(raw RGB bytes of frame i)` (decoded pixels, not PNG bytes, so hashes are
independent of the PNG encoder). `hash = sha256(concatenation of all frameHashes)`.

### 6.2 Strategies (auto-selected, recorded in the report)

* **A `waapi_step` (default).** Playwright Chromium, device scale factor 1, viewport as requested. Install
  `page.clock` at virtual time 0 (controls `Date`, timers, `requestAnimationFrame`, `performance.now`). Pause
  every animation from `document.getAnimations()` (including same-origin frames) and, per frame, set each
  animation's `currentTime = t_i − startOffset`, seek `<video>` elements to `t_i` and wait for `seeked`, then
  `clock.run_for(1000/fps)` so JS/rAF animations advance, then screenshot (`animations="allow"`). Animations
  discovered after frame 0 are treated as starting at the frame in which they first appear. Animations inside
  shadow roots (open or closed, recorded by an init script that wraps `attachShadow`) are driven the same way;
  SVG SMIL timelines are paused and set with `setCurrentTime`; an animation the page resumes later is
  re-adopted; `Math.random` is replaced by a fixed-seed generator (ADR-022).
  **Double-shot check:** at frame 0 and at the last frame the screenshot is taken twice at the same virtual
  time, 100 ms of real time apart; if the two hashes differ, something animates outside the controlled clocks
  ⇒ `deterministic=false`. A `<video>` seek or an animation-ready wait that times out also sets
  `deterministic=false` with a note.
* **B `virtual_time`.** CDP `Emulation.setVirtualTimePolicy` with a `1000/fps` ms budget per frame, plus
  `HeadlessExperimental.beginFrame` where the launched Chromium supports it. Same double-shot check. The page is
  loaded under 250 ms budgets and pre-rolled to a fixed 2 000 ms before frame 0 (pausing virtual time before
  navigation never loads a page). Without BeginFrameControl, rendering follows the real clock; a once-per-build
  self-test detects this and auto mode then skips B (ADR-023).
* **C `realtime`.** CDP `Page.startScreencast` with timestamps, resampled to `fps` by holding the latest
  frame at or before `t_i`. `deterministic=false`; the report and UI show a warning banner; PASS is
  downgraded to INCONCLUSIVE (§6.4).

Fall-through A → B → C happens when the page contains *uncontrollable* content: animated GIF/WebP/APNG
(detected by file header, not extension), WebGL canvases, cross-origin iframes, or when the double-shot check
fails. The reason is recorded in `meta.fallbackReason`.

### 6.3 Determinism test (M2)

Three synthetic pages (CSS keyframes, WAAPI, rAF+JS) captured twice under A and under B must yield identical
`frameHashes` lists.

### 6.4 Verdict escalation (CLI/server layer)

* Capture error or fewer than `2·fps` frames ⇒ **INCONCLUSIVE**, reason given.
* Engine FAIL ⇒ **FAIL** regardless of strategy (what was observed did flash).
* Engine PASS with `deterministic=false` ⇒ **INCONCLUSIVE** ("page passed under non-deterministic capture;
  frames may have been missed").
* Engine PASS with `deterministic=true` ⇒ **PASS**.

Exit codes: 0 PASS, 1 FAIL, 2 error; INCONCLUSIVE exits 1 unless `--no-fail`.

---

## 7. Attribution and fixes (summary; details in code comments and ADRs)

* Candidates per failing window: animation targets from `getAnimations()` (effect target, name, duration,
  keyframes), `elementsFromPoint` on a 5×5 grid inside the window, and `<video>/<canvas>/<img>` with animated
  formats. Each candidate gets a stable `data-ft-id`, a unique CSS selector, bounding rect and animation
  details. Cross-origin iframes are reported as an "unattributable region".
* Stage 1 (bbox): rank by overlap between candidate rect and the failing cells of the event.
* Stage 2 (ablation): for the top k ≤ 6, re-capture with the candidate suppressed
  (`[data-ft-id="X"]{animation-play-state:paused!important;transition:none!important}`; media:
  `visibility:hidden!important`). Score = reduction in qualifying flashes. Greedy minimal culprit set.
* Fixes (≤ 3 per culprit, CSS override strings derived from measured values): rate
  (`animation-duration` for ≤ 2.5 flashes/s), luminance (ΔL < 0.10 by lowering the brighter state's
  lightness, or opacity), red (desaturate below 0.8), area (scale factor reported, never auto-applied).
  Verify = inject as `<style>`, re-capture with the same strategy, re-run the engine; "verified" only when the
  re-run passes under deterministic capture.

---

## 8. Report

`engine/schema/report.schema.json` is the contract; every report is validated against it before it is
written. Field list in the build brief; additions: `settings.redRule`, `settings.windowPx`, `assumptions[]`,
`capture.frameHashes[]`, `capture.fallbackReason`, `engineVersion`.

---

## 9. Safety requirements for FlashTrace's own UI, CLI and docs

1. Nothing FlashTrace renders flashes faster than 3 Hz: evidence is still frames; frame scrubbing is manual
   (prev/next) and never auto-plays; charts are static; no blinking indicators.
2. The demo site (which intentionally flashes) is served only by `flashtrace demo`, behind a warning
   interstitial at `/`; the flashing page itself lives at `/site/` and honours `prefers-reduced-motion`.
3. Every report, HTML export and screen carries the disclaimer (top of this file).
4. The UI respects `prefers-reduced-motion`, is keyboard operable, uses text/icons as well as colour for
   verdicts, and passes an axe-core run with 0 violations on every screen.

---

## 10. Known limitations (copied into every report's `limitations[]`)

* Headless sRGB rendering is not a physical display; brightness, gamma and viewing distance differ per user.
* Canvas/WebGL attribution is element-level only (what is drawn inside cannot be attributed).
* Cross-origin iframes are unattributable and cannot be clock-controlled.
* Animated GIF/WebP/APNG force non-deterministic capture (strategy C) and a PASS becomes INCONCLUSIVE.
* The fine-pattern exception (T6) and spatial-pattern hazards are not evaluated.
* Only the captured viewport and duration are analysed; content revealed by scrolling or interaction is
  analysed only if an interaction script drives it.
* Window size for viewports other than 1024 px wide is scaled by the width ratio (an assumption).
* No numbers in this repository are estimates: accuracy, agreement and runtime figures exist only in
  `docs/RESULTS.md`, produced by `flashtrace bench`, with date and commit hash.
