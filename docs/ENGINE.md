# FlashTrace engine, explained step by step

*Analysis aid, not a compliance certification.* A PASS means "no WCAG 2.3.1 violation detected under the
tested conditions". This page explains what the code in `engine/` does, in order, for a reader who has never
used NumPy. `docs/SPEC.md` is the normative definition; where this page and the spec disagree, the spec wins.

## The question the engine answers

WCAG 2.2 Success Criterion 2.3.1 says a page must not contain "anything that flashes more than three times
in any one second period, or the flash is below the general flash and red flash thresholds". The engine
receives a sequence of still frames (screenshots taken at a known rate, normally 60 per second of animation
time) and answers: *in any one-second window, did more than three qualifying flashes happen?* A flash
"qualifies" when it is bright enough (or red enough) **and** large enough. Everything below is in service of
that single question.

## The pipeline at a glance

```
frame (bytes)  ->  linear light  ->  4x4 px cell averages  ->  per-cell signals (L, redness, u'v')
   -> peaks & valleys per cell  ->  transitions (big enough changes)  ->  flashes (opposing pairs)
   -> per-frame "which cells flashed" masks  ->  biggest 341x256 window share  ->  qualifying events
   -> sliding one-second counts  ->  verdict, violations, advisories, timeline, heatmap
```

Frames are processed one at a time (streaming). Between frames the engine only remembers a few numbers per
cell, so a 10-second capture never has to sit in memory at once. Every number is a 64-bit float or integer
and every step runs in a fixed order, so the same frames always produce byte-identical output.

## `engine/constants.py` - every threshold, with its source

**In:** nothing. **Out:** named numbers.

Every number the engine compares against lives here, next to a comment quoting the W3C sentence it comes
from, with the boundary spelled out (for example "10% or more" is `>=`, "below 0.80" is `<`, "more than
three" means 4 fails). No other file is allowed to write a threshold as a bare number.

## `engine/color.py` - from screen bytes to light

**In:** red/green/blue bytes 0-255. **Out:** relative luminance, chromaticity, redness.

Screens store colour with a curve ("gamma"): byte 128 is not half as bright as byte 255. WCAG's thresholds
are about physical light, so each channel is first converted with WCAG's own formula:

```
C = byte / 255
if C <= 0.04045:  linear = C / 12.92
else:             linear = ((C + 0.055) / 1.055) ^ 2.4
L = 0.2126 * R_linear + 0.7152 * G_linear + 0.0722 * B_linear
```

Because a byte can only take 256 values, the formula is evaluated once per value into a lookup table
(`SRGB8_TO_LINEAR`); converting a frame is then just "look each byte up", which is fast and guarantees equal
bytes always give the exact same float.

**Worked example, the grey `#808080` (128, 128, 128):** 128 / 255 = 0.50196. That is above 0.04045, so
linear = ((0.50196 + 0.055) / 1.055) ^ 2.4 = 0.52793 ^ 2.4 = 0.21586. All three channels are the same, so
L = (0.2126 + 0.7152 + 0.0722) x 0.21586 = **0.2159**. Mid-grey is only about a fifth as bright as white.

For the red rule the module also computes, per colour:

* the **redness ratio** R / (R + G + B) on the sRGB-encoded (byte-scale) components. Pure red is 1.0,
  white is 0.333, black is defined as 0 (ADR-003);
* the **CIE 1976 (u', v') chromaticity**, "which hue, ignoring brightness", via the XYZ matrix printed in
  the Understanding document. Pure black would divide 0 by 0, so it is given the white point (0.1978,
  0.4683): black counts as colourless, like white and grey (ADR-011). Example: sRGB red has
  (u', v') = (0.4508, 0.5229); its distance to the white point is 0.259, which is more than the 0.2 that
  WCAG requires, so red <-> black, red <-> white and red <-> grey all count as a colour change.

## `engine/cells.py` - 4x4 pixel cells

**In:** one frame, uint8 [height, width, 3]. **Out:** mean linear colour per cell, and the five signals.

A 1024x768 frame has 786 432 pixels; tracking each one over time would be slow and would not match how the
criterion is written (it is about regions, not pixels). The frame is cut into 4x4 pixel cells and each cell
becomes the *average linear colour* of its 16 pixels: 256 x 192 = 49 152 cells. Averaging happens after
linearising (ADR-002) because luminance is linear in light: a cell's L is then exactly the mean of its
pixels' L. Pixels beyond the last whole cell (at most 3 per edge) are dropped.

From each cell's mean colour the module reads the five per-cell signals: `L`, `ratio`, `u`, `v` and the
legacy red value `rv` (used only in the comparison mode). It also sizes the WCAG window: 341x256 CSS px at a
1024 px wide viewport is 85 x 64 cells (341 / 4 = 85.25 rounds to 85); other widths scale proportionally
(ADR-007).

## `engine/transitions.py` - peaks, valleys, transitions, flashes

**In:** one frame's signals for all cells, frame after frame. **Out:** "cell c completed a flash at frame f".

WCAG defines a *transition* as "the change ... between adjacent peaks and valleys in a plot of ...
measurement against time" and says "A flash consists of two opposing transitions". So per cell the engine
needs the turning points of its signal over time.

`RunTracker` keeps three facts per cell: where the current *run* started (the previous turning point), the
direction it is going (up, down, or not moved yet), and the most extreme value seen so far in this run. On
each new frame:

* if the signal pushes further in the run's direction, that frame becomes the new extremum;
* if it turns back by more than a tiny tolerance (0.005 in luminance, one twentieth of the threshold), the
  run has ended at the extremum. The rule for that signal is asked whether start -> extremum was a
  transition; the next run begins at the extremum;
* a flat stretch or a wobble smaller than the tolerance changes nothing.

Rules (each comparison in the code sits beside the W3C sentence it implements):

* **general:** |L_end - L_start| >= 0.10 **and** the darker of the two < 0.80;
* **red (WCAG 2.2):** at least one end has redness ratio >= 0.8 **and** the (u', v') distance between the
  ends is > 0.2;
* **red (legacy comparison mode):** at least one end has ratio >= 0.8 **and** the red value changes by > 20.

A transition is stamped with the frame where the extremum was first reached. `FlashPairer` then pairs each
cell's transitions in order, first with second, third with fourth; a flash is counted at the frame of its
second (completing) transition (ADR-005). The engine does all of this for all 49 152 cells at once with a
handful of array operations per frame rather than a loop over cells.

**Worked example, a 4 Hz black/white square wave at 60 fps.** Four cycles per second means the screen
changes state every 7.5 frames: black for frames 0-7, white 8-14, black 15-22, white 23-29, black 30-37,
and so on. L jumps between 0.0 and 1.0, so every rise and fall is a transition (change 1.0 >= 0.10, darker
state 0.0 < 0.80). Rises land on frames 8, 23, 38, 53, ...; falls on 15, 30, 45, 60, .... Pairing rise 8
with fall 15 completes a flash at frame 15; the next at 30, then 45, 60, 75. Four flashes in sixty frames.

## `engine/area.py` - is the flash big enough?

**In:** per-frame masks "cell completed a flash at t". **Out:** per frame, the largest window share and
whether a qualifying event is counted at that frame.

WCAG lets small flashes through: content passes when the flashing area "occupies no more than ... 25% of
any 10 degree visual field", and a "341 x 256 pixel rectangle anywhere on the displayed screen area"
stands in for that field. "Anywhere" means every position of that rectangle must be tried. The engine
does this with an *integral image*: a running total so that "how many flashing cells are in this window?"
is answered with four look-ups for every possible position. The best position is recorded (ties go to the
top-left-most). Steps per frame (SPEC 5.6):

1. cells completing a flash one frame apart are treated as one event (`E[t] = M[t] or M[t+1]`);
2. `area_max[t]` = flashing cells in the best window / cells in a window (85 x 64 = 5 440);
3. the event qualifies if `area_max > 0.25` (strictly greater: exactly 25 % passes);
4. an event that qualifies on several consecutive frames is counted once, at the first of them.

**Example continued.** Every cell flashes at frame 15, so every window is 100 % full: the event qualifies.
Because of step 1 it already qualifies on frame 14 (the frame before the completing transition), so it is
counted at 14. Qualifying general events: frames 14, 29, 44, 59, 74, ...

## `engine/rate.py` - more than three in any second?

**In:** the list of qualifying event frames. **Out:** counts, violations, advisories, timeline.

"Any one second period" is a sliding window of `fps` frames. Using a cumulative total, the number of events
in any window is one subtraction, so every possible window start is checked.

* `window_counts`: events in the second starting at each frame; the maximum is the reported rate.
* `find_violations`: a window fails when its count is **> 3**; consecutive failing windows are merged into
  one violation spanning from the first failing start to the last failing window's end.
* `trailing_counts`: events in the second *ending* at each frame (shown per frame in the UI).
* `find_advisories`: a span longer than 5 s in which the trailing count is >= 2 at every frame. This is
  **not** a WCAG rule (ADR-009); it is reported with the label "advisory - not a WCAG 2.3.1 criterion" and
  never changes the verdict.
* `timeline`: events per second, for the chart. Bin `k` covers frames `[k*fps, (k+1)*fps)`; a trailing
  partial second gets its own bin, so every qualifying event appears in exactly one bin and the bins always
  sum to the totals.

**Example finished.** The window of frames 0-59 contains the events at 14, 29, 44 and 59: four, which is
more than three, so the capture **FAILs** with `generalMaxRatePerSec = 4`. The same wave at 3 Hz changes
state every 10 frames, completes flashes at 20, 40, 60, 80, ... and never puts more than three in any
sixty-frame window: **PASS**, reported as "no 2.3.1 violation detected under the tested conditions".

## `engine/types.py` - the result and its JSON

**In:** the numbers above. **Out:** `AnalysisResult` and `to_dict()`.

`AnalysisResult` carries the verdict (`FAIL` if any violation, else `PASS`; a property), a one-sentence
reason (the `reason()` method; a PASS reads "No 2.3.1 violation detected under the tested conditions."), the
violations (each with the CSS-pixel rectangle of the largest event's window and the event frames inside
it), advisories, the per-second timeline, per-frame statistics, and a heatmap counting completed flashes per
cell (stored as base64 little-endian 16-bit integers so it is compact and reproducible). Key names match
`engine/schema/report.schema.json` and are always written in the same order.

Two sizes appear in the output and they differ by design: `windowPx` is the nominal WCAG window (341 x 256
CSS px at a 1024 px wide viewport), while each violation's `window` rectangle is the window that was actually
scored, in whole cells (85 x 64 cells = 340 x 256 px). ADR-006 records this 0.3 % difference.

## `engine/analyze.py` - putting it together

`analyze(frames, fps, viewport, red_rule)` runs the streaming pass (colour -> cells -> signals -> trackers
-> pairers -> masks and heatmap), then the area and rate passes, and assembles the result. It accepts a
whole array or a generator of frames, rejects anything that is not uint8 [H, W, 3], fewer than two frames
or an unknown red rule, and reports progress through an optional callback. `evidence_frame_indices` picks a
small, deterministic set of frames to save as still images: frame 0 and, per violation, its first event
frame and the one before it.

## Determinism and speed

Same input, same output, every time: float64 throughout, no randomness, no threads, fixed operation order,
and a lookup table instead of per-pixel arithmetic. The acceptance tests (SPEC 5.10 item 9) analyse the
same scene twice and compare the JSON byte for byte. Runtime figures are deliberately not quoted on this
page: SPEC section 10 reserves them for `docs/RESULTS.md`, which `flashtrace bench` produces with a date and
commit hash. The cost per frame is dominated by the 4x4 cell averaging and the tracker's array operations,
both of which are O(cells) and independent of what the page shows.

## What the engine deliberately does not do

* It does not evaluate the "fine, balanced, pattern" exception (white noise, tiny checkerboards); 4x4
  averaging already smooths such patterns towards grey, which makes the engine lenient, not strict, there.
* It does not model a physical display, viewing distance or HDR; it measures what headless Chromium drew.
* It does not sum general and red flashes together; WCAG counts them separately and so does the engine.
* It never says "WCAG compliant". It reports what it measured, under the conditions it was given.
