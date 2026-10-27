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
| T4 | area: "25% of any 10 degree visual field"; Note 1: "a 341 x 256 pixel rectangle anywhere on the displayed screen area when the content is viewed at 1024 x 768 pixels"; [UNDERSTANDING]: "341 x 256 CSS pixels, or a flashing area of 87,296 CSS pixels" | `REFERENCE_VIEWPORT = (1024, 768)`, `WINDOW_PX = (341, 256)`, `MAX_AREA_FRACTION = 0.25` (exclusive: > 0.25 qualifies) | A flash **event** is *qualifying* when the pixels completing it cover **more than 25%** of at least one 341×256 px window placed anywhere in the viewport (§5.6, ADR-044). For viewports whose width is not 1024 the window is scaled by the width ratio and this is recorded as an assumption in the report (ADR-007). |
| T5 | SC text: "flashes more than three times in any one second period"; glossary: "no more than three general flashes and / or no more than three red flashes within any one-second period" | `MAX_FLASHES_PER_SECOND = 3` (exclusive), `RATE_WINDOW_SECONDS = 1.0` | **FAIL** when the number of qualifying general flashes **or** the number of qualifying red flashes whose completing frame falls inside any window of `fps` consecutive frames is **greater than 3** (§5.7). The two types are counted separately and never summed. |
| T6 | Exception: "a fine, balanced, pattern such as white noise or an alternating checkerboard pattern with 'squares' smaller than 0.1 degree" | — | **Not implemented.** Listed in every report's `limitations[]`. (A balanced pattern inside a 4×4 px cell, half its pixels brightening while the other half darken, leaves the cell's mean signal still, so no run starts there (§5.4) and nothing is counted: FlashTrace is lenient, not strict, for such content; ADR-044.) |
| T7 | *not WCAG* — advisory modelled on IRIS "extended failure" (2–3 flashes/s sustained over 5 s) | `ADVISORY_MIN_FLASHES_PER_SECOND = 2`, `ADVISORY_MIN_DURATION_SECONDS = 5.0` (exclusive) | Emit an **advisory** (never a FAIL) when, for one type, the trailing-one-second qualifying-flash count is ≥ 2 at every frame of a span longer than 5 s (§5.9). Reports label it "advisory, not a WCAG 2.3.1 criterion". |

Derived numbers (for readers; the code derives them from the constants above, never hard-codes them):
window area 341×256 = 87 296 CSS px; 25% = 21 824 px. In 4×4 px cells the window is 85×64 = 5 440 cells
(341/4 = 85.25 rounded to 85, 256/4 = 64) and the qualifying threshold is > 1 360 cells (ADR-006 records the
0.3% under-estimate caused by rounding). Areas are counted in pixels (§5.6, ADR-044): the scored window is
85 × 64 × 16 = 87 040 px and an event needs more than 21 760 flashing pixels inside it.

---

## 5. Engine definition (`engine/`)

Pure functions over NumPy arrays. No browser, no I/O, no randomness, no threads. Same input ⇒ bit-identical
output (float64 throughout, fixed operation order).

### 5.1 Input

* `frames`: `uint8[T, H, W, 3]` sRGB (R, G, B order), T ≥ 2.
* `fps`: frames per second of *animation time* (default 60). Frame `i` stands for the time slot
  `[i / fps, (i + 1) / fps)` and shows the page a quarter of the way into it, at `t_i = (i + 1/4) / fps` seconds
  (ADR-029). Reports label frames by their slot start, `i / fps`.
* `viewport`: `(W_css, H_css)`; `frames` are at device scale factor 1 so `W == W_css`. A frame width that differs
  from the viewport width is rejected with `ValueError` (the window size is derived from the viewport width).
* `red_rule`: `"wcag22"` (default) or `"wcag21-legacy"` (comparison only).

### 5.2 Cells

The frame is divided into 4×4 px cells (`CELL_PX = 4`). Pixels beyond the last full cell (when W or H is
not a multiple of 4) are dropped (ADR-002). Each pixel is linearised per channel (T1) and the cell colour is
the **mean linear RGB** of its 16 pixels (averaging in linear light; ADR-002). 1024×768 ⇒ 256×192 cells.

The cell colour's signals (§5.3) decide **when** a cell's runs start and end (§5.4). Each frame also hands over
every cell's 16 pixels unchanged (`engine/cells.py` `pixel_blocks`), because **whether** a run is a transition is
decided pixel by pixel (§5.4, ADR-044): the mean alone would count a whole cell of which a black/white edge
covers 2 pixels, and miss a low-contrast element that covers half a cell.

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
  undefined. When it is pure black, **or when R8, G8 and B8 are all below 16** (near-black, ADR-020), FlashTrace
  uses the chromaticity of white computed with the same matrix (≈ `(0.1978, 0.4683)`, the D65 white point), so
  that black and every colour that looks black count as achromatic (ADR-011, ADR-043).
* Legacy mode only: `RV[t, y, x] = max(0, (R−G−B)) × 320` on 0–1 sRGB-encoded components.

The same signals are computed for single pixels, from each pixel's own whole 0–255 values (`measure_general`,
`measure_red_wcag22`, `measure_red_legacy`); the per-pixel rule of §5.4 uses them.

### 5.4 Transitions (per cell, per signal)

Following Note 2 ("the change … between adjacent peaks and valleys in a plot … against time"), a cell's
signal `s[t]` is split into **monotone runs**: a run starts at an extremum and ends at the next extremum.
Plateaus do not end a run, and neither does a reversal smaller than the signal's *reversal tolerance*
(0.005 for L and for the redness ratio, 1.0 for the legacy red value; one twentieth of the threshold, so such a
wobble could never be a transition itself) (ADR-018). The run's end frame (its *stamp*) is the frame at which the
signal **arrived** at its extremum: while the signal keeps moving, every new extremum moves the stamp, so a smooth
fade is stamped at the bottom of its valley; once the signal has stood still or wobbled back by no more than the
tolerance (it has *settled*), a further gain moves the stamp only when it goes more than the tolerance beyond the
stamped value, and the signal then counts as moving again (ADR-018, ADR-038). For a square wave the stamp is the
first frame showing the new state; sub-tolerance noise on a flat colour cannot move it. The threshold rule below is
always evaluated on the true extremum.

**End of the capture (ADR-042).** A run still open at the last frame is evaluated only if its signal has settled
(stood still or turned back by no more than the tolerance since its stamp); it is then stamped at its stamp frame as
usual. A run still moving at the last frame has not reached its extremum inside the capture and gives no transition.

**Pixel by pixel (ADR-044).** The run boundaries above come from the cell's signal. When a run ends, the rule below
is applied to **each of the cell's 16 pixels**: state A is the pixel's colour where the run started (the previous
extremum) and state B its colour at the run's extremum. The cell makes a **transition timestamped at frame `b`** (the
run's stamp) when at least one of its pixels passes, and the transition records which pixels did. A cell whose 16
pixels are one colour at both ends is evaluated once. A balanced pattern inside a cell (half its pixels rising while
the other half fall) leaves the cell's signal still, so no run starts and nothing is evaluated (T6). The rule, for
one pixel going from state A to state B:

* general: signal is `L`; `|L_B − L_A| ≥ 0.10` and `min(L_A, L_B) < 0.80` (T2);
* red (wcag22): signal is `ratio`; `max(ratio_A, ratio_B) ≥ 0.8` and `‖uv_B − uv_A‖₂ > 0.2` (T3);
* red (wcag21-legacy): signal is `RV`; `max(ratio_A, ratio_B) ≥ 0.8` and `|RV_B − RV_A| > 20`.

Runs in which no pixel meets the rule produce no transition but still reset the run start (the next run starts at
their end extremum). Transition sign is the run direction.

### 5.5 Flashes (per cell, per type)

Transitions are paired **per pixel, non-overlapping, in order** (1st+2nd, 3rd+4th, …), and only opposing
transitions pair: each pixel a transition reached (§5.4) pairs it with that pixel's pending transition (ADR-044).
Two same-direction transitions in a row are possible when a run below the threshold sits
between them (e.g. rise, small dip, rise); the newer one then replaces the pending one (ADR-018). Each pair is one **flash, timestamped at the frame of
its second (completing) transition** (ADR-005). A cell *completes a flash* at frame `t` when at least one of its
pixels does; §5.6 counts how many. A 4 Hz square wave therefore yields 8 transitions and 4
flashes per second, which is the counting convention of PEAT/IRIS ("more than 6 transitions in 1 s" ⇔ "more
than 3 flashes").

### 5.6 Area: qualifying flash events

For each type separately, `M[t]` holds, per cell, the number of its pixels (0–16) that complete a flash at frame `t`
(ADR-044).

1. **1-frame tolerance:** `E[t] = min(16, M[t−1] + M[t])` (pixels whose completions are within one frame of each
   other belong to the same event; `M[−1] = 0`). The capped sum is the union: a pixel cannot complete two flashes
   one frame apart (a flash needs two of its transitions, on different frames), so the pixels counted at `t−1` and
   at `t` are different pixels. Looking backwards keeps the event on the frame where the flash completes (ADR-019).
2. **Window scoring:** with a 2-D integral image of `E[t]`, the number of flashing pixels inside every
   `(wy × wx)`-cell window position is computed; `area_max[t]` is the maximum fraction
   `count / window_pixels` and `window_argmax[t]` its top-left cell (ties → smallest y, then smallest x), where
   `window_pixels` is the size of the **full** window (85 × 64 = 5 440 cells = 87 040 px at 1024 px wide) even when a
   short viewport forces the scanned window to be clipped to the grid: 25% is an absolute area (ADR-019). An event
   therefore needs more than 21 760 flashing pixels in one window position. This is O(cells) per frame. Because
   pixels are counted, the result does not depend on where a region sits (translation invariance, ADR-044): it is
   exact when the cells a region touches fit inside one window position. Windows slide in whole cells (4 px steps),
   so a region that spans more cells than the window (wider or taller than about 340 × 256 px) can lose up to 3 px
   along such an edge, in the lenient direction (§10).
3. **Qualifying:** `Q_raw[t] = area_max[t] > 0.25` (T4).
4. **Counting events (claim rule, ADR-038):** going forward through the frames, an event counted at frame `t`
   *claims* frames `t` and `t+1` (the 1-frame tolerance): their completions belong to it. A frame `t` with
   `Q_raw[t]` that is not claimed yet is scored again using only the completions of frames `t−1` and `t` that no
   earlier event has claimed; when that best-window share is still **> 0.25**, an event is counted at `t`
   (`Q[t]`). Consequences, each pinned by `engine/tests/test_area.py` or the acceptance tests:
   * the same cells completing a flash on `n` consecutive frames give `ceil(n/2)` events, at `s, s+2, s+4, …`;
     a single flash is counted once, at its completing frame; a 30 Hz strobe at 60 fps is counted 30 times
     per second;
   * one flash whose cells complete on two neighbouring frames (a smooth fade over dim and bright cells) keeps
     `Q_raw` true on three frames but is counted **once** (item 15);
   * two separate regions that each exceed 25% on their own and complete **one** frame apart are counted as
     **one** event; **two or more** frames apart they are two events. The count is therefore not monotone in
     the delay between such regions; ADR-038 records this trade-off.

   (ADR-019 replaced the leading-edge rule of ADR-006; ADR-038 replaced ADR-019's `ceil(n/2)`-per-run count with
   the claim rule.)

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

* `FrameStats[t]`: `generalCells`, `redCells` (cells with at least one pixel completing a flash at t),
  `generalAreaMax`, `redAreaMax` (the best window's share of the full window, measured in pixels, §5.6 step 2),
  `generalWindow`, `redWindow` (cell coordinates), `generalQualifying`, `redQualifying` (true at the frames where
  a qualifying event is counted, §5.6 step 4),
  `generalTrailing1s`, `redTrailing1s` (count in the window ending at t).
* `Violation[]` (§5.7). `advisory[]` (§5.9).
* `heatmap`: `uint16[cells_y, cells_x]`, per-cell count of completed flashes of either type (a transition that
  completes a flash in any of the cell's pixels adds one).
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
8. Translation invariance: the same region at two offsets gives identical verdict, counts and area (offsets off
   the 4 px grid: item 19).
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
15. A smooth 3 Hz pulse whose left half peaks at byte 120 and right half at byte 255 completes each flash on two
    neighbouring frames and keeps more than 25% of a window on three frames in a row ⇒ PASS, `maxRatePerSec == 3`
    (each flash is one event under the claim rule, ADR-038).
16. A 4 Hz grey 60 ↔ 200 square wave over 1 680 cells (30.9% of a window) with +0/+1 byte of noise per cell per
    frame ⇒ FAIL with `maxRatePerSec == 4` and the same `area_max` as the clean wave (sub-tolerance noise cannot
    move a settled stamp, §5.4, ADR-038).
17. A smooth black ↔ white page pulse at 3 Hz (cosine or quadratic ease-in-out) and at 2.9 Hz, 600 frames sampled
    at `(i + 1/4)/60` and ending part-way through a fall ⇒ PASS, `maxRatePerSec == 3`, at the phases tested (a run
    still moving at the last frame is dropped, §5.4, ADR-042). Counterpart: a 4 Hz square wave whose last black
    state is held for a few frames keeps its last flash (80 frames ⇒ events at 15, 30, 45, 60, 75).
18. A (150,0,0) ↔ black cosine pulse at 4 Hz ⇒ FAIL via the red rule, red `maxRatePerSec == 4`, no general
    violation, exactly like the square wave between the same colours; (150,0,0) square-waved against (1,0,0),
    (2,2,2) or (15,0,0) ⇒ the same verdict and rates as against (0,0,0) (near-black is achromatic, §5.3, ADR-043).
19. 4 Hz rectangles moved 1–3 px off the 4 px grid (offsets (0,0), (1,0), (2,2) and (3,1) from (100,100)) keep
    their verdict: 172×120 px and 340×64 px black ↔ white ⇒ PASS; 88×256 px grey 118 ↔ 150 (ΔL 0.124) ⇒ FAIL. A
    172×120 px rectangle measures exactly 20 640 px (over the 87 040 px window) at every offset (§5.6, ADR-044).

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
  every animation from `document.getAnimations()` (including same-origin frames). Per frame `i`, in this order:
  (1) advance `page.clock` with one `clock.run_for(round(t_i) − round(t_{i−1}))`, whole milliseconds (frame 0 runs
  `round(t_0)`, a quarter-frame, ADR-029), so timers and JS animations advance; the IntersectionObserver callbacks
  that step 4 of the previous frame queued run at the start of this step, after any 0 ms timers the page set
  during that same pass (the clock fires timers due at the same time in the order they were set), as the browser
  runs them in a task after a frame;
  (2) run the page's queued `requestAnimationFrame` callbacks once at that clock time, as a browser does once per
  drawn frame (ADR-029); (3) seek each animation to
  `currentTime = phase + playbackRate × (t_i − startOffset)`, honouring the phase and rate the page gave it
  (ADR-022, ADR-029), and seek each `<video>` like an animation (below), waiting for `seeked`; (4) evaluate every
  IntersectionObserver once, on the layout just seeked (the layout of the picture), as a browser does once per
  rendering update, and queue its callbacks as a 0 ms task on the page clock (ADR-052); (5) screenshot
  (`animations="allow"`). Steps 2 to 4 are one pass in the page; the network is waited for before and after it.
  The clock moves first so that an animation a timer or callback creates in that step is seeked before the
  picture, not one frame later. Animations discovered after frame 0 are treated as starting at the frame in which
  they first appear. Other work the page starts and the browser finishes on the real clock is not waited for
  (§10). A screenshot request may take 10 s of real time; one that times out is made once more at the same
  virtual time, with a note (the clock and double-shot checks still apply), and if that one times out too the
  attempt fails and auto falls through (ADR-053). One capture retries at most 2 pictures in all; the next picture
  that times out fails the attempt the same way, so stalls cost a capture at most 30 s of real time. Animations inside shadow roots (open or closed, recorded by an
  init script that wraps `attachShadow`) are driven the same way; SVG SMIL timelines are paused and set with `setCurrentTime`; an
  animation the page resumes later is re-adopted; `Math.random` is replaced by a fixed-seed generator (ADR-022).
  **`<video>` (ADR-045):** each video is muted, paused and seeked per frame to media time
  `phase + playbackRate × (t_i − start)`, wrapped when it loops and held at its end otherwise. `start` is the
  frame in which the page started it (`autoplay` or `play()`) and `phase` the media time the page gave it. The
  page's `pause()`, `play()`, `currentTime` and `playbackRate` changes are honoured from the frame in which they
  happen; a video the page never started keeps its picture.
  **Checks during the frame loop (ADR-046):** at frame 0, at the first frame of every captured second (every
  `fps`-th frame) and at the last frame, the page clock must read the virtual time, and the screenshot is taken
  twice at the same virtual time, 100 ms of real time apart (**double-shot check**); if the two hashes differ,
  something animates outside the controlled clocks. At **every** frame strategy A also looks again for
  uncontrollable content (the list below): image elements, WebGL and offscreen canvases, cross-origin frames,
  workers, and every image URL the page requested since the previous frame; at those checkpoint frames it also
  walks every CSS property that paints an image. A failed check, or uncontrollable content that appears after
  frame 0, makes auto fall through to the next strategy (the A loop stops at its first failure, since its frames
  would be discarded); a forced A capture becomes `deterministic=false` and lists the content in
  `meta.uncontrollable`. A `<video>` seek or an animation-ready wait that times out also sets
  `deterministic=false` with a note.
* **B `virtual_time`.** CDP `Emulation.setVirtualTimePolicy` with a `1000/fps` ms budget per frame, plus
  `HeadlessExperimental.beginFrame` where the launched Chromium supports it. Page-clock and double-shot checks at
  frame 0 and at the last frame, and with budget-only pictures also at the first frame of every captured second
  (below). The page is loaded under 250 ms budgets and pre-rolled to a fixed 2 000 ms before frame 0 (pausing
  virtual time before navigation never loads a page). Without BeginFrameControl, rendering follows the real clock; a
  once-per-build self-test detects this and auto mode then skips B (ADR-023). That self-test measures only a CSS
  animation, so a budget-only B (no BeginFrameControl) also steps aside, as A does, for uncontrollable content that
  the scan before frame 0 finds; forced, it captures and reports `deterministic=false` with the content in
  `meta.uncontrollable` (ADR-046). At A's checkpoints (frame 0, the first frame of every captured second and the
  last frame) a budget-only B also looks again for uncontrollable content: the full scan, plus every image URL the
  page requested since the previous checkpoint. Content that appears after frame 0 makes auto fall through to C (the
  B loop stops there) and a forced B `deterministic=false`, with the content in `meta.uncontrollable`. With
  BeginFrameControl, B does not look again.
* **C `realtime`.** CDP `Page.startScreencast` with timestamps, resampled to `fps` by holding the latest
  frame at or before each sample time. Chromium's compositor draws on a 60 Hz clock, so the screencast timestamps
  sit on one 1/60 s lattice; the sample grid starts half a compositor tick away from that lattice (the circular
  mean of the timestamps' phases), or a quarter of a sample after the first frame when the timestamps agree on no
  phase (ADR-048). A compositor frame drawn late or dropped can still move a transition by one frame, so a
  realtime rate can be one flash per second off (§10). `deterministic=false`; the report and UI show a warning
  banner; PASS is downgraded to INCONCLUSIVE (§6.4).

**Page loading (every strategy, ADR-047).** Every navigation may take at most 30 s. Strategies A and C wait for
`DOMContentLoaded`, then give the `load` event at most 10 s of real time (a subresource that never answers no
longer holds the capture; a note says that `load` never fired); B loads under its budgets (above). A and C then
wait at most 10 s of real time for web fonts before frame 0 (B, whose page is paused, reads their status). A font
still loading after that is noted, and because Playwright's `page.screenshot` waits for `document.fonts.ready`
before every picture, strategy A then gives up its attempt (auto falls through; a forced A returns no frames, with
the reason). A main document that answers with HTTP status 400 or above raises `PageLoadError` (exported by
`capture`): a capture error (§6.4 rule 1), never retried under another strategy. Each image read over the network
for detection may take 5 s of real time; an image that does not answer counts as `image-unverified`, and image
classifications are reused by the later strategies of the same capture. All image reads of one capture, every
strategy's scan and the later looks together, may take at most 60 s of real time (the detection budget,
`DETECTION_BUDGET_S`); a read gives up when it runs out, and image URLs not read within it count as
`images-unverified` (ADR-046, ADR-049).

Fall-through A → B → C happens when the page contains *uncontrollable* content (ADR-046), recognised from bytes,
never from a file extension:

* animated images: GIF, APNG, animated WebP, and SVG images (`.svg` or gzipped `.svgz`) that contain SMIL
  animation elements or CSS `@keyframes`, wherever the page shows an image (`<img>`, SVG `<image>`,
  `<input type=image>`, `<object>`, `<embed>`, `<video poster>` and every CSS property that paints an image);
* images that could not be checked: unreadable bytes (`image-unverified`), a GIF whose frames or an SVG whose
  markup go on past the first 4 MB (`gif-unverified`, `svg-unverified`), and image URLs beyond the first 200 per
  page, scan and later checks together, or not read within the detection budget (`images-unverified`);
* WebGL canvases and canvases handed to an `OffscreenCanvas`;
* cross-origin iframes;
* Web Workers and SharedWorkers (recorded by an init script that wraps their constructors), whose timers run on
  the real clock.

An `<object>` or `<embed>` that shows a same-origin document is not an image: strategy A drives that document's
animations like the page's own. Fall-through also happens when a check of the frame loop fails (above), when a
strategy cannot load the page under its time source, or when it raises. The reason is recorded in
`meta.fallbackReason`. Uncontrollable content that a strategy given up in auto found, before frame 0 or during its
frame loop, counts as found by the scan before frame 0 of every later strategy of the same capture (a note says
so when that scan did not see it itself), because the page can show such content at moments a later strategy's
frames do not cover (ADR-046).

### 6.3 Determinism test (M2)

Three synthetic pages (CSS keyframes, WAAPI, rAF+JS) captured twice under A and under B must yield identical
`frameHashes` lists.

### 6.4 Verdict escalation (CLI/server layer)

The rules are applied in this order and the first one that matches decides (`cli/report.py` `decide_verdict`,
ADR-008, ADR-039):

1. Capture error, or fewer than 2 frames (the engine cannot run) ⇒ **INCONCLUSIVE**, reason given. A capture
   error includes `PageLoadError`, a page that answered with HTTP status 400 or above (§6.2, ADR-047); a forced
   strategy that gave up before frame 0 returns no frames.
2. Engine FAIL ⇒ **FAIL** regardless of strategy **and of capture length** (what was observed did flash; a
   capture shorter than `2·fps` frames that already shows more than three qualifying flashes in one second
   is a FAIL).
3. Fewer than `2·fps` frames ⇒ **INCONCLUSIVE** (too short for a PASS to mean anything), reason given.
4. Engine PASS with `deterministic=false` ⇒ **INCONCLUSIVE** ("page passed under non-deterministic capture;
   frames may have been missed").
5. Engine PASS with `deterministic=true` ⇒ **PASS**.

Exit codes: 0 PASS, 1 FAIL, 2 error; INCONCLUSIVE exits 1 unless `--no-fail`. Exit 2 is for errors that stop
FlashTrace before a report exists (invalid input, a failure of the tool itself); a capture that fails after the
inputs were accepted, `PageLoadError` included, is an INCONCLUSIVE report that says why. Every invocation's
human-readable output ends with the disclaimer, error exits included (ADR-050).

---

## 7. Attribution and fixes (summary; details in code comments and ADRs)

* Candidates per failing window: animation targets from `getAnimations()` (effect target, name, duration,
  keyframes), `elementsFromPoint` on a 5×5 grid inside the window, and `<video>`, `<canvas>` and `<img>` whose
  bytes are an animated image (`capture/detect.py`, §6.2). Each candidate gets a stable `data-ft-id`, a unique CSS
  selector, bounding rect and animation details. Cross-origin iframes are reported as an "unattributable region".
* Stage 1 (bbox): rank by overlap between the candidate's box and the failing cells of the violation (the cells in
  which at least one pixel completed a flash in the violation's events, ADR-044), as the intersection over union
  of 4 px cell masks; ties go to the smaller box, then to an element that can flash by itself, then to document
  order (ADR-026).
* Stage 2 (ablation): for the top k ≤ 6, a fresh capture of the page with the candidate suppressed **inside the
  capturer**, by its unique selector (the `data-ft-id` tags of the inspection page do not exist in a fresh load):
  the suppressed element's own animations and CSS transitions are held at their frame-0 state, a media element
  (video, canvas, img, svg, iframe) is hidden whole, and an element without an animation of its own (page script
  changes it) has its own paint hidden. Its children are **not** suppressed: they are candidates of their own,
  so a wrapper does not get the credit for what it contains. No ablation CSS is injected or recorded in the
  report (ADR-013, ADR-027, ADR-040). Score (`ablationDelta`) = qualifying general + red events of the baseline
  minus those of the re-capture. Greedy by score, then pruned to a minimal culprit set (ADR-027).
* Which culprits get fixes: every member of ablation's minimal set; otherwise (bbox ranking, or ablation that
  found no set) the culprits whose overlap is ≥ 0.5, keeping those that move by themselves when there are any,
  else only the top-ranked one, at most 3 culprits (ADR-031).
* Fixes (≤ 3 per culprit, in the order rate, luminance, red, area; CSS override strings derived from measured
  values, ADR-030):
  * rate: `animation-duration` so the element flashes at most 2.5 times per second (CSS animations and Web
    Animations with a finite duration; no rate fix for transitions, script, canvas or video);
  * luminance (general violations only), target difference ≤ 0.09: the brighter keyframe colours lowered in
    linear light, keeping the hue; else, for keyframes that switch only opacity between two values, the two
    opacity values moved toward their middle; else a `contrast()` filter (not for an element that is
    see-through in one of its states);
  * red (red violations only): saturated keyframe reds mixed in linear light with the grey of equal luminance
    down to R/(R+G+B) = 0.75; else a `saturate()` filter that takes the measured red state just below 0.8;
  * area: a `transform: scale()` factor reported, never auto-applied.

  Verify = inject the fix as `<style>` (together with one fix per other member when the culprit belongs to a
  minimal set of two or more, because with any member still flashing the page fails), re-capture with the job's
  settings and the same strategy (forced), re-run the engine; "verified" only when the re-run passes under
  deterministic capture (ADR-032).

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
* Animated GIF/WebP/APNG, animated SVG images and Web Workers force non-deterministic capture (strategy C) and a
  PASS becomes INCONCLUSIVE.
* Under realtime capture (strategy C) a measured rate can be one flash per second higher or lower than the page's,
  because a compositor frame drawn late or dropped moves a transition by one frame.
* Under strategy A, work that the page starts and the browser finishes on the real clock is not waited for
  (ADR-052): React scheduler tasks (MessageChannel messages), the page's handling of `fetch` and `XMLHttpRequest`
  answers, history traversals (`history.back()`, `forward()`, `go()`), script, style and image load events,
  `window.postMessage`, WebSocket and EventSource messages, storage callbacks and Navigation API traversals. A page
  that changes what it shows on such an event can show that change one frame earlier or later in another capture
  (several frames when the page also holds the change behind a gate on its own timers), although both captures are
  reported as deterministic.
* Under strategy A, IntersectionObserver is FlashTrace's own implementation (ADR-052). It clips a target by its
  ancestors' overflow along an approximated containing-block chain and ignores transforms, `clip-path`, `clip`,
  `border-radius`, `overflow-clip-margin` and `scrollMargin`, so a reveal that depends on those can start at a
  different scroll position than in a browser; `isVisible` (`trackVisibility`) equals `isIntersecting`, without an
  occlusion test.
* The fine-pattern exception (T6) and spatial-pattern hazards are not evaluated.
* Only the captured viewport and duration are analysed; content revealed by scrolling or interaction is
  analysed only if an interaction script drives it.
* Window size for viewports other than 1024 px wide is scaled by the width ratio (an assumption).
* The 341×256 px window is scored in whole 4×4 px cells and slides in 4 px steps, so a flashing area wider or
  taller than the window can be under-measured by up to 3 px along such an edge.
* No numbers in this repository are estimates: accuracy, agreement and runtime figures exist only in
  `docs/RESULTS.md`, produced by `flashtrace bench`, with date and commit hash.
