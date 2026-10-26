# Architecture Decision Records

Short records of every non-trivial choice. Format: context, decision, consequences. Newest at the bottom.
All dates are absolute.

## ADR-001 — Red-flash rule: WCAG 2.2 CIE 1976 UCS definition, legacy rule as comparison mode (2026-10-24)

**Context.** The build brief's working value for a red transition was "R/(R+G+B) ≥ 0.8 and a change in
(R−G−B)×320 greater than 20". The brief also says to correct working values if the W3C text differs. The
W3C text differs: WCAG 2.2 (W3C Recommendation 12 December 2024) Note 3 of *general flash and red flash
thresholds* defines a pair of opposing transitions involving a saturated red as one where "one transition is
either to or from a state with a value R/(R + G + B) that is greater than or equal to 0.8, and the difference
between states is more than 0.2 (unitless) in the CIE 1976 UCS chromaticity diagram [ISO 9241-391]". The
republished WCAG 2.1 (06 May 2025) prints the same note ("working definition (as of 2022)"). The
(R−G−B)×320 rule is the pre-2022 working definition still implemented by PEAT and by EA IRIS.

**Decision.** Verdicts use the WCAG 2.2 UCS rule (`red_rule="wcag22"`, default). The legacy rule is
implemented behind `red_rule="wcag21-legacy"` only so results can be compared with IRIS/PEAT; the report
records which rule produced it.

**Consequences.** Red ↔ dark-red pulsing (same chromaticity) is a *general* flash only under wcag22 (if
ΔL ≥ 0.10) but a *red* flash under the legacy rule; test 10 in SPEC §5.10 pins this difference. The
chromaticity of pure black is undefined; see ADR-011.

## ADR-002 — 4×4 px cells averaged in linear light; remainder pixels dropped (2026-10-24)

**Context.** The brief asks for 4×4 px cells. Averaging can be done on sRGB-encoded or linear values.
**Decision.** Linearise each pixel first (WCAG relative-luminance formula), then average the 16 linear RGB
values. Luminance is linear in light, so the cell's L equals the mean of its pixels' L, which is what the
threshold is about. Pixels beyond the last full cell are dropped (at most 3 px per edge); the standard
viewports (1024×768, 1280×720, 1366×768, 1920×1080, 375×812 → 375 drops 3 px) are documented in the
report's assumptions when cropping happens.
**Consequences.** Fine checkerboards average toward grey (lenient, see SPEC T6). Exactly reproducible.
*Amended 2026-10-25 (ADR-044):* the cell average still decides when a cell's runs start and end, but whether a run
is a transition is now decided for each of the cell's 16 pixels, and areas are counted in pixels.

## ADR-003 — Redness ratio on 0–255 sRGB-encoded components (2026-10-24)

**Context.** WCAG does not state the scale for R/(R+G+B). The brief specifies 0–255 sRGB components. IRIS
computes it on linearised values (which flags more colours as saturated red).
**Decision.** Follow the brief: compute the ratio on the cell colour re-encoded to sRGB (0–255 scale;
scale-invariant so 0–1 is identical). Black (sum 0) has ratio 0.
**Consequences.** Diverges from IRIS for orange-ish reds; identical for the pure red / black / white / grey
colours used in tests and the benchmark. Recorded in the report assumptions.

## ADR-004 — Transitions are monotone runs between adjacent extrema, timestamped at the run's end (2026-10-24)

**Context.** WCAG Note 2 defines a transition as the change between adjacent peaks and valleys of the
signal plotted against time.
**Decision.** Per cell and signal, split the time series into monotone runs (plateaus do not end a run; a
reversal does). Evaluate the threshold on the run's end states. A transition is timestamped at the frame
where the run reaches its extremum. A run still open at the last frame is evaluated at the last frame.
**Consequences.** Square waves and ramps are both handled; a ramp counts once when it ends. Tiny
anti-aliasing wobbles could split a slow ramp into sub-threshold runs; headless rendering is deterministic
and the benchmark measures whether this matters in practice (reported, not assumed).
*Amended 2026-10-25 (ADR-042):* a run still open at the last frame is evaluated only if its signal has settled;
a run still moving there is dropped.

## ADR-005 — Flash = consecutive non-overlapping pair of opposing transitions; time = second transition (2026-10-24)

**Decision.** Pair a cell's transitions in order (1st+2nd, 3rd+4th, …). The flash happens when its second
transition completes.
**Consequences.** A square wave at f Hz gives f flashes per second, matching the "more than 6 transitions
per second" convention of PEAT/IRIS. Overlapping pairing would nearly double the count and was rejected.

## ADR-006 — Qualifying events: 2-frame union, integral-image window scoring, strict > 25%, leading edge (2026-10-24)

**Decision.** `E[t] = M[t] ∪ M[t+1]`; score every window position with a 2-D integral image; an event
qualifies when `max window fraction > 0.25`; count it at the first frame it qualifies (`Q[t] ∧ ¬Q[t−1]`).
The window in cells is 85×64 (341/4 = 85.25 → 85; 256/4 = 64), i.e. 87 040 px versus 87 296 px (0.3%
smaller; the qualifying threshold therefore sits at 1 360 cells = 21 760 px instead of 21 824 px).
**Consequences.** Translation invariant; O(cells) per frame; two independent regions qualifying on adjacent
frames are counted once (16 ms apart). The benchmark tags ±5% boundary cases and reports them separately.
*Amended 2026-10-25 (ADR-044):* "translation invariant" held only for regions aligned with the 4 px grid. The masks
now count flashing pixels per cell, the union is `min(16, M[t−1] + M[t])`, and the threshold in pixels is more than
21 760 px of the 87 040 px scored window. Step 1 and step 4 were replaced earlier (ADR-019, ADR-038).

## ADR-007 — Other viewport widths scale the window by the width ratio (2026-10-24)

**Decision.** For a viewport W px wide, the window is `round(341·W/1024) × round(256·W/1024)` px, clamped to
the viewport. Recorded as an assumption in every report whose width ≠ 1024.
**Consequences.** Keeps "25% of a 10° field" roughly proportional on wider screens; it is an approximation
(the Understanding document notes CSS px sizes are already density independent).

## ADR-008 — Verdicts, INCONCLUSIVE and exit codes (2026-10-24)

**Decision.** Engine FAIL ⇒ FAIL under any strategy (an observed flash is real). Engine PASS ⇒ PASS only
under deterministic capture; otherwise INCONCLUSIVE (frames may have been missed). Capture error or fewer
than 2·fps frames ⇒ INCONCLUSIVE. Exit codes 0/1/2; INCONCLUSIVE exits 1 unless `--no-fail`.
*Order (ADR-039):* a capture too short for the engine (under 2 frames) is INCONCLUSIVE; otherwise an engine FAIL
stands even when the capture is shorter than 2·fps frames; the 2·fps minimum only stops a PASS.

## ADR-009 — Advisory for sustained 2–3 Hz flashing, modelled on IRIS "extended failure" (2026-10-24)

**Decision.** For each type, a span longer than 5 s during which the trailing-1-second qualifying-flash count
is ≥ 2 at every frame produces an advisory labelled "advisory - not a WCAG 2.3.1 criterion". It never changes
the verdict. (IRIS fails content with 4–6 transitions per second sustained for 5 s; FlashTrace only advises,
because WCAG 2.3.1 does not contain this rule.)

## ADR-010 — Toolchain: project venv on Python 3.14, Playwright's bundled ffmpeg for benchmark videos (2026-10-24)

**Context.** The laptop has Python 3.14.0 (brief says 3.11+), Node 26, Playwright 1.63 with Chromium 1243
already installed, and no system ffmpeg. Playwright ships `ffmpeg-1011` (libvpx VP8 encoder, WebM muxer).
**Decision.** `.venv` created with `python3 -m venv`; dependencies pinned in `pyproject.toml`
(`pip install -e ".[dev]"`). Benchmark `<video>` pages are generated as WebM/VP8 with Playwright's bundled
ffmpeg located through `playwright`'s driver cache; no Homebrew install, no extra services.
**Consequences.** Fully offline after install. If the bundled binary is missing, `<video>` benchmark cases
are skipped and reported as skipped (never silently).

## ADR-011 — Chromaticity of pure black is the D65 white point (2026-10-24)

**Context.** (u′, v′) is undefined when X+15Y+3Z = 0, i.e. for RGB (0,0,0). A transition between
saturated red and black must still be a red transition (the pre-2022 rule counted it; red strobes on black
are the classic hazard).
**Decision.** Treat black as achromatic: use D65 (u′, v′) = (0.1978, 0.4683), computed from the same
sRGB matrix. The distance from sRGB red (0.4507, 0.5229) to D65 is 0.259 > 0.2, so red ↔ black, red ↔ white
and red ↔ grey are red transitions; red ↔ orange (255,128,0) is not (0.13).
*Amended 2026-10-25 (ADR-043):* every near-black colour (all three whole 0–255 values below 16) gets the D65 white
point too, not only (0, 0, 0).

## ADR-012 — Demo server is loopback-only and sits behind an interstitial (2026-10-24)

**Context.** SPEC §9.2: the demo page intentionally strobes at 4–5 Hz; it exists only as a test subject.
**Decision.** `demo/serve.py` refuses any non-loopback host with a `ValueError` (not a warning), serves the
warning interstitial at `/` and the hazardous page at `/site/`, sends `Cache-Control: no-store`, and the page
honours `prefers-reduced-motion` and offers a "Pause all motion" button (state via `aria-pressed`).
**Consequences.** The flashing page is never reachable from another machine; the audit targets `/site/`
directly and no human has to look at it.

## ADR-013 — Ablation must suppress animations inside the capturer, not only via injected CSS (2026-10-24)

**Context.** Measured while building the demo: once a script has called `pause()`/`play()` on a
`CSSAnimation` through the Web Animations API, that animation no longer obeys the CSS
`animation-play-state` property. Strategy A pauses and seeks every animation through that API, so an
injected `[data-ft-id="X"]{animation-play-state:paused!important}` rule alone would be silently ineffective
during an ablation capture.
**Decision.** Ablation re-captures are fresh page loads in which the capturer (a) excludes animations whose
target matches the suppressed `data-ft-id` from seeking and holds them at their frame-0 state (and cancels
CSS transitions on that target via the injected `transition:none!important`), and (b) hides JS/canvas/video/img
candidates with `visibility:hidden!important`. The injected CSS from the brief is still emitted (it is what a
developer would write) and recorded in the report, but it is not what makes the ablation effective.
**Consequences.** Ablation results do not depend on whether the page's own scripts touched the animation.
*Amended 2026-10-25 (ADR-040):* as built, the brief's CSS is neither emitted nor recorded in the report (the
schema has no field for it). Suppression is done by unique selector inside the capturer (ADR-027): the element's
own animations and transitions are held at frame 0, a media element is hidden whole, and an element without an
animation of its own has its own paint hidden; its children are not suppressed. attribution.json records each
re-capture's selectors and result.

## ADR-014 — Benchmark geometry in 4-px cells; near-limit single elements use the nearest legal rectangle (2026-10-24)

**Context.** The truth file must state the exact number of flashing cells the engine will see. 1 306 cells
(0.24 × 5 440) and 1 414 (0.26 × 5 440) have no factor pair that fits a single rectangle inside the 85 × 64-cell
window.
**Decision.** Element rectangles are cell-aligned, at least 2 × 2 cells, laid side by side with 1-cell gaps inside
one 85 × 64-cell region anchored at fixed cell offsets. Multi-element pages split the target count exactly;
single-element pages take the nearest legal count (1 305, 1 416) and the truth records both `areaFraction`
(grid value) and `areaFractionActual` / `totalCells` (real). Scoring uses the real values.
**Consequences.** Truth area is exact, never estimated; 0.24 / 0.26 remain boundary cases either way.

## ADR-015 — Benchmark videos: JPEG frames piped to Playwright's ffmpeg, single-threaded VP8 (2026-10-24)

**Context.** Playwright's bundled ffmpeg (n7.0.1 build 1011) is compiled with `--disable-everything`: no
rawvideo demuxer, no `fd:` protocol, only image2pipe + mjpeg input, libvpx VP8 and the WebM muxer.
**Decision.** Feed two solid JPEG frames (quality 100, no chroma subsampling) through
`-f image2pipe -vcodec mjpeg -i pipe:0` and encode with `-c:v libvpx -b:v 2M -deadline best -cpu-used 0
-auto-alt-ref 0 -pix_fmt yuv420p -g 15 -fflags +bitexact -flags +bitexact -threads 1 -r 60`. Frame state uses
integer arithmetic `((i*2*hz)//60) % 2`.
**Consequences.** Measured: WebM output byte-identical across two full 200-page runs on this machine; Chromium
renders the greys exactly and red as (254,0,0). Video pages with target ΔL ≤ 0.11 or area 0.24 / 0.26 are
tagged boundary because the codec is lossy.

## ADR-016 — Benchmark distractors are inert by construction; WAAPI step timing lives on keyframes (2026-10-24)

**Context.** A distractor that produced qualifying flashes or added cells to the flashing window would make
the expected verdict wrong. The brief's WAAPI recipe (`easing: "steps(1,end)"` in the `animate()` options)
never reaches its 50% keyframe: an options-level easing shapes the whole iteration's progress, which stays at 0.
**Decision.** The 0.5 Hz fade uses colour (224,236,252), L = 0.8287, so its darker state is never below 0.80
(SPEC T2); the checkerboard is static; the marquee is at least one window away from the flashing region; all
items are ≥ 8 px from the elements. WAAPI pages put `steps(1,end)` on each keyframe (CSS per-segment timing).
**Consequences.** The expected verdict depends only on the flashing elements; pages still contain real
non-culprit animations for attribution to ignore. Any synthetic page built from the brief's WAAPI options
recipe is static and must be fixed the same way.

## ADR-017 — Benchmark randomness is seeded per page by (seed, index) (2026-10-24)

**Decision.** Each page uses `random.Random("flashtrace-bench:{seed}:{index}")`; all draws happen before any
file is written and never depend on ffmpeg availability. `bench/truth.json` is committed because it is small
(384 KB), reproducible and useful to inspect; `bench/pages/` is regenerated.
**Consequences.** Page *i* is identical for any *n*, so the 15-page test set is a prefix of the 200-page set;
machines without ffmpeg produce the same truth except video pages become `video-skipped` / `SKIP`.

## ADR-018 — Run boundaries: first-frame extremum, reversal tolerance, newer same-sign transition replaces pending (2026-10-24)

**Context.** Review of M1 found three places where the engine's (tested, deterministic) behaviour was more
precise than SPEC §5.4–5.5: which plateau frame stamps a transition, how tiny reversals are handled, and what
happens when two same-direction transitions meet.
**Decision.** (1) A transition is stamped at the **first** frame at which the run's extremum is reached, i.e.
when the change has finished; stamping the last plateau frame would delay a slow flash by its whole plateau.
(2) A reversal smaller than one twentieth of the threshold (0.005 in L and in the redness ratio, 1.0 in the
legacy red value) does not end a run, so anti-aliasing wobble cannot split a ramp; such a wobble could never be
a transition itself. (3) If a sub-threshold run separates two same-direction transitions, the newer one replaces
the pending one in the pairer.
**Consequences.** SPEC §5.4–5.5 amended. A rise of 0.06, a dip of 0.004 and another rise of 0.06 count as one
0.116 transition; the strict reading of WCAG Note 2 would count none. The effect is limited to signals that wobble
by less than 0.005.
*Amended 2026-10-25 (ADR-038, ADR-042):* once a run has settled, a further gain moves its stamp only when it goes
more than the reversal tolerance beyond the stamped value (ADR-038), and after the last frame only runs that have
settled are closed (ADR-042).

## ADR-019 — Qualifying events: backward 1-frame union, one event per two frames, absolute window area (2026-10-24)

**Context.** Review of M1 found, and a run confirmed, that the leading-edge rule of ADR-006 merges every flash
into one event when completions are two frames apart or closer. A full-screen 30 Hz strobe captured at 60 fps
returned **PASS with a rate of 1 per second**, and 24 Hz was counted as 12. The forward union `M[t] ∪ M[t+1]`
also put each event one frame *before* its completing frame, so the "before" and "after" evidence frames showed
the same image. Separately, on a viewport shorter than 256 px the window was clipped and the 25% denominator
shrank with it.
**Decision.** `E[t] = M[t−1] ∪ M[t]`; an event lasts at most two frames, so a run of `n` consecutive qualifying
frames holds `ceil(n/2)` events, counted at its 1st, 3rd, 5th … frame. The area fraction always divides by the
full window size (5 440 cells at 1024 px), because WCAG's 25% is an absolute area (21 824 px). Window sizes
round halves up. This replaces step 4 of ADR-006.
**Consequences.** Rates up to 30 Hz are counted correctly at 60 fps (frequencies above half the capture rate
cannot be seen in the frames at all; listed as a limitation). Evidence frames are the completing frame and the
frame before it. Under this rule, two regions that each qualify on their own on consecutive frames counted as two
events. *Amended 2026-10-25:* the `ceil(n/2)` counting step was replaced by the claim rule of ADR-038, under which
such regions count as one event (and one smooth flash completing over two frames is no longer counted twice).

## ADR-020 — Redness ratio on whole 0–255 values; reds darker than R = 16 have no hue (2026-10-24)

**Context.** Review of M1 found, and an exhaustive check confirmed, that 834 of the 2 079 colours whose redness
ratio is exactly 0.8 measured 0.7999999999999999 after the linear → sRGB round trip, so the inclusive "≥ 0.8"
boundary was missed. A second finding: rgb(0,0,0) ↔ rgb(1,0,0) is a red transition under the UCS rule (ratio 1,
chromaticity distance 0.259), so invisible video noise on black could fail a page.
**Decision.** The cell colour is re-encoded to sRGB and rounded to whole 0–255 values before `R/(R+G+B)` and the
legacy red value are computed (the brief's "0–255 sRGB components", taken literally). A colour with `R < 16` gets
ratio 0: its hue is not visible. 16 is not arbitrary: it is the smallest red that the pre-2022 working definition
could ever count against black, since `(R−G−B)×320 > 20` needs `R−G−B > 20/320 = 15.9/255`.
**Consequences.** Exact-0.8 colours are saturated red; near-black noise cannot create red flashes. A deliberate
dim red (R < 16) strobe is not a red flash (it is still a general flash if ΔL ≥ 0.10, which it cannot be at that
brightness). SPEC §5.3 amended; acceptance tests 12 and 13 pin it.
*Amended 2026-10-25 (ADR-043):* the same floor now also sets the chromaticity: a colour whose R8, G8 and B8 are all
below 16 gets the D65 white point, like black.

## ADR-021 — Timeline has a bin for a trailing partial second (2026-10-24)

**Decision.** `timeline` has `ceil(T/fps)` bins; the last bin may cover fewer than `fps` frames and holds its raw
count. Every qualifying event lands in exactly one bin, so the bins add up to the totals. SPEC §5.8 amended.

## ADR-022 — Strategy A drives shadow-root animations, SMIL, re-started animations and Math.random (2026-10-24)

**Context.** Review of M2, confirmed by a probe: `document.getAnimations()` returns 0 for an animation inside a
shadow root (the shadow root itself returns 1), and an SVG SMIL animation advanced 0.317 s in 0.3 s of real time
while `page.clock` was frozen. Both would have been captured on the real clock while the report said
"deterministic".
**Decision.** An init script wraps `Element.prototype.attachShadow` to record every shadow root (open or
closed); strategy A collects animations and `<video>` elements from the document, every recorded shadow root and
every same-origin frame. Outermost `<svg>` elements are paused (`pauseAnimations()`) and set per frame with
`setCurrentTime(t)`. An animation that the page resumes after A paused it is re-adopted on the next frame. An
init script replaces `Math.random` with a fixed-seed generator. The double-shot check waits 100 ms of real time
between its two screenshots. Any timed-out `<video>` seek or animation-ready wait sets `deterministic=false`.
**Consequences.** More page types are captured deterministically; what remains uncontrollable is still caught by
detection or the double-shot check. `crypto.getRandomValues` is not seeded (listed as a limitation).
*Amended 2026-10-25 (ADR-045, ADR-046):* `<video>` elements are now driven like animations, at the page's
playbackRate and from when the page started them. Detection and the double-shot check ran only before frame 0 and
at the first and last frames, so content that appeared in between was not caught; strategy A now looks for
uncontrollable content at every frame and runs the clock and double-shot checks at the first frame of every
captured second as well.

## ADR-023 — Strategy B on this platform: budgets with a fixed pre-roll; self-tested and skipped without BeginFrameControl (2026-10-24)

**Context.** Measured during M2 on macOS, Chromium 153 (Playwright 1.63): `Target.createTarget
{enableBeginFrameControl: true}` fails with "BeginFrameControl is not supported on MacOS yet"; a bare
`HeadlessExperimental.beginFrame` hangs; pausing virtual time before navigation never loads the page; and with
budgets only, page clocks are virtual but rendering follows the real clock (CSS boxes flipped every 3–4 captured
frames, a GIF advanced at its real cadence). Flash rates measured from such frames would be inflated.
**Decision.** B loads the page under 250 ms budgets, pre-rolls to exactly 2 000 ms of page time, and advances
`1000/fps` ms per frame. It probes BeginFrameControl with `Target.createTarget` (never with `beginFrame`) and runs
a once-per-build self-test (two pictures 100 ms of real time apart at one virtual instant). Without
BeginFrameControl, auto mode skips B and uses C; a forced B capture is marked `deterministic=false`. C records
`Page.startScreencast` frames, resamples by holding the latest frame, and is always non-deterministic.
**Consequences.** On this laptop every page with uncontrollable content is captured by C, so a PASS there is
reported as INCONCLUSIVE (ADR-008). The SPEC §6.3 determinism test for B is skipped with Chromium's own error text
rather than failed. A platform with BeginFrameControl (Linux headless) exercises the beginFrame path.
*Amended 2026-10-25 (ADR-046):* the self-test only shows that a CSS animation stands still with the virtual clock,
and it can pass on this platform. Animated images, SVG images, workers and cross-origin frames run on clocks it
does not measure: an animated SVG image that passed both double-shots was measured too fast and reported as
deterministic. So a budget-only B (no BeginFrameControl) now steps aside, as A does, for uncontrollable content
that the scan before frame 0 finds: in auto it steps aside; forced, it captures and reports `deterministic=false`
with the content in `meta.uncontrollable`. *Amended again 2026-10-25:* it also looks again during its frame loop, at
strategy A's checkpoints, and counts what an earlier strategy of the same capture found (ADR-046). With
BeginFrameControl, B keeps its role.

## ADR-024 — Motion in the web UI: purposeful one-shot transitions, proven safe by FlashTrace itself (2026-10-24)

**Context.** The student asked for a livelier viewer: reactive components, transitions and a stronger look and
feel, using their installed motion and component skills (Motion, React Bits, Magic UI, Aceternity). The brief's
safety rule still holds: the app's own UI never renders flashing faster than 3 Hz, and it must respect
`prefers-reduced-motion` and keep axe-core at 0 violations.
**Decision.** Add the `motion` library (one npm dependency, no service). Motion must explain cause and effect
(where something came from, what changed): route and dialog entrances, the selection outline gliding between
culprit boxes, chart bars growing from the baseline, numbers counting to their value once, the Verify result
settling in, list items leaving smoothly. Rules: no infinite or looping animation; one-shot durations of 600 ms or
less; no luminance toggling; user-driven toggles stay rate-limited; components taken from Magic UI / Aceternity /
React Bits are ported to the project's CSS (no Tailwind) and stripped of looping effects such as sparkles,
meteors, shimmer and beams; `MotionConfig reducedMotion="user"` plus the CSS media query turn motion off.
**Proof.** A browser test serves the built UI with fixture data, captures each screen's entrance and a scripted
"fast user" interaction sequence with FlashTrace's own capture layer, and runs FlashTrace's engine on the frames:
every screen must PASS. FlashTrace audits itself.
**Consequences.** More code in `web/` to explain; one extra dependency; the self-audit turns the safety rule into a
measured result instead of a promise.

## ADR-025 — Chromium runs with five rendering switches so machine load cannot reach the pixels (2026-10-25)

**Context.** The first full benchmark (commit 8674c95, 200 pages, 4 worker processes) found pages whose frame
hashes differed on a second run (the Determinism line of docs/RESULTS.md as committed in 0761f45), although every
capture reported `deterministic`. Every
differing page contained the scrolling-marquee distractor; every page without it was identical. Frames kept from
four simultaneous captures showed no shift in the text: a single 7 px-wide slice of newly revealed text was drawn
differently and then moved with the layer. Measured with four concurrent 10 s captures of that page: software
rendering alone (`--disable-gpu`, `--disable-gpu-compositing`, `--disable-gpu-rasterization`) fixed sequential runs
but not concurrent ones (a 20-page development benchmark under load still had differing pages); larger tiles or
disabling partial raster alone did not fix it; **every switch set containing `--disable-threaded-animation` made
the four concurrent captures identical**.
Strategy A pauses and seeks animations from the page; with threaded animation the compositor thread keeps its own
copy of a transform animation, and under load its raster timing leaks into the image.
**Decision.** `capture.capturer.launch_chromium` starts every Chromium FlashTrace uses with
`--disable-gpu --disable-gpu-compositing --disable-gpu-rasterization --disable-threaded-animation
--disable-partial-raster` (partial raster off so a tile never depends on its previous contents). Strategy-specific
switches are appended.
**Consequences.** A new browser test (capture/tests/test_rendering_switches.py) captures a marquee page four times
at once and requires identical hashes. The determinism figure of record after the change is the Determinism line
of docs/RESULTS.md from the re-run at a later commit (ADR-041); a development re-run of the 20 pages, not produced
by `flashtrace bench`, is not cited as a figure. Rendering is
done on the CPU, which also removes GPU differences between machines. Frame hashes differ from those recorded
before this ADR (expected; the full benchmark is re-run).

## ADR-026 — Attribution stage 1 ranks candidates by intersection over union of 4 px cell masks (2026-10-25)

**Context.** SPEC 7 asks for candidates ranked by how much each one overlaps the failing cells. If the score were plain coverage (the share of flashing cells inside the box), every ancestor that encloses the culprit would score 100%, the same as the culprit itself: <main>, wrapper divs, full-page overlays. Candidates also have to be collected with the page in exactly the state of the captured frame.
**Decision.** The engine adds AnalysisResult.violation_cells: for each violation, one bool [cells_y, cells_x] mask that is the union of the event masks E[t] = M[t-1] | M[t] over the violation's event frames. It is a by-product only and is not written by to_dict(). capture/inspect.py reloads the page with the capturer's own per-attempt setup (_run_attempt: init scripts, fake clock, interaction script). InspectStrategy is a copy of strategy A's frame loop that stops at each violation's first event frame and collects: getAnimations() targets (document, recorded shadow roots, same-origin frames), elementsFromPoint on a 5x5 grid inside the window, and video, canvas, animated img (detected by capture/detect.py) and iframe elements. html and body are excluded, and a cross-origin iframe becomes nodeType 'iframe (cross-origin, unattributable)'. Candidates are tagged data-ft-id='ft-<n>' in document order. Each gets a selector: #id when that id is unique, otherwise a tag:nth-of-type path from the nearest ancestor with a unique id. A selector that does not match exactly one element is recorded in attribution.json selectorWarnings. Each rect is turned into the cells it touches; a partly covered cell counts, because the engine averages all 16 pixels of a cell. overlap = |box AND flashing| / |box OR flashing|. Ranking: overlap descending, then smaller box area, then an element that can flash by itself (it has an animation or is self-painting media), then document order. With several violations, each selector keeps its best overlap. Candidates with overlap 0 are dropped.
**Consequences.** An enclosing ancestor scores low because most of its area does not flash; on the demo, #main scores 0.18 against 0.92 for #loader. Elements whose boxes cover exactly the same cells (the culprit and a static svg on top of it) tie on overlap, and geometry alone cannot separate them; the smaller-box and own-motion tie-breaks and stage 2 do that. The own-motion tie-break is an addition to the brief's 'smaller area, then document order'.
*Amended 2026-10-25 (ADR-044):* a partly covered cell still counts, but no longer because the engine averages a cell's 16 pixels. The engine now judges each pixel, and a cell is in violation_cells when at least one of its pixels completed a flash in the violation's events (engine/analyze.py _violation_cells uses the per-pixel counts `masks > 0`).

## ADR-027 — Ablation by selector in fresh captures, greedy then pruned minimal set, recorded in attribution.json (2026-10-25)

**Context.** ADR-013 requires suppression inside the capturer. Because each re-capture is a fresh page load, data-ft-id tags from the inspection page do not exist there. Re-captures are expensive (each one is a full capture plus an engine run), and the brief bounds them at k <= 6 single suppressions plus the greedy steps.
**Decision.** Each re-capture calls capture(..., suppress_selectors=[...]) with the unique selectors from stage 1, the audit's viewport, fps, duration and interaction script, and the strategy the baseline actually used (meta.strategy). It is analysed with the audit's red rule. ablationDelta = (qualifying general + red events of the baseline) - (the same count in the re-capture); a re-capture that raises gets ablationDelta null and is ordered last. The greedy order is ablationDelta descending, ties broken by stage-1 rank. Selectors are added until the suppressed set PASSes, then each member is removed again if the page still passes without it, which leaves a minimal set. Runs are memoised by their sorted selector set, so no set is captured twice. When even all top-k together fail, no culprit is marked and attribution.json limitations gets 'attribution: no minimal set within the top 6 candidates', which cli/report.py copies into the report. Re-capture frames go to <job>/ablation/<n>/frames and are deleted right after analysis; attribution.json keeps each run's selectors, verdict, qualifying counts, determinism, strategy, frame hash and frame count, plus the recapture count. Any failure during inspection or ablation is written to attribution.json 'error', and the culprits found so far are returned, so the audit's verdict is never lost.
**Consequences.** Disk use stays bounded; the hashes still let a run be compared later. *Corrected 2026-10-25 (ADR-040):* an earlier version of this paragraph said that suppressing an element also holds its descendants' animations, so that enclosing ancestors score the same ablationDelta as the culprit. The code does not do that: ftIsSuppressed (capture/page_scripts.py) checks only the element itself, and suppression stops only what that element does (its own animations, its own picture, or its own paint). Its children keep running, because they are candidates of their own. So a wrapper around the flashing elements gets ablationDelta 0 and is not in the minimal set. capture/tests/test_attribute.py::test_ablation_credits_the_flashing_tiles_not_their_wrapper pins this.

## ADR-028 — Benchmark attribution is scored on truth geometry; the ablation sample is reported separately (2026-10-25)

**Context.** Several elements that flash in sync can be explained by more than one minimal set, so plain set equality with truth.culprits marks correct answers wrong. Ablation costs about k + 2 captures per page, which is too much for the full 200-page pass.
**Decision.** bench/score.py scores only pages that are expected FAIL and audited FAIL. Top-1 is correct when culprits[0].selector is in truth.culprits; a FAIL page with no culprits counts as wrong. A minimal set is correct when every selector marked inMinimalSet is a truth culprit; when the cells of the remaining truth elements (exact w*h/16, since the rectangles are cell aligned per ADR-014) are at most 1360, or the page's rate is at most 3/s; and when putting back any single member makes it fail again. bench --attribute bbox|ablate attributes FAIL pages in the main pass. --ablate-sample K --ablate-duration D then re-audits the first K main-pass FAIL pages with ablate at D seconds, in its own worker pool. It reports requested/sampled/scored/not-FAIL counts, page ids, top-1, minimal-set accuracy, re-captures per page and audit wall time apart from the main pass. RESULTS.md prints the rule and states that the sample is small.
**Consequences.** Minimal-set accuracy is judged by the same 25%-of-window rule the engine applies, not by one arbitrary truth set. Ablation figures always come with their sample size and duration and are never presented as whole-benchmark accuracy.

## ADR-029 — Sample a quarter-frame into each slot; run animation-frame callbacks once per captured frame (2026-10-25)

**Context.** Two measurement errors were found by checking reported rates against pages whose true rate is known.
(1) The demo's loader strobes at exactly 5 Hz, but FlashTrace reported 6 per second: its events were 12, 24, 37,
48, 60, 73 … frames, not every 12. The loader switches state every 100 ms, so many switches fell exactly on a
sample instant, and Chromium decides such instants with floating-point seconds (0.6 / 0.2 = 2.9999999999999996),
showing the old state at some of them. One transition in three arrived a frame late, so six flashes fitted in one
second. Strategy A also anchored each animation's clock at the sample instant where it was first seen, which kept
every later sample on those switch instants. (2) An exact 3 Hz benchmark page driven by `requestAnimationFrame`
reported 4 per second (expected PASS, got FAIL): Playwright's fake clock fires animation-frame callbacks every
16 ms, which drifts against 16.667 ms frames, so event gaps were 19, 20 and 21 frames instead of 20.
**Decision.** Frame `i` shows the page at `(i + 1/4) / fps` (`capture.strategies.base.SAMPLE_PHASE`). At 60 fps a
quarter-frame offset is never a whole millisecond or a third of one, so it never coincides with the state switch of
a CSS or Web Animations animation whose timing is whole milliseconds or thirds; for all rates up to 3 per second and
the 2.5 per second fix target this holds for every frame. An animation first seen in a pass starts its clock at the
start of that frame slot (`anchorMs`), not at the sample instant. An init script (`RAF_SYNC_JS`) queues
`requestAnimationFrame` callbacks, and strategy A runs the queue once per captured frame at that frame's clock time,
before seeking animations and taking the picture, as a browser runs them once per drawn frame. Strategy B advances to
the quarter-frame before frame 0; strategy C (real time) keeps its grid because its timestamps come from the real
clock.
**Consequences.** Measured after the change: the demo reports 5 per second with every gap 12 frames; the 3 Hz rAF
benchmark page reports 3 per second with every gap 20 frames and passes; the rAF and CSS test pages now agree frame
for frame. capture/tests/test_sampling.py pins the two rates and gaps (on a 5 Hz strobe page and a 3 Hz rAF page),
and capture/tests/test_determinism.py pins the frame-for-frame agreement. Every frame hash changes once (expected).
Reports still label frame `i` as `t = i / fps`, the start of its slot.
*Amended 2026-10-25 (ADR-048):* strategy C no longer keeps its grid. It starts half a 60 Hz compositor tick away
from the lattice its screencast timestamps sit on, or a quarter of a sample after the first frame when the
timestamps agree on no phase.
*Corrected 2026-10-25:* SPEC §6.2 listed strategy A's steps per frame as seek, then `run_for`, then screenshot. The
code (capture/strategies/waapi_step.py `run`) advances the clock first, by `round(t_i) − round(t_{i−1})` ms, then
runs the queued animation-frame callbacks and seeks every animation and video in one pass, then takes the picture.
Both orders show every clock at `t_i`; advancing first lets an animation that a timer or callback creates be
seeked before the picture instead of one frame later. SPEC §6.2 now lists the code's order. No behaviour changed.

## ADR-030 — How fixes are derived from measured data (capture/fix.py) (2026-10-25)

Context: SPEC 7 asks for at most 3 CSS fixes per culprit, built from measured values. Decision: Each culprit's two states are measured from the job's frames: the per-pixel median colour inside the culprit's box, on a qualifying event frame f and on frame f-1. Of the first 12 event frames of the violations the culprit takes part in, the pair with the largest linear-light difference is kept. The engine's colour functions turn each state into L and R/(R+G+B). Targets, each a margin below the W3C wording: rate 2.5 flashes per second, below 'more than three times in any one second period'; flashes 0.4 s apart put at most 3 in any 1 s window. Luminance: a difference of 0.09, below '10% or more'. Red: a ratio of 0.75 for CSS colours, below 'greater than or equal to 0.8'. Area: 95% of the largest non-qualifying count, floor(25% of the full window) = 1360 cells at 1024 px. Rate: D' = ceil(r x D / 1000 x 1000 / 2.5), proposed for CSS animations and Web Animations with a finite duration; for Web Animations the rationale says CSS may not override animate() timing. No rate fix for transitions, script, canvas or video, and the first fix's rationale says why. Luminance and red for CSS animations: a new @keyframes ft-fix-<rank>-lum|red copies the inspected keyframes. For luminance, every opaque colour brighter than darkest + 0.09 is scaled in linear light, which keeps chromaticity; the scale is found by bisection on the whole-number colour. For red, each saturated red is mixed in linear light with the grey of equal luminance until its ratio is <= 0.75. animation-name is then overridden. For other mechanisms (and for see-through keyframe colours): filter: contrast(c) or saturate(s), using the Filter Effects Level 1 formulas on sRGB-encoded values, rounded to bytes. Each amount is the largest one, to 3 decimals, that meets the target. The SPEC's 'or opacity' is not used, because the result of opacity depends on what is behind the element. *Corrected 2026-10-25 (ADR-040):* opacity is used, but only for keyframes that switch opacity between two values and change no colour, visibility or display (capture/fix.py opacity_luminance_fix). The two opacity values are moved toward their middle by the largest factor at which the two measured states, which already include what was behind the element, differ by at most 0.09 in L. The luminance order is keyframe colours, then keyframe opacity, then contrast(). A contrast() or saturate() filter is not offered for an element that is see-through in one of its states. Order: rate, luminance, red, area, cut to 3. Consequences: every number in a fix and its rationale comes from this audit, and the rationale quotes the threshold it targets. SPEC 7 amendment: 'luminance (difference <= 0.09: brighter keyframe colours lowered in linear light, keeping the hue, or a contrast() filter for other mechanisms)'.

## ADR-031 — Which culprits get fixes (2026-10-25)

After ablation: the minimal set only. After bbox ranking: culprits with overlap >= 0.5; among those, the ones that move by themselves (they have an animation, or are video, canvas, iframe or img) are kept; if none moves by itself, only the top-ranked one. At most 3 culprits. (Clarified 2026-10-25: the cap of 3 applies to the bbox rule. Every member of a minimal set gets fixes, because each one is needed for a PASS. When ablation finds no set, the bbox rule applies.) Context, measured: on the demo, #loader, the static court <svg> under it and the panel <section> all scored IoU 0.923. Without the tie-break, the svg and section each received a contrast() filter and an area fix that point at the wrong element.

## ADR-032 — How fixes are verified (2026-10-25)

verify_fix injects the fix CSS as a <style> element (capture extra_css) and re-captures with the job's settings: duration, viewport, fps, red rule, and the interaction script copied to <job>/script.js at audit time. The original capture's strategy is forced, so fall-through cannot change it. The frames go to <job>/verify/fix-<n>/frames, and the engine runs on them. 'after' is a schema measurement whose verdict follows the SPEC 6.4 escalation. verified = after.verdict == PASS and after.deterministic. Area fixes are never applied: after stays null, verified stays false, and a note is added to the rationale. A failed re-capture gives an INCONCLUSIVE 'after' and the error is appended to the rationale, because the schema's fix object has no notes field. When the fix's culprit belongs to a minimal set of two or more, one fix per other member is injected with it (capture/fix.py companion_fixes), because with any member still flashing the page fails. On css_keyframes.html (3 s), the rate fix (400 ms) and the luminance fix (#ffffff to #545454) both verify: the re-capture PASSes with a general rate of at most 3 per second under deterministic capture. capture/tests/test_fix.py::test_box_fix_verifies_by_recapture pins this.

## ADR-033 — CLI and job metadata for M6/M7 (2026-10-25)

`flashtrace audit --attribute` now defaults to bbox; bench keeps none. meta.json keeps the settings with an absolute script path, plus jobDir and reportCopies (the --out paths); the server's adopt_run already rewrites jobDir. `flashtrace fix REPORT` lists the fixes numbered from 1 (the HTTP API counts from 0). `--apply N` prints fix N's CSS on stdout. `--apply N --verify` finds the job in this order: a report.json next to meta.json; a meta.json whose reportCopies lists the file; a job under --work-dir whose report has the same url, capturedAt and capture hash. It then runs verify_fix_in_job, updates the given copy too, prints before and after, and exits 0 if verified, 1 if not (area fixes always 1), 2 on error. Progress: a progress callable that also has phase(label, done=None, total=None) is told 'attributing (bbox)', 'attributing 3/6', 'proposing fixes' and 'verifying'. The CLI prints these on stderr. `serve` lazily imports server.app.serve(port, work_dir, allow_local_files).

## ADR-034 — In the HTML export, fix CSS is shown in <code> with '@' written as the entity &#64; (2026-10-25)

It looks identical, but the page source never contains '@keyframes'. The plain-text check that the report page declares no animation (SPEC 9.1, test_html) therefore stays meaningful even when a luminance fix is listed.

## ADR-035 — Local server: job directory made at submit time, audit built in <job>/run/, one worker thread (2026-10-24)

Context: POST /jobs must return a stable id at once, but cli.audit.run_audit always creates its own time-stamped job directory, and the brief allows only one audit at a time. Decision: POST /jobs creates the job directory right away with cli.jobs.new_job_dir and writes meta.json with status queued and source server. The single runner thread later calls run_audit(work_dir=<job>/run), which builds <job>/run/jobs/<audit id>/. When it finishes, server.jobs.adopt_run moves every entry up into <job>/ and merges meta.json. The audit's fields are kept, the job's own jobId, createdAt and source win, and auditJobId plus a corrected jobDir are recorded. Tasks (audit, ablate -> cli.audit.reattribute, verify -> cli.audit.verify_fix_in_job) share one FIFO deque behind a threading.Condition. Any exception ends a task with status error and one sentence such as 'The audit failed (TimeoutError: ...).'. *Added 2026-10-25:* save_state never raises: a status that cannot be written (full disk, read-only folder, damaged meta.json) is logged and kept in memory. If recording an outcome still fails, the worker marks the job error and continues; submit restarts a worker thread that has died. The in-memory index is rebuilt from every readable meta.json at start; a folder without a readable meta.json is left out. Consequences: server jobs and flashtrace audit jobs share one history in the same layout, so contract items 4 and 5 work on server jobs. Ablation and verification keep a single phase for their whole run, as the UI's stepper expects. A follow-up task that fails sets status error, but the report stays readable, because it existed before the failure. After an ablation the summary's settings.attribute follows report.settings.attribute. *Corrected 2026-10-25:* "any exception ends a task with status error" and "a follow-up task that fails sets status error" are true only of a new audit. A follow-up (ablate, verify) works on an audit that is already done, so its report files are backed up before it starts. When it fails, or is stopped by the watchdog or by the server stopping, the files are put back (server/runner.py end_unfinished_task) and the job stays done, with its old report and the one sentence in error (server/jobs.py mark_follow_up_failed). The same happens at the next start to a follow-up the server was cut off in (recover_if_interrupted). The job becomes error only when the files cannot be put back. A new audit that fails ends as error. server/tests/test_api.py::test_failed_ablation_leaves_the_audit_done_with_the_error_and_its_report and server/tests/test_queue.py::test_interrupted_follow_up_leaves_the_job_done_with_its_report pin this.

## ADR-036 — Cancelling and restart recovery (2026-10-24)

Decision: the runner passes run_audit a JobProgress object. It is called once per captured frame, and cli.audit calls its phase(label, done, total) hook between steps; reattribute and verify_fix_in_job get it too when their signature accepts progress. Once DELETE has set the job's cancel flag, both entry points raise JobCancelled. JobCancelled derives from BaseException, so run_audit's handler that turns capture failures into an INCONCLUSIVE report (it catches Exception) cannot swallow it, and the capture's finally blocks close the context and the browser. DELETE removes the job from the index at once and returns 204. A queued task is dropped and its directory deleted immediately. A running task's directory is deleted by the runner after the task stops, never while it is still writing. *Amended 2026-10-25 (ADR-049):* a cancelled task still running 2 s later (CANCEL_GRACE_S) has its own Chromium processes killed by the runner's watchdog, so the hung call raises. A step without a browser (engine analysis) still finishes first. DELETE takes the job out of the index and then writes a `deleted` marker in the job folder, before it cancels the task or touches any files. Folders are removed by renaming them to <id>.deleted and then deleting them. The next start removes marked folders and .deleted leftovers. On shutdown, the lifespan stops the runner and the running job ends as error 'The server stopped during the job.'. On the next start, any job still marked running becomes error with that message, and any job still queued becomes error 'The server stopped before the job started.'. Its half-built <job>/run/ folder is removed and its report, if any, is kept. (Corrected 2026-10-25, see ADR-035: a follow-up, ablate or verify, cut off by the stop is not marked error; its report files are put back and the job stays done with that sentence in error.) Consequences: cancel takes effect within one frame, or within about 2 s for a step that waits on the browser (ADR-049). A flashtrace audit running into the same work directory while the server starts would be shown as error until the next restart.

## ADR-037 — Server hardening beyond docs/API.md (2026-10-24)

Context: a loopback-only server can still be reached by web pages through DNS rebinding and cross-site form POSTs. Decision: (1) serve() refuses any host but 127.0.0.1. (2) FlashTrace's AllowedHostMiddleware (validation.host_allowed) accepts only the Host names 127.0.0.1 and localhost, with any port, and answers anything else with 400 {"detail": "The Host header must be 127.0.0.1 or localhost."} (amended 2026-10-25; it replaced Starlette's TrustedHostMiddleware, whose 400 was plain text). (3) A POST or DELETE carrying an Origin header that is not the server's own origin (or, with FLASHTRACE_DEV_CORS=1, http://localhost:3000 or http://127.0.0.1:3000) gets 403. Requests without an Origin header, such as curl, are allowed. (4) CORS is off unless FLASHTRACE_DEV_CORS=1. (5) URLs with a user name or password are refused with 422, because the URL is stored in meta.json and the report. (6) Swagger and ReDoc are disabled because they load scripts from a CDN; /openapi.json stays. (7) A malformed job id returns 422; a frame index must be a whole number, and anything outside 0..frameCount-1 returns 404, so a client never names a path. (8) Relative local paths are resolved against the project directory, and symlinks are resolved before the inside check. The outside check runs before the existence check, so the error never reveals whether an outside file exists. (9) *Added 2026-10-25:* every response, refusals included, carries `Content-Security-Policy: frame-ancestors 'none'`, `X-Frame-Options: DENY` and `X-Content-Type-Options: nosniff` (SecurityHeadersMiddleware, the outermost layer), so no other site can show the UI in a frame and trick a user into clicking Verify or Ablate (clickjacking); the static UI's .js, .mjs and .css files are served as text/javascript and text/css, so nosniff never blocks them. *Corrected 2026-10-25:* "every response" leaves out one case. Starlette 1.10.0 puts its ServerErrorMiddleware outside every middleware FlashTrace adds, and server/app.py registers no handler for an unhandled exception, so an unexpected internal error (a bug in a route) is answered with Starlette's plain-text 500 "Internal Server Error": it is not {"detail": ...} JSON and does not carry the three headers. Every response the API and the UI produce themselves, refusals and 4xx errors included, does. docs/API.md says so. *Fixed 2026-10-25:* server/app.py now registers a handler for `Exception` (`on_unexpected_error`). Starlette runs it from ServerErrorMiddleware, so an unhandled exception is answered with 500 {"detail": "Internal error: <first line of the exception>"} (the exception's class name when its message is empty), and the handler sets the three headers itself, because SecurityHeadersMiddleware sits inside ServerErrorMiddleware and never sees that response. The case above no longer exists: every response carries the headers. Starlette re-raises the exception after answering, so the server log still shows it. server/tests/test_security_static.py pins `test_unexpected_error_is_json_and_carries_the_security_headers`. Consequences: two extra status codes (400 bad Host, 403 foreign Origin), both JSON {"detail": ...}; docs/API.md lists them with the headers.

**ADR-024 amendment (2026-10-25).** A 390 px and dark-mode review, measured with the self-audit, found ordinary
actions that exceeded 3 flashes per second: one History delete (a gap-closing glide of dense text across the screen),
Back/Forward pairs between routes, culprit selection re-wrapping the heatmap caption and gliding a dense label, radio
churn moving the form, and the reduced-motion switch replaying entrances. Fixes: list gaps close at once; large
layout shifts are designed out (constant caption, always-present custom-size row); dense glides run only at 1024 px
and wider; the route display gate is measured from the last display change and holds for 500 ms; the frame viewer
steps at most every 500 ms, counted from the end of its opening fade; network results apply at a fixed page-clock
time after their request. The self-audit (tests/test_ui_self_audit.py) runs every interaction scenario at 1280 px
light, 390 px light and 1280 px dark, plus an attack scenario for each finding and a strobe control page that must
FAIL; all pass, also with four concurrent processes. The worst measured case is the frame viewer at 390 px (open,
two steps, close) at 3 per second, which is within WCAG's "no more than three"; four in one second is impossible by
the gate arithmetic.

## ADR-038 — Event counting by claims, and stamps that sub-tolerance noise cannot move (2026-10-25)

**Context.** The M1/M2 review fixes (commit 8674c95) changed two engine rules without an ADR: SPEC §5.4/§5.6 and
ADR-018/ADR-019 kept describing the earlier rules while engine/area.py and engine/transitions.py did something else.
(1) Under ADR-019, a run of `n` consecutive qualifying frames held `ceil(n/2)` events. A smooth flash whose dim and
bright cells reach their extreme byte one frame apart keeps three frames over 25%, so it was counted twice: a 3 Hz
split pulse measured 6 per second and failed. (2) ADR-018 stamped a transition at the first frame at which the run's
extremum value is reached. With noise of one byte on a flat colour, each new extremum moved the stamp, which spread
one flash's completions over up to 8 frames, and no single frame reached 25%.
**Decision.** (1) *Claim rule* (engine/area.py `count_events`). Going forward through the frames, an event counted at
`t` claims frames `t` and `t+1`, the 1-frame tolerance of SPEC §5.6 step 1. A qualifying frame that is not yet
claimed is scored again using only the completions of `t−1` and `t` that no earlier event has claimed, and it is an
event when that share is still > 25% (WCAG: "no more than ... 25% of any 10 degree visual field"). (2) *Settled
stamps* (engine/transitions.py `RunTracker`). While the signal keeps moving, every new extremum moves the stamp. Once
it has stood still or wobbled back by no more than the reversal tolerance, a further gain moves the stamp only when
it goes more than the tolerance beyond the stamped value. The threshold rule always sees the true extremum.
**Consequences.**
* The same cells completing on consecutive frames still give `ceil(n/2)` events, and rates up to 30 Hz are still
  counted at 60 fps.
* One flash completing over two frames counts once (SPEC §5.10 item 15). Sub-tolerance noise leaves the flash
  intact (item 16). engine/tests/test_acceptance.py and engine/tests/test_area.py pin both.
* *Trade-off, accepted.* Two separate regions that each exceed 25% on their own and complete exactly one frame
  (16.7 ms at 60 fps) apart count as one event, as under ADR-006. Two or more frames apart, they count as two. The
  count is therefore not monotone in the delay. Two 2 Hz regions offset by one frame measure 2 per second and pass;
  offset by two frames they measure 4 per second and fail. Completions one frame apart are treated as concurrent,
  which is the 1-frame tolerance the area rule already uses. The alternative, `ceil(n/2)`, doubles the rate of
  ordinary smooth fades, which are far more common than two independent strobes offset by exactly one frame.
  engine/tests/test_area.py::test_two_regions_qualifying_on_consecutive_frames_count_once pins the one-frame case.
* SPEC §5.4, §5.6 step 4, §5.8 and §5.10 (items 15 and 16) are amended. ADR-019's consequence sentence is marked as
  replaced. docs/ENGINE.md already described both rules.

## ADR-039 — An observed FAIL stands in a capture shorter than two seconds (2026-10-25)

`cli/report.py` `decide_verdict` applies SPEC §6.4 in a fixed order: capture error, or fewer than 2 frames, gives
INCONCLUSIVE; then engine FAIL gives FAIL; then fewer than 2·fps frames gives INCONCLUSIVE; then a non-deterministic
PASS gives INCONCLUSIVE; otherwise PASS. SPEC §6.4 listed the 2·fps rule first, which would turn a 1 s capture that
already shows more than three qualifying flashes in one second into INCONCLUSIVE. FAIL wins for the same reason as
in ADR-008: what was observed did flash. The 2·fps minimum exists only because a short PASS proves little. SPEC
§6.4 is amended to the numbered order.

## ADR-040 — SPEC §7 brought in line with attribution and fixes as built (2026-10-25)

**Context.** SPEC §7 still described ablation with the brief's injected `[data-ft-id="X"]{animation-play-state:...}`
CSS, luminance fixes "by lowering the brighter state's lightness, or opacity", and verification of one fix alone.
ADR-013, ADR-027 and ADR-030 each contained one statement that the code does not do.
**Decision.** SPEC §7 now states the built behaviour:
* ablation by unique selector inside the capturer, with no CSS emitted or recorded;
* suppression of the element itself only, so its children are separate candidates;
* which culprits get fixes (ADR-031);
* the fix order and each fix's mechanism: the opacity fix is real and limited to opacity-only keyframes, and
  filters are not used on see-through elements;
* verification together with one fix per other minimal-set member.

ADR-013, ADR-027, ADR-030 and ADR-031 each carry a dated correction instead of being rewritten.
**Consequences.** A reader who compares SPEC §7 with capture/attribute.py, capture/page_scripts.py and
capture/fix.py finds no silent difference. No behaviour changed.

## ADR-041 — Figures of record live only in docs/RESULTS.md (2026-10-25)

**Context.** The brief allows accuracy, agreement and runtime figures only in docs/RESULTS.md, produced by a
script in this repository with date and commit. Every report's `limitations[]` says so too (SPEC §10). Development
measurements had been typed into STATUS.md, ADR-025, ADR-032 and AI_USAGE.md: wall times of probes, and
determinism counts from re-runs that `flashtrace bench` did not produce. One of them, a claim that every re-run
page was identical after ADR-025, also contradicted the only scripted figure at that time.
**Decision.**
* Accuracy, agreement (including run-to-run determinism) and runtime figures appear only in docs/RESULTS.md, which
  only `flashtrace bench` writes. The per-page JSON it cites, docs/RESULTS.json, is kept next to it.
* Other documents describe development checks qualitatively and point at the test that pins the behaviour, or at
  docs/RESULTS.md.
* Test counts, and single expected values that a test asserts (for example "the 5 Hz strobe page measures 5 per
  second"), are not accuracy figures and may be stated.
* STATUS.md, ADR-025 and ADR-032 are edited accordingly. AI_USAGE.md is append-only, so it gets a dated correction
  entry.
* A benchmark re-run is recorded in STATUS.md with its command and commit. The committed docs/RESULTS.md must come
  from a clean worktree of the commit it names.
**Consequences.** The sentence in every report's limitations ("accuracy, agreement and runtime figures exist only in
docs/RESULTS.md") is true of the repository. Development timings are no longer kept anywhere. Anyone who needs one
runs `flashtrace bench`. ADR-051 records how the bench itself warns when its output could not serve as that record.

## ADR-042 — End of the capture: only runs that have settled are closed (amends ADR-004 and ADR-018) (2026-10-25)

**Context.** ADR-004 said that a run still open at the last frame is evaluated at the last frame. A smooth pulse
that the capture cuts off part-way down a fall was then closed there: the engine stamped a flash on the last frame,
up to a few frames before the valley that the signal never reached inside the capture. A 3 Hz cosine or eased
black ↔ white pulse, 600 frames sampled at `(i + 1/4)/60`, could therefore count four flashes in its last second
and FAIL, and a 2.9 Hz pulse failed at some phases too. SC 2.3.1 fails only content that "flashes more than three
times in any one second period", and a 3 Hz pulse flashes three times.
**Decision.** After the last frame, `RunTracker.finish` (engine/transitions.py) closes an open run only when its
signal has *settled*: since its stamp it has stood still or turned back by no more than the reversal tolerance
(ADR-018, ADR-038). Such a run has reached its extremum inside the capture, so it is evaluated and stamped at its
stamp frame exactly as during the capture. A run still moving at the last frame is dropped, because its real end
lies after the capture.
**Consequences.**
* A flash whose completing step is shown only by the last frame is not counted. That is lenient by at most one
  flash per cell, at the very end of the capture. A flash that completed earlier and whose last state is held for
  at least one more frame still counts.
* SPEC §5.4 amended; SPEC §5.10 item 17 pins both sides (engine/tests/test_acceptance.py `test_17_*`), and
  engine/tests/test_transitions.py adds `test_open_run_still_moving_at_the_end_is_dropped`,
  `test_open_run_that_has_settled_is_evaluated_by_finish` and
  `test_smooth_pulse_cut_off_by_the_end_adds_no_early_flash`.
* Verdicts can change, so the engine version is 1.1.0 (ADR-044).

## ADR-043 — Near-black colours are achromatic (amends ADR-011 and ADR-020) (2026-10-25)

**Context.** ADR-011 gives pure black the D65 white-point chromaticity, and ADR-020 gives every colour with
R8 < 16 a redness ratio of 0 ("a red that dark has no visible hue"). A red that fades smoothly to black passes
through near-black reds such as (10, 0, 0). Their ratio was already 0, but their chromaticity was still red. The run
of the ratio signal ended at the first such frame, so the fade measured almost no colour change and failed WCAG's
"the difference between states is more than 0.2 (unitless) in the CIE 1976 UCS chromaticity diagram". A 4 Hz
(150, 0, 0) ↔ black cosine pulse therefore passed, while a square wave between the same two colours failed. For
the same reason (150, 0, 0) against (1, 0, 0) or (2, 2, 2) was not judged like red against black, so one byte of
noise in the black decided the verdict.
**Decision.** A colour whose whole 0–255 values R8, G8 and B8 are **all** below 16 (`RED_MIN_R8`, the ADR-020
floor) gets the D65 white point as its chromaticity, exactly like black (engine/color.py `is_near_black`, used by
`uv_chromaticity`). It looks black, and the same floor already says that its red shows no hue. Cells and single
pixels (ADR-044) are treated alike.
**Consequences.**
* Red ↔ (1, 0, 0), red ↔ (2, 2, 2) and red ↔ (15, 0, 0) are judged exactly like red ↔ black, and a smooth red fade
  to black is a red flash like the square wave (SPEC §5.10 item 18; engine/tests/test_color.py
  `test_near_black_colours_have_the_white_point_chromaticity` and
  `test_is_near_black_needs_every_channel_below_the_floor`).
* The floor is hard, so (16, 0, 0) ↔ (15, 0, 0) is a red transition, as (16, 0, 0) ↔ black already was.
* Acceptance item 13 (rgb(0,0,0) ↔ rgb(1,0,0) passes, no red flash) still holds, because (1, 0, 0) has ratio 0.
* SPEC §5.3 amended. Engine version 1.1.0 (ADR-044).

## ADR-044 — Transitions and area are measured to the pixel inside the 4 × 4 px cells (amends ADR-002 and ADR-006) (2026-10-25)

**Context.** The build brief asks for 4 × 4 px cells. ADR-002 averaged each cell's 16 pixels in linear light and
applied the threshold rules to that average. That is exact only when a flashing element lines up with the 4 px
grid; an element that covers part of a cell got the wrong area in both directions:
* a black/white edge that covers 2 of a cell's 16 pixels still changes the cell's mean luminance by 0.125, so the
  whole cell counted as flashing;
* a low-contrast element (luminance change 0.124) that covers half a cell changes the mean by only 0.062, below
  WCAG's "10% or more", so none of it counted.

Moving the same rectangle by 1 to 3 px could therefore change the verdict. A 172 × 120 px black/white rectangle
(below 25% of a window) failed at one offset, and an 88 × 256 px low-contrast one (above 25%) passed at one.
WCAG measures "the combined area of flashes occurring concurrently", which does not depend on a pixel grid.
**Decision.**
* **The brief's cells stay, for timing.** Each cell's mean signal (luminance, redness ratio, chromaticity) still
  decides when a run starts and ends (SPEC §5.4). Following every pixel's signal over hundreds of frames would mean
  sixteen times as many signals, and the cell mean keeps the brief's grid and its leniency for balanced fine
  patterns (SPEC T6).
* **The threshold is asked per pixel.** When a run ends, the rule is applied to each pixel: its colour where the
  run started against its colour at the run's extremum. The cell makes a transition when at least one pixel does,
  and the transition records which pixels did (`Transition.pixels`). A cell whose pixels are one colour at both
  ends is judged once.
* **Flashes are paired per pixel** (SPEC §5.5), and `M[t]` counts the pixels of each cell (0 to 16) that complete
  a flash at `t`.
* **Area in pixels** (SPEC §5.6). Step 1 is `E[t] = min(16, M[t−1] + M[t])`: a pixel cannot complete two flashes one
  frame apart, so the sum is a union of different pixels. Step 2 divides the flashing pixels in the best window by
  the pixels of the full window (85 × 64 cells × 16 = 87 040 px at 1024 px wide), so an event needs more than
  21 760 px: WCAG's "no more than ... 25%" applied to the scored window.
* `ENGINE_VERSION` goes from 1.0.0 to 1.1.0 because verdicts can change (this ADR, ADR-042 and ADR-043).

**Consequences.**
* A region whose cells fit inside one window position is measured to the pixel wherever it sits: the 172 × 120 px
  rectangle measures exactly its 20 640 px at every offset tested (SPEC §5.10 item 19,
  engine/tests/test_acceptance.py `test_19_*`).
* Windows still slide in whole cells (4 px steps). A region that spans more cells than the window can lose up to
  3 px along such an edge, in the lenient direction; SPEC §10 lists it.
* A balanced fine pattern inside a cell (half its pixels brighten while the other half darken) leaves the cell mean
  still, starts no run and is not counted, as before. An unbalanced one moves the mean, and its pixels are judged.
* `FrameStats` `generalCells` / `redCells`, and the `violation_cells` that attribution compares boxes with, now
  mean cells with at least one flashing pixel; `generalAreaMax` / `redAreaMax` are pixel shares (SPEC §5.8, §7).
* The engine keeps each cell's pixels at the two ends of its current run and applies the rule per pixel only where
  runs end. docs/ENGINE.md describes the cost in words; runtime figures stay in docs/RESULTS.md (ADR-041).
* engine/tests/test_area.py (pixel counts), test_transitions.py (per-pixel rule and pairing) and test_color.py
  (`pixel_blocks` and the pixel measures) pin the parts.

## ADR-045 — Strategy A drives `<video>` like an animation (2026-10-25)

**Context.** SPEC §6.2 said that strategy A seeks every `<video>` to `t_i`. That ignores what the page asked of the
video. A 2 Hz flashing video that the page plays at `playbackRate` 2 really flashes 4 times per second, but it was
seeked at normal speed, measured 2 per second and passed. A video the page never started (no `autoplay`, no
`play()`) was played anyway, and the page's own `pause()`, `play()` and seeks were overwritten.
**Decision.** Each `<video>` is driven like a CSS or Web Animation (ADR-022, ADR-029). It is muted and paused, and
per frame it is seeked (waiting for `seeked`) to media time `phase + playbackRate × (t_i − start)`, wrapped when it
loops and held at its end otherwise.
* `start` is the start of the frame slot in which the page started the video (`autoplay`, or its first `play()`).
* `phase` is the media time the page gave it: a `currentTime` assignment, `fastSeek()`, or where `play()` found it.
  A video already playing on the real clock when it is first seen is not read from, because real time would put it
  somewhere different on every run.
* What the page asks is honoured. `ANIMATION_RECORDER_JS` (capture/page_scripts.py) records the page's `pause()`,
  `play()`, `fastSeek()` and `currentTime` assignments; FlashTrace's own pause and seek go through `ftOwnCall`, so
  they are not mistaken for the page's. A pause holds the video where it stands, `play()` continues from there, an
  assigned time restarts its clock from that time, and a new `playbackRate` applies from the frame that first sees
  it.
* A video the page never started keeps the picture the page left it at.

**Consequences.** The 2 Hz video at rate 2 now fails at 4 per second, like a 4 Hz video at normal speed, and pages
that leave their videos alone are captured as before. capture/tests/test_robustness.py pins rates 2 and 0.5 (each
captured twice with identical hashes), a rate change, `play()`, `pause()` and a seek at 500 ms, and a video never
started. The capture notes say how many videos were driven, started later, held or never started. SPEC §6.2
amended.

## ADR-046 — Uncontrollable content is looked for during the capture, not only before it (2026-10-25)

**Context.** Strategy A promises identical frames only for content it controls. Four gaps let other content through
while the capture still said `deterministic=true`:
* detection ran once, before frame 0, and the double-shot check ran only at the first and last frames. Content
  that appears in between, such as a GIF a timer inserts at 1 s and removes at 3 s, slipped through both;
* animated SVG images (SMIL elements or CSS `@keyframes` inside an SVG shown as an image) and Web Workers, whose
  timers run on the real clock, were not detected at all;
* past the first 200 image URLs of a page, images were simply not checked;
* strategy B without BeginFrameControl trusts a self-test that measures only a CSS animation. When A stepped aside
  for an animated SVG image, B could pass that self-test, capture the image on the real clock, measure it too fast
  and still report `deterministic=true`.

**Decision.**
* **Checks during the loop.** Strategy A runs the page-clock check and the double-shot check at frame 0, at the
  first frame of every captured second (every `fps`-th frame) and at the last frame (`is_checkpoint`).
* **Detection during the loop.** `detect.ContentWatch` remembers what the first scan reported. At every frame A
  asks it for anything new: image elements, WebGL and offscreen canvases, cross-origin frames, workers, and every
  image URL the page requested since the previous frame (`NetworkTracker.take_image_requests`). At the checkpoint
  frames it also walks every CSS property that paints an image, which is too slow to do every frame.
* **What a late finding does.** Content found after frame 0 is a failure: auto falls through to the next strategy,
  and A's loop stops at its first failure because those frames would be discarded. A forced A finishes, reports
  `deterministic=false` and lists the content in `meta.uncontrollable` (`StrategyOutcome.uncontrollable`).
* **New kinds of uncontrollable content.** SVG images with SMIL elements or `@keyframes`, gzipped `.svgz` included;
  SVG markup longer than 64 KB is read up to 4 MB, and past that it counts as `svg-unverified`. Web Workers and
  SharedWorkers, recorded by `WORKER_PROBE_JS`. Image URLs beyond the first 200 per page, scan and later checks
  together, count as `images-unverified`. Each of these errs on the safe side: "could be animated" is treated as
  animated.
* *Added 2026-10-25:* **A detection budget.** All the image reads of one capture, by every strategy's scan
  before frame 0 and by the later looks, may take at most `DETECTION_BUDGET_S` = 60 s of real time together
  (`detect.ReadBudget`, shared like the image verdicts of ADR-047). A read gives up when the budget runs out (it
  is given the time left when that is under 5 s), and image URLs not read within it count as uncontrollable:
  `images-unverified: N image URL(s) not checked within the 60 s detection budget`. The reads are silent, and
  without this limit 60 image requests that never answer kept a capture without progress for the server
  watchdog's 300 s (ADR-049). A GIF or SVG whose second, longer read (past the first 64 KB) fails now counts as
  `image-unverified`; it had counted as still, although its first 64 KB end inside the first frame or the
  markup.
* An `<object>` or `<embed>` that shows a same-origin document is not an image, because A drives that document's
  animations like the page's own.
* A budget-only strategy B (no BeginFrameControl) steps aside, as A does, for uncontrollable content that the
  scan before frame 0 finds (`VirtualTimeStrategy.needs_controllable_content`). Forced, it captures anyway,
  reports `deterministic=false` and lists the content in `meta.uncontrollable`, like a forced A. *Amended
  2026-10-25:* a budget-only B also looks again during its frame loop, at A's checkpoints (frame 0, the first
  frame of every captured second and the last frame, `is_checkpoint`) but not at every frame: there it runs the
  page-clock and double-shot checks and asks `ContentWatch` for the full scan plus every image URL the page
  requested since the previous checkpoint (`VirtualTimeStrategy._late_content`). Content found there sends auto
  on to C (the loop stops at once, as A's does) and makes a forced B `deterministic=false`, with the content in
  `meta.uncontrollable`. Content that comes and goes between two checkpoints and leaves no request behind (a
  data: URL image, a WebGL canvas) is not caught; a worker is, because `WORKER_PROBE_JS` records every worker the
  page starts. With BeginFrameControl, B checks frame 0 and the last frame only and does not look again.
* *Added 2026-10-25:* **What one strategy found counts for the next.** Uncontrollable content that a strategy
  given up in auto had found, before frame 0 or during its frame loop, counts as found by the first scan of
  every later strategy of the same capture (`capturer._SharedDetection`, `ContentWatch(carried=...)`), with a
  note when that scan did not see it itself. Before, each attempt scanned afresh. B's frames start at page time
  2 s (its pre-roll, ADR-023), so a GIF that A found at 1.5 s and that the page removed before 2 s was gone at
  B's frame 0, and a budget-only B captured that page as deterministic.

**Consequences.**
* Such pages are captured by strategy C, so a PASS on them becomes INCONCLUSIVE (SPEC §6.4) instead of a PASS that
  rested on frames the real clock moved. SPEC §10 now names animated SVG images and Web Workers.
* A page whose content changes only after frame 0 costs a second capture under the next strategy.
* Tests: capture/tests/test_robustness.py (a late `<img>`, a late CSS background request, a late worker, forced-A
  reporting, a data-URL CSS GIF found by the once-a-second scan, an SVG `<img>`, a worker started at load, a
  same-origin `<object>` driven deterministically) and capture/tests/test_detect.py (the SVG sniffer,
  `test_workers_are_uncontrollable`, `test_images_past_the_cap_count_as_unverified`, the `ContentWatch` cache).
  *Added 2026-10-25:* capture/tests/test_robustness.py pins
  `test_gif_inserted_after_1_5_s_never_ends_in_a_deterministic_capture` (a GIF inserted at 1.5 s, kept or removed
  before B's frame 0: the chain is `waapi_step (failed) -> virtual_time (skipped) -> realtime (used)`),
  `test_budget_only_b_looks_again_at_its_checkpoints` (forced, and B first in auto) and
  `test_image_reads_that_never_answer_stay_within_the_detection_budget`; capture/tests/test_detect.py pins
  `test_image_reads_stop_when_the_detection_budget_is_spent`, `test_gif_whose_longer_read_fails_is_unverified_not_still`
  and `test_first_scan_counts_what_an_earlier_attempt_found`.
* SPEC §6.2 amended; ADR-022 and ADR-023 carry dated notes.

## ADR-047 — Page loading has time limits, and an HTTP error page is not audited (2026-10-25)

**Context.** Three ways a page could stop or fool the capture:
* a subresource that never answers (a stalled image or tracking pixel) holds the `load` event back for ever, and
  the capture waited for it until its own limit and then gave up with INCONCLUSIVE;
* a web font whose server never answers kept `document.fonts.ready` waiting for ever. Playwright 1.63's
  `page.screenshot` also waits for `document.fonts.ready` before every picture, so a capture could hang with no
  end;
* a mistyped URL or a broken deploy answers with an error page (404, 500). That page was audited like any other and
  could PASS with exit 0.

**Decision.**
* Every navigation may take at most 30 s (`NAVIGATION_TIMEOUT_MS`). Strategies A and C wait for
  `DOMContentLoaded`, then give the `load` event at most 10 s of real time (`LOAD_WAIT_MS`); when it does not come,
  the capture goes on and a note says so.
* A and C wait at most 10 s of real time for web fonts (`FONT_WAIT_MS`), racing `document.fonts.ready` against the
  browser's real timer, because A's own timers are frozen. A font still loading after that is noted. A strategy
  whose pictures come from `page.screenshot` (A, flag `pictures_wait_for_fonts`) then gives up the attempt: auto
  falls through, and a forced A returns no frames, with the reason. Inspection takes no screenshots and does not
  give up.
* A main document with HTTP status 400 or above raises `PageLoadError` (`check_main_response`, in A's and C's
  navigation and in B's). The capturer never falls through on it, since every strategy would load the same answer.
  The audit reports it as a capture error: INCONCLUSIVE with the reason "the capture failed (PageLoadError: the page
  answered HTTP 404 ...)" (SPEC §6.4 rule 1), which exits 1, or 0 with `--no-fail`.
* Image reads for detection are limited to 5 s each (`IMAGE_READ_TIMEOUT_MS`), and their results are reused by
  the later strategies of the same capture, so a fall-through does not wait for the same image again.

**Consequences.** One stalled request or font can no longer stop a capture; it costs at most the limits above and
leaves a note. An error page is never reported as PASS. capture/tests/test_robustness.py pins
`test_http_error_status_is_an_error_not_a_page`, `test_image_that_never_answers_does_not_stop_the_capture` and
`test_web_font_that_never_answers_does_not_stop_the_capture`. SPEC §6.2 and §6.4 amended.

## ADR-048 — Realtime capture samples half a compositor tick away from the screencast's frames (amends ADR-029) (2026-10-25)

**Context.** Strategy C resamples a screencast by holding, for each sample time, the latest frame shown at or before
it. ADR-029 left C's grid starting at the first screencast frame. Chromium's compositor draws on the ticks of a
60 Hz clock, so every screencast timestamp lies on one 1/60 s lattice, give or take a millisecond or two. A grid
that starts on the first frame puts every sample on a lattice point, where that small jitter decides whether a
sample shows the frame before or after a change. A 4 Hz page could then measure 5 per second, and a page flashing
just under 3 Hz could fail on one run and pass on the next.
**Decision.** `sample_origin` (capture/strategies/realtime.py) finds where the timestamps sit within a tick
(`lattice_phase`, a circular mean, so phases 0.98 and 0.02 average to 0) and starts the grid half a tick away from
that phase, where a timestamp would have to be off by half a tick to change the result. When the timestamps agree on
no phase (agreement below 0.5, for example arrival times used because Chromium sent no timestamps), the grid starts
a quarter of a sample after the first frame, as strategies A and B do (ADR-029). A note records which origin was
used.
**Consequences.** Realtime rates no longer depend on jitter that small. A compositor frame drawn late or dropped can
still move one transition by a frame, so a realtime rate can be one flash per second higher or lower than the
page's; a note in every realtime capture and SPEC §10 say so. C stays non-deterministic, and its PASS stays
INCONCLUSIVE. capture/tests/test_strategies.py pins the circular mean, the half-tick origin under jitter and the
quarter-frame fallback.

## ADR-049 — A watchdog stops a hung job's browser (2026-10-25)

**Context.** The server runs one job at a time (docs/API.md). Playwright calls such as `page.evaluate` and
`clock.run_for` have no time limit, and a page whose script loops forever never returns from them. Such a job never
reached its next progress callback, so neither DELETE nor a server stop could end it, and the single runner was
blocked for good.
**Decision.** One watchdog thread per task (server/runner.py `Watchdog`). It lists this process's descendants with
POSIX `ps` (server/processes.py) and sends SIGKILL to those at depth 2 or more, which are Chromium below the
Playwright driver, and only to those that appeared after the task started. A killed browser makes the blocked call
raise at once, and the task ends the normal way. It kills:
* 2 s after a cancel (`CANCEL_GRACE_S`; DELETE or server stop), and again on every later check while the task runs;
* after 300 s without a progress callback while such a browser runs (`STALL_LIMIT_S`). The job then ends with
  "The page stopped responding (no progress for 300 s), so its browser was stopped.": an audit as status error, an
  ablate or verify as done with its old report and that sentence in `error`.

Browser-less work (the engine's analysis) always ends and is never timed out. Rejected alternative: a child process
per task, which would break the in-process test fakes and need a progress relay between processes.

*Why 300 s.* Inside a capture the first progress callback comes from a strategy's frame loop, so the waits before
frame 0 of every strategy that steps aside before its loop add up:
* navigation, up to 30 s for each of the three strategies (`NAVIGATION_TIMEOUT_MS`);
* in A and in C, up to 10 s for the `load` event (`LOAD_WAIT_MS`) and up to 10 s for web fonts (`FONT_WAIT_MS`);
* in C, up to 5 s for the screencast's first frame, then the whole recording (the duration, at most 60 s, plus a
  quarter-second settle) before its first callback;
* detection's image reads, which are silent too. Each read may take 5 s (`IMAGE_READ_TIMEOUT_MS`), and a long GIF
  or SVG is read a second time, up to 4 MB. *Amended 2026-10-25:* all the reads of one capture together may take
  at most 60 s of real time (`DETECTION_BUDGET_S`, ADR-046), whether the scan before frame 0 of any strategy makes
  them or the look-again of strategy A between two frames or of a budget-only B at its checkpoints; a read gives
  up when the budget runs out, and image URLs not read within it count as `images-unverified`. The results are
  reused by the later strategies (ADR-047).

That is 3 × 30 + 2 × (10 + 10) + 5 + 60 + 60 s, about 255 s, and 300 s leaves a margin for a slow machine.
(Before the amendment the image reads were not in the sum, which was about 195 s, and had no limit in all.) The
comment above `STALL_LIMIT_S` in server/runner.py states the same sum.

**Consequences.**
* One hostile page can no longer hold the runner and every job behind it.
* On Windows (no `ps` or SIGKILL) nothing is killed and the old behaviour remains: a cancel takes effect at the next
  callback. `flashtrace audit` has no watchdog yet.
* *Known gap, closed 2026-10-25.* A page with many image requests that never answer could reach the 300 s limit
  before frame 0: 60 reads that each waited their full 5 s made 300 s on their own, and the watchdog stopped a
  capture that would have finished. The detection budget bounds those reads at 60 s per capture; the image URLs
  left unread count as `images-unverified`, so such a page is captured by C and a PASS on it is INCONCLUSIVE.
  capture/tests/test_robustness.py pins `test_image_reads_that_never_answer_stay_within_the_detection_budget`.
* server/tests/test_watchdog.py pins the process depth and kill, the watchdog rules at fixed clock times, DELETE,
  the stall limit and shutdown of a task hung in a browser, and a real page that loops forever, and
  `test_stall_limit_covers_every_silent_wait_before_frame_0` computes the sum above from the constants (added
  2026-10-25). ADR-035 and ADR-036 carry dated notes, and docs/API.md describes DELETE and the stall limit.

## ADR-050 — Every CLI invocation ends with the disclaimer (2026-10-25)

**Context.** SPEC §9.3 says every report, HTML export and screen carries the disclaimer "Analysis aid, not a
compliance certification.". Each `flashtrace` command printed it at its normal end, but an error exit (one
`error:` line and exit 2), a Click usage error such as a bad `--attribute` value, `--help` and Ctrl-C ended without
it.
**Decision.** `cli/main.py` uses `DisclaimerGroup`, a subclass of Typer's command group whose `main` prints the
disclaimer on stderr in a `finally` block, so it is the last line printed on every path. The per-command copies at
the end of a run are removed, so an ordinary command prints it once. `demo` and `serve` also print it when they
start, so it stays on screen beside the server banner while they run, and each banner carries one more copy:
demo/serve.py's on stdout (also what `python demo/serve.py`, run directly, prints) and server/app.py's on stderr.
So `demo` and `serve` print it three times.
**Consequences.** No command can forget the disclaimer. Help text (`--help`, and the help shown when `flashtrace`
is run with no arguments) goes to stdout, as Click prints it; the top-level help text contains the disclaimer as
well. Apart from help and the demo server's banner, stdout carries only the JSON report (`audit --json`) or one
fix's CSS (`fix --apply N`), and other human output goes to stderr. cli/tests/test_cli.py pins
`test_every_error_exit_ends_with_the_disclaimer`, `test_demo_prints_the_disclaimer_before_serving_and_after` (the
disclaimer, the server's output, the disclaimer again) and `test_demo_start_error_is_one_line_then_the_disclaimer`.

## ADR-051 — The benchmark names its record by repository path and warns when it cannot be the record (2026-10-25)

**Context.** ADR-041 makes docs/RESULTS.md, with docs/RESULTS.json next to it, the only place for figures. The
committed page had named its JSON and every page's job folder by absolute paths on one machine (a scratch
worktree), so the link was dead in the repository, and nothing warned when a run was made from a tree with
uncommitted changes or wrote its summary outside the repository.
**Decision.**
* bench/run.py names its output files (`resultsMd`, `resultsJson`) and each row's `jobDir` by repository path
  (docs/RESULTS.json); a path outside the repository stays absolute.
* bench/results_md.py links the JSON by file name, with the repository path as the link text, because the JSON
  is always written next to the page.
* `record_warnings` warns, without refusing, when `--out` is outside the repository or the working tree has
  uncommitted changes. The run's own output files are left out of that check and out of the `-dirty` commit
  check, because a summary left over from an earlier run changes no measurement.
* The warnings are printed on stderr before the run starts (even with `--quiet`, so a long run can still be
  stopped), again in the closing summary lines, and in a "Record warnings" row of the Run table ("none" when
  there are none).

**Consequences.** A committed RESULTS.md shows on its face whether it can be the record: its Commit has no `-dirty`
and its Record warnings row says "none". Warnings rather than refusal keep development runs possible.
bench/tests/test_run.py pins `test_summary_names_files_by_repository_path`,
`test_per_page_rows_name_the_job_folder_by_repository_path`,
`test_record_warnings_name_out_outside_repo_and_uncommitted_changes` and
`test_uncommitted_paths_leave_out_this_runs_own_outputs`.
