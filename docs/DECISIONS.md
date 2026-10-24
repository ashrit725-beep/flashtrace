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

## ADR-007 — Other viewport widths scale the window by the width ratio (2026-10-24)

**Decision.** For a viewport W px wide, the window is `round(341·W/1024) × round(256·W/1024)` px, clamped to
the viewport. Recorded as an assumption in every report whose width ≠ 1024.
**Consequences.** Keeps "25% of a 10° field" roughly proportional on wider screens; it is an approximation
(the Understanding document notes CSS px sizes are already density independent).

## ADR-008 — Verdicts, INCONCLUSIVE and exit codes (2026-10-24)

**Decision.** Engine FAIL ⇒ FAIL under any strategy (an observed flash is real). Engine PASS ⇒ PASS only
under deterministic capture; otherwise INCONCLUSIVE (frames may have been missed). Capture error or fewer
than 2·fps frames ⇒ INCONCLUSIVE. Exit codes 0/1/2; INCONCLUSIVE exits 1 unless `--no-fail`.

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
frame before it. Two regions that each qualify on their own on consecutive frames count as two events.

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
