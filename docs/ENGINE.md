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
   -> peaks & valleys per cell  ->  transitions (big enough changes, judged pixel by pixel)
   -> flashes (opposing pairs, per pixel)  ->  per-frame "how many pixels of each cell flashed" masks
   -> biggest 341x256 window share  ->  qualifying events
   -> sliding one-second counts  ->  verdict, violations, advisories, timeline, heatmap
```

Frames are processed one at a time (streaming): a frame is dropped as soon as its cell signals have been read,
so the frames of a 10-second capture never sit in memory together. What the engine does keep is, for the
whole capture, one small count per cell per frame for each flash type ("how many of this cell's pixels
completed a flash at this frame?", needed by the area step after the last frame), plus a few numbers per cell
and the cell's pixels at the two ends of its current run for the run trackers.
Every number is a 64-bit float or integer and every step runs in a fixed order, so the same frames always
produce byte-identical output.

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
(`SRGB8_TO_LINEAR`); converting a frame is then just "look each byte up", which runs the power function 256
times instead of once per byte and guarantees equal bytes always give the exact same float.

**Worked example, the grey `#808080` (128, 128, 128):** 128 / 255 = 0.50196. That is above 0.04045, so
linear = ((0.50196 + 0.055) / 1.055) ^ 2.4 = 0.52793 ^ 2.4 = 0.21586. All three channels are the same, so
L = (0.2126 + 0.7152 + 0.0722) x 0.21586 = **0.2159**. Mid-grey is only about a fifth as bright as white.

For the red rule the module also computes, per colour:

* the **redness ratio** R / (R + G + B). The colour is first converted back to sRGB and **rounded to whole
  0-255 values** (R8, G8, B8), because the brief defines the ratio on 0-255 components and because the
  round trip through linear light is not exact in floating point: without the rounding, byte 12 comes back
  as 11.999999999999998, and (240, 30, 30), whose ratio is exactly 240 / 300 = 0.8, measured
  0.7999999999999999, just under the inclusive ">= 0.8" (ADR-003, ADR-020). Pure red is 1.0, white is 1/3, (204, 51, 0) is exactly 0.8. The
  ratio is 0 for black (R8 + G8 + B8 = 0) and for any colour with **R8 below 16**: a red that dark has no
  visible hue, and without this floor an invisible one-step change rgb(0,0,0) <-> rgb(1,0,0) would count as
  a red flash. 16 is the smallest red that the pre-2022 rule ((R - G - B) x 320 > 20) could ever count
  against black (ADR-020);
* the **CIE 1976 (u', v') chromaticity**, "which hue, ignoring brightness", via the XYZ matrix printed in
  the Understanding document. Pure black would divide 0 by 0, so it is given the white point, which the code
  computes from the same matrix (about (0.1978, 0.4683)), so black and white get exactly the same numbers:
  black counts as colourless, like white and grey (ADR-011). Every **near-black** colour, one whose three
  whole values are all below 16 such as (6, 0, 0) or (15, 15, 15), gets the white point too: it looks black,
  and the same floor already says its red shows no hue (ADR-043). Without this, a red fading
  smoothly to black would keep red chromaticity on its near-black frames, where its ratio is already 0, and
  the fade would measure as no colour change at all, while the same two colours as a square wave fail.
  Example: sRGB red has (u', v') = (0.4508,
  0.5229); its distance to the white point is 0.259, which is more than the 0.2 that WCAG requires, so
  red <-> black, red <-> white and red <-> grey all count as a colour change. (204, 51, 0) is 0.214 from the
  white point, so it too makes a red transition against black, white or grey. One consequence of a hard
  floor: (16, 0, 0) <-> (15, 0, 0) is a red transition, exactly as (16, 0, 0) <-> black already was;
* the **legacy red value** (comparison mode only): max(0, R8 - G8 - B8) / 255 x 320, on the same whole
  values.

## `engine/cells.py` - 4x4 pixel cells

**In:** one frame, uint8 [height, width, 3]. **Out:** mean linear colour per cell, and the five signals.

A 1024x768 frame has 786 432 pixels; tracking each one over time would mean sixteen times as many numbers
and would not match how the criterion is written (it is about regions, not pixels). The frame is cut into 4x4 pixel cells and each cell
becomes the *average linear colour* of its 16 pixels: 256 x 192 = 49 152 cells. Averaging happens after
linearising (ADR-002) because luminance is linear in light: a cell's L is then exactly the mean of its
pixels' L. Pixels beyond the last whole cell (at most 3 per edge) are dropped.

From each cell's mean colour the module reads the five per-cell signals: `L`, `ratio`, `u`, `v` and the
legacy red value `rv` (used only in the comparison mode). These cell signals decide *when* a cell's runs
start and end (next section). Whether a run is a transition is then asked pixel by pixel, so the module also
hands over each cell's 16 pixels as they are (`pixel_blocks`) and computes the same signals for single
pixels (`measure_general`, `measure_red_wcag22`, `measure_red_legacy`). Averaging alone would get the area
wrong for anything that does not line up with the 4 px grid: a black/white element covering only 2 of a
cell's 16 pixels still changes the cell mean by 0.125 and would count the whole cell, while a low-contrast
element (L change 0.124) covering half a cell changes the mean by only 0.062 and would count none of it
(ADR-044). It also sizes the WCAG window: 341x256 CSS px at a
1024 px wide viewport is 85 x 64 cells (341 / 4 = 85.25 rounds to 85); other widths scale proportionally
(ADR-007), and halves round up (64.5 becomes 65). Two sizes come out of this: `window_cells_full` is the
whole window (85 x 64 = 5 440 cells at 1024 px wide), and `window_cells` is that window clamped to the grid,
which is what gets slid over the frame. They differ only when the viewport is smaller than the window, for
example a 1024 x 100 banner (25 cell rows), where the scanned window is 85 x 25 cells.

## `engine/transitions.py` - peaks, valleys, transitions, flashes

**In:** one frame's signals for all cells, frame after frame. **Out:** "cell c completed a flash at frame f".

WCAG defines a *transition* as "the change ... between adjacent peaks and valleys in a plot of ...
measurement against time" and says "A flash consists of two opposing transitions". So per cell the engine
needs the turning points of its signal over time.

`RunTracker` keeps a few facts per cell: where the current *run* started (the previous turning point), the
direction it is going (up, down, or not moved yet), the most extreme value seen so far in this run, and the
frame the run will be stamped with (plus whether the signal has settled there). On each new frame:

* if the signal pushes further in the run's direction, that frame becomes the new extremum;
* if it turns back by more than a tiny tolerance (0.005 in luminance and in the redness ratio, 1.0 in the
  legacy red value; one twentieth of the threshold), the run has ended at the extremum. The rule for that
  signal is asked whether start -> extremum was a transition; the next run begins at the extremum (ADR-018);
* a flat stretch, or a wobble no larger than the tolerance, changes nothing. A wobble that small could never
  be a transition itself; ignoring it stops anti-aliasing noise from cutting a slow fade into pieces.

The tracker also keeps each cell's 16 pixels as they were at the start of the run and at its extremum. When
the run ends, the rule is applied to **each pixel's** two end colours, and the cell makes a transition if at
least one pixel does; the transition records which pixels those are (ADR-044). Most cells are one
flat colour at both ends, so the rule is asked once for them. A balanced fine pattern (half the pixels go
up while the other half go down) leaves the cell mean still, starts no run and is not counted, as before.

After the last frame, a run that is still open is closed only if its signal has **arrived** (stood still or
wobbled back since its stamp). A run still moving at the last frame, such as a fade cut off part-way or a
step shown by the last frame alone, has not reached its extremum inside the capture, so it is dropped: its
real end is after the capture. Stamping it at the last frame would put the flash up to a few frames before it
happens, and a smooth 3 Hz pulse could then count 4 in the last second (ADR-042).

Rules (each comparison in the code sits beside the W3C sentence it implements):

* **general:** |L_end - L_start| >= 0.10 **and** the darker of the two < 0.80;
* **red (WCAG 2.2):** at least one end has redness ratio >= 0.8 **and** the (u', v') distance between the
  ends is > 0.2;
* **red (legacy comparison mode):** at least one end has ratio >= 0.8 **and** the red value changes by > 20.

A transition is stamped with the frame where the signal **arrived** at its extremum, i.e. the first frame
showing the new state, not the last frame of a plateau (ADR-018). While the signal keeps moving, the stamp
follows each new extremum, so a smooth fade is stamped at the bottom of its valley. Once the signal stands
still or wobbles back, it has arrived: from then on a further gain moves the stamp only if it goes more than
the tolerance beyond the stamped value. Without that, noise of a fraction of a byte on a flat colour would move
the stamp to wherever the noise happened to peak, and one flash would be spread over several frames (ADR-038).
`FlashPairer` then pairs each pixel's transitions in
order, first with second, third with fourth, and only a rise with a fall; a flash is counted at the frame of
its second (completing) transition (ADR-005), and a cell reports how many of its pixels completed one. Two
rises in a row can happen when a change too small to be a
transition sits between them (rise, small dip, rise); the newer rise then replaces the waiting one, so a
flash is always one rise and one fall (ADR-018). The engine does all of this for all 49 152 cells at once
with a handful of array operations per frame rather than a loop over cells. (`detect_transitions_1d` and
`flash_frames_1d` run the same tracker on a single cell; they exist for the tests and for teaching.)

**Worked example, a 4 Hz black/white square wave at 60 fps.** Four cycles per second means the screen
changes state every 7.5 frames: black for frames 0-7, white 8-14, black 15-22, white 23-29, black 30-37,
and so on. L jumps between 0.0 and 1.0, so every rise and fall is a transition (change 1.0 >= 0.10, darker
state 0.0 < 0.80). Rises land on frames 8, 23, 38, 53, ...; falls on 15, 30, 45, 60, .... Pairing rise 8
with fall 15 completes a flash at frame 15; the next at 30, then 45, 60, 75. Four flashes in sixty frames.

## `engine/area.py` - is the flash big enough?

**In:** per-frame masks "how many pixels of cell c completed a flash at t" (0 to 16). **Out:** per frame,
the largest window share and whether a qualifying event is counted at that frame.

WCAG lets small flashes through: content passes when the flashing area "occupies no more than ... 25% of
any 10 degree visual field", and a "341 x 256 pixel rectangle anywhere on the displayed screen area"
stands in for that field. "Anywhere" means every position of that rectangle must be tried. The engine
does this with an *integral image*: a running total so that "how many flashing pixels are in this window?"
is answered with four look-ups for every possible position. The best position is recorded (ties go to the
top-left-most). Counting pixels, not whole cells, makes the share the same wherever an element sits: a
172 x 120 px rectangle measures its 20 640 pixels at (100, 100) and at (102, 102) alike (ADR-044).
Windows still slide in whole cells, so an element wider or taller than the window can lose at most 3 px
along such an edge (the lenient direction). Steps per frame (SPEC 5.6, ADR-019, ADR-038):

1. cells completing a flash one frame apart are treated as one event, looking backwards:
   `E[t] = M[t-1] or M[t]` (frame 0 has nothing before it). In pixel counts the union is the sum, capped at
   16: a pixel cannot complete two flashes one frame apart, so the two frames' pixels are different ones;
2. `area_max[t]` = flashing pixels in the best window / pixels in the **full** window (85 x 64 = 5 440 cells,
   87 040 px, at 1024 px wide). On a viewport too short for the window, only the part that fits is slid over the frame,
   but the share is still taken of 5 440 cells, because WCAG's 25 % is an absolute area (21 824 px), not a
   quarter of whatever happens to be on screen;
3. the frame qualifies if `area_max > 0.25` (strictly greater: exactly 25 % passes), which at 1024 px wide means
   more than 21 760 flashing pixels in one window position;
4. counting: an event counted at frame `t` *claims* the completions of frames `t` and `t+1` (step 1's
   tolerance). Going forward through the frames, a qualifying frame that is not claimed is scored again
   using only the completions of `t-1` and `t` that no earlier event has claimed; if that share is still over
   25 %, an event is counted at `t`. So one flash is counted once even when its cells finish on two
   neighbouring frames (a smooth fade over dim and bright cells does this: they reach their extreme byte one
   frame apart). The price: two separate regions that qualify one frame apart are also counted once, while
   two frames apart they count twice (ADR-038 records this trade-off).

**Example continued.** Every cell completes a flash at frame 15, so every window is 100 % full on frames 15
and 16 (step 1 carries frame 15 into frame 16). Frame 15 counts an event, which claims frames 15 and 16, so
frame 16 is not counted again. The event sits at frame 15, the frame that shows the new (black) state.
Qualifying general events: frames 15, 30, 45, 60, 75.

**A faster example.** A 30 Hz strobe at 60 fps changes colour on every frame, so every cell completes a flash
every 2 frames (2, 4, 6, ...). The event at 2 claims frames 2 and 3; frame 4 brings new, unclaimed
completions, so it is the next event, and so on: 30 events in every second. Frequencies above half the frame
rate cannot be seen in the frames at all.

**A split flash.** A 3 Hz smooth pulse whose left half peaks at byte 120 and right half at byte 255
completes the left half on one frame and the right half on the next. Three frames in a row are over 25 %
(the pair of completion frames and the one after), but the event on the first frame claims both completion
frames, so each flash counts once: 3 per second, a PASS.

**A short page.** On a 1024 x 100 viewport (25 cell rows) a flashing block of 24 x 25 = 600 cells covers
600 / 5 440 = 11 % of the window: it does not qualify, even though it fills 28 % of the 85 x 25 cells that fit
on screen.

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
  partial second gets its own bin with its raw count (ADR-021), so every qualifying event appears in exactly
  one bin and the bins always sum to the totals.

**Example finished.** The window of frames 0-59 contains the events at 15, 30 and 45: three. The window of
frames 1-60 contains 15, 30, 45 and 60: four, which is more than three, so the capture **FAILs** with
`generalMaxRatePerSec = 4`. In a 90-frame (1.5 s) capture the failing window starts run from 1 to 30, so the
violation spans frames 1-89 and lists the events 15, 30, 45, 60, 75; the timeline has two bins, 3 events in
second 0 (frames 0-59) and 2 in the partial second 1 (frames 60-89). The same wave at 3 Hz changes state every
10 frames, completes flashes at 20, 40, 60, 80, ... and never puts more than three in any sixty-frame window:
**PASS**, reported as "no 2.3.1 violation detected under the tested conditions".

## `engine/types.py` - the result and its JSON

**In:** the numbers above. **Out:** `AnalysisResult` and `to_dict()`.

`AnalysisResult` carries the verdict (`FAIL` if any violation, else `PASS`; a property), a one-sentence
reason (the `reason()` method; a PASS reads "No 2.3.1 violation detected under the tested conditions."), the
violations (each with the CSS-pixel rectangle of the largest event's window and the event frames inside
it), advisories, the per-second timeline, per-frame statistics, and a heatmap counting completed flashes per
cell (stored as base64 little-endian 16-bit integers so it is compact and reproducible). Key names match
`engine/schema/report.schema.json` and are always written in the same order. In the per-frame statistics,
`generalCells` / `redCells` count the cells with at least one pixel completing a flash at that frame, and
`generalAreaMax` / `redAreaMax` are the best window's share measured in pixels (SPEC 5.8, ADR-044).

Two sizes appear in the output and they differ by design: `windowPx` is the nominal WCAG window (341 x 256
CSS px at a 1024 px wide viewport), while each violation's `window` rectangle is the window that was actually
scored, in whole cells (85 x 64 cells = 340 x 256 px). ADR-006 records this 0.3 % difference. On a viewport
shorter than the window the rectangle is clipped to the viewport (85 x 25 cells = 340 x 100 px on a
1024 x 100 page), while the area share is still measured against the full window (ADR-019).

## `engine/analyze.py` - putting it together

`analyze(frames, fps, viewport, red_rule)` reads as the pipeline stages, each a small named helper:
`_check_arguments` (red rule and fps), `_resolve_viewport` (the viewport, or the frame size when none is
given), `_geometry` (cell grid, scanned window and full window), `_stream` (colour -> cells -> signals ->
trackers -> pairers -> masks and heatmap, one frame at a time), `_score_type` (area, rate, violations and
advisories for one flash type) and `_assemble` (the `AnalysisResult`). It accepts a whole array or a
generator of frames and rejects anything that is not uint8 [H, W, 3], fewer than two frames, an unknown red
rule, or a frame width that differs from the viewport width (frames must be at device scale factor 1, SPEC
5.1). It reports progress through an optional callback. `evidence_frame_indices` picks a small,
deterministic set of frames to save as still images: frame 0 and, per violation, its first event frame and
the one before it. Because an event is counted on the frame where its flash completes, the two show the
state just before and just after the change (frames 14 and 15 in the 4 Hz example: white, then black).

## Determinism and speed

Same input, same output, every time: float64 throughout, no randomness, no threads, fixed operation order,
and a lookup table instead of per-pixel arithmetic. The acceptance tests (SPEC 5.10 item 9) analyse the
same scene twice and compare the JSON byte for byte. Runtime figures are deliberately not quoted on this
page: SPEC section 10 reserves them for `docs/RESULTS.md`, which `flashtrace bench` produces with a date and
commit hash. The 4x4 cell averaging and the tracker's array operations do a fixed amount of work per cell on
every frame; the pixel-by-pixel rule adds work only where runs end, so a page that changes everywhere on every
frame (a full-screen strobe) costs the most.

## What the engine deliberately does not do

* It does not evaluate the "fine, balanced, pattern" exception (white noise, tiny checkerboards). A balanced
  pattern keeps each 4x4 cell's mean still, so no run starts and nothing is counted, which makes the engine
  lenient, not strict, there; an unbalanced fine pattern moves the cell mean and its pixels are judged.
* It does not model a physical display, viewing distance or HDR; it measures what headless Chromium drew.
* It does not sum general and red flashes together; WCAG counts them separately and so does the engine.
* It cannot see flashing faster than half the frame rate (30 Hz at 60 fps): such content is not in the frames.
* It never claims compliance. It reports what it measured, under the conditions it was given.
