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

**Context.** The first full benchmark (commit 8674c95, 200 pages, 4 worker processes) found that only 9 of 20
pages gave identical frame hashes on a second run, although every capture reported `deterministic`. Every
differing page contained the scrolling-marquee distractor; every page without it was identical. Frames kept from
four simultaneous captures showed no shift in the text: a single 7 px-wide slice of newly revealed text was drawn
differently and then moved with the layer. Measured with four concurrent 10 s captures of that page: software
rendering alone (`--disable-gpu`, `--disable-gpu-compositing`, `--disable-gpu-rasterization`) fixed sequential runs
but not concurrent ones (a 20-page benchmark under load: 10/20 identical); larger tiles or disabling partial raster
alone did not fix it; **every switch set containing `--disable-threaded-animation` gave four identical runs**.
Strategy A pauses and seeks animations from the page; with threaded animation the compositor thread keeps its own
copy of a transform animation, and under load its raster timing leaks into the image.
**Decision.** `capture.capturer.launch_chromium` starts every Chromium FlashTrace uses with
`--disable-gpu --disable-gpu-compositing --disable-gpu-rasterization --disable-threaded-animation
--disable-partial-raster` (partial raster off so a tile never depends on its previous contents). Strategy-specific
switches are appended.
**Consequences.** Measured after the change: the same 20-page benchmark under 4 workers gave 20/20 identical pages,
and a new browser test captures a marquee page four times at once and requires identical hashes. Rendering is
done on the CPU, which also removes GPU differences between machines. Frame hashes differ from those recorded
before this ADR (expected; the full benchmark is re-run).

## ADR-026 — Attribution stage 1 ranks candidates by intersection over union of 4 px cell masks (2026-10-25)

**Context.** SPEC 7 asks for candidates ranked by how much each one overlaps the failing cells. If the score were plain coverage (the share of flashing cells inside the box), every ancestor that encloses the culprit would score 100%, the same as the culprit itself: <main>, wrapper divs, full-page overlays. Candidates also have to be collected with the page in exactly the state of the captured frame.
**Decision.** The engine adds AnalysisResult.violation_cells: for each violation, one bool [cells_y, cells_x] mask that is the union of the event masks E[t] = M[t-1] | M[t] over the violation's event frames. It is a by-product only and is not written by to_dict(). capture/inspect.py reloads the page with the capturer's own per-attempt setup (_run_attempt: init scripts, fake clock, interaction script). InspectStrategy is a copy of strategy A's frame loop that stops at each violation's first event frame and collects: getAnimations() targets (document, recorded shadow roots, same-origin frames), elementsFromPoint on a 5x5 grid inside the window, and video, canvas, animated img (detected by capture/detect.py) and iframe elements. html and body are excluded, and a cross-origin iframe becomes nodeType 'iframe (cross-origin, unattributable)'. Candidates are tagged data-ft-id='ft-<n>' in document order. Each gets a selector: #id when that id is unique, otherwise a tag:nth-of-type path from the nearest ancestor with a unique id. A selector that does not match exactly one element is recorded in attribution.json selectorWarnings. Each rect is turned into the cells it touches; a partly covered cell counts, because the engine averages all 16 pixels of a cell. overlap = |box AND flashing| / |box OR flashing|. Ranking: overlap descending, then smaller box area, then an element that can flash by itself (it has an animation or is self-painting media), then document order. With several violations, each selector keeps its best overlap. Candidates with overlap 0 are dropped.
**Consequences.** An enclosing ancestor scores low because most of its area does not flash; on the demo, #main scores 0.18 against 0.92 for #loader. Elements whose boxes cover exactly the same cells (the culprit and a static svg on top of it) tie on overlap, and geometry alone cannot separate them; the smaller-box and own-motion tie-breaks and stage 2 do that. The own-motion tie-break is an addition to the brief's 'smaller area, then document order'.

## ADR-027 — Ablation by selector in fresh captures, greedy then pruned minimal set, recorded in attribution.json (2026-10-25)

**Context.** ADR-013 requires suppression inside the capturer. Because each re-capture is a fresh page load, data-ft-id tags from the inspection page do not exist there. Re-captures are expensive (each one is a full capture plus an engine run), and the brief bounds them at k <= 6 single suppressions plus the greedy steps.
**Decision.** Each re-capture calls capture(..., suppress_selectors=[...]) with the unique selectors from stage 1, the audit's viewport, fps, duration and interaction script, and the strategy the baseline actually used (meta.strategy). It is analysed with the audit's red rule. ablationDelta = (qualifying general + red events of the baseline) - (the same count in the re-capture); a re-capture that raises gets ablationDelta null and is ordered last. The greedy order is ablationDelta descending, ties broken by stage-1 rank. Selectors are added until the suppressed set PASSes, then each member is removed again if the page still passes without it, which leaves a minimal set. Runs are memoised by their sorted selector set, so no set is captured twice. When even all top-k together fail, no culprit is marked and attribution.json limitations gets 'attribution: no minimal set within the top 6 candidates', which cli/report.py copies into the report. Re-capture frames go to <job>/ablation/<n>/frames and are deleted right after analysis; attribution.json keeps each run's selectors, verdict, qualifying counts, determinism, strategy, frame hash and frame count, plus the recapture count. Any failure during inspection or ablation is written to attribution.json 'error', and the culprits found so far are returned, so the audit's verdict is never lost.
**Consequences.** Suppressing an element also holds the animations of its descendants and hides media inside it (capture's ftIsSuppressed looks at ancestors). So enclosing ancestors also show a positive ablationDelta. On the demo, section, div, article and main all score the same delta as #loader. The minimal set is still correct, because ties go to the higher stage-1 rank, but ablationDelta on its own is not evidence that an element flashes by itself. Disk use stays bounded; the hashes still let a run be compared later.

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
for frame. Every frame hash changes once (expected). Reports still label frame `i` as `t = i / fps`, the start of its
slot.

## ADR-030 — How fixes are derived from measured data (capture/fix.py) (2026-10-25)

Context: SPEC 7 asks for at most 3 CSS fixes per culprit, built from measured values. Decision: Each culprit's two states are measured from the job's frames: the per-pixel median colour inside the culprit's box, on a qualifying event frame f and on frame f-1. Of the first 12 event frames of the violations the culprit takes part in, the pair with the largest linear-light difference is kept. The engine's colour functions turn each state into L and R/(R+G+B). Targets, each a margin below the W3C wording: rate 2.5 flashes per second, below 'more than three times in any one second period'; flashes 0.4 s apart put at most 3 in any 1 s window. Luminance: a difference of 0.09, below '10% or more'. Red: a ratio of 0.75 for CSS colours, below 'greater than or equal to 0.8'. Area: 95% of the largest non-qualifying count, floor(25% of the full window) = 1360 cells at 1024 px. Rate: D' = ceil(r x D / 1000 x 1000 / 2.5), proposed for CSS animations and Web Animations with a finite duration; for Web Animations the rationale says CSS may not override animate() timing. No rate fix for transitions, script, canvas or video, and the first fix's rationale says why. Luminance and red for CSS animations: a new @keyframes ft-fix-<rank>-lum|red copies the inspected keyframes. For luminance, every opaque colour brighter than darkest + 0.09 is scaled in linear light, which keeps chromaticity; the scale is found by bisection on the whole-number colour. For red, each saturated red is mixed in linear light with the grey of equal luminance until its ratio is <= 0.75. animation-name is then overridden. For other mechanisms (and for see-through keyframe colours): filter: contrast(c) or saturate(s), using the Filter Effects Level 1 formulas on sRGB-encoded values, rounded to bytes. Each amount is the largest one, to 3 decimals, that meets the target. The SPEC's 'or opacity' is not used, because the result of opacity depends on what is behind the element. Order: rate, luminance, red, area, cut to 3. Consequences: every number in a fix and its rationale comes from this audit, and the rationale quotes the threshold it targets. SPEC 7 amendment: 'luminance (difference <= 0.09: brighter keyframe colours lowered in linear light, keeping the hue, or a contrast() filter for other mechanisms)'.

## ADR-031 — Which culprits get fixes (2026-10-25)

After ablation: the minimal set only. After bbox ranking: culprits with overlap >= 0.5; among those, the ones that move by themselves (they have an animation, or are video, canvas, iframe or img) are kept; if none moves by itself, only the top-ranked one. At most 3 culprits. Context, measured: on the demo, #loader, the static court <svg> under it and the panel <section> all scored IoU 0.923. Without the tie-break, the svg and section each received a contrast() filter and an area fix that point at the wrong element.

## ADR-032 — How fixes are verified (2026-10-25)

verify_fix injects the fix CSS as a <style> element (capture extra_css) and re-captures with the job's settings: duration, viewport, fps, red rule, and the interaction script copied to <job>/script.js at audit time. The original capture's strategy is forced, so fall-through cannot change it. The frames go to <job>/verify/fix-<n>/frames, and the engine runs on them. 'after' is a schema measurement whose verdict follows the SPEC 6.4 escalation. verified = after.verdict == PASS and after.deterministic. Area fixes are never applied: after stays null, verified stays false, and a note is added to the rationale. A failed re-capture gives an INCONCLUSIVE 'after' and the error is appended to the rationale, because the schema's fix object has no notes field. Measured: on css_keyframes.html (3 s), the rate fix (400 ms) gave PASS with general 3/s and 7 qualifying, against 4/s and 11 before; the luminance fix (#ffffff to #545454) gave PASS.

## ADR-033 — CLI and job metadata for M6/M7 (2026-10-25)

`flashtrace audit --attribute` now defaults to bbox; bench keeps none. meta.json keeps the settings with an absolute script path, plus jobDir and reportCopies (the --out paths); the server's adopt_run already rewrites jobDir. `flashtrace fix REPORT` lists the fixes numbered from 1 (the HTTP API counts from 0). `--apply N` prints fix N's CSS on stdout. `--apply N --verify` finds the job in this order: a report.json next to meta.json; a meta.json whose reportCopies lists the file; a job under --work-dir whose report has the same url, capturedAt and capture hash. It then runs verify_fix_in_job, updates the given copy too, prints before and after, and exits 0 if verified, 1 if not (area fixes always 1), 2 on error. Progress: a progress callable that also has phase(label, done=None, total=None) is told 'attributing (bbox)', 'attributing 3/6', 'proposing fixes' and 'verifying'. The CLI prints these on stderr. `serve` lazily imports server.app.serve(port, work_dir, allow_local_files).

## ADR-034 — In the HTML export, fix CSS is shown in <code> with '@' written as the entity &#64; (2026-10-25)

It looks identical, but the page source never contains '@keyframes'. The plain-text check that the report page declares no animation (SPEC 9.1, test_html) therefore stays meaningful even when a luminance fix is listed.

## ADR-035 — Local server: job directory made at submit time, audit built in <job>/run/, one worker thread (2026-10-24)

Context: POST /jobs must return a stable id at once, but cli.audit.run_audit always creates its own time-stamped job directory, and the brief allows only one audit at a time. Decision: POST /jobs creates the job directory right away with cli.jobs.new_job_dir and writes meta.json with status queued and source server. The single runner thread later calls run_audit(work_dir=<job>/run), which builds <job>/run/jobs/<audit id>/. When it finishes, server.jobs.adopt_run moves every entry up into <job>/ and merges meta.json. The audit's fields are kept, the job's own jobId, createdAt and source win, and auditJobId plus a corrected jobDir are recorded. Tasks (audit, ablate -> cli.audit.reattribute, verify -> cli.audit.verify_fix_in_job) share one FIFO deque behind a threading.Condition. Any exception ends a task with status error and one sentence such as 'The audit failed (TimeoutError: ...).'. The in-memory index is rebuilt from every readable meta.json at start; a folder without a readable meta.json is left out. Consequences: server jobs and flashtrace audit jobs share one history in the same layout, so contract items 4 and 5 work on server jobs. Ablation and verification keep a single phase for their whole run, as the UI's stepper expects. A follow-up task that fails sets status error, but the report stays readable, because it existed before the failure. After an ablation the summary's settings.attribute follows report.settings.attribute.

## ADR-036 — Cancelling and restart recovery (2026-10-24)

Decision: the runner passes run_audit a JobProgress object. It is called once per captured frame, and cli.audit calls its phase(label, done, total) hook between steps; reattribute and verify_fix_in_job get it too when their signature accepts progress. Once DELETE has set the job's cancel flag, both entry points raise JobCancelled. JobCancelled derives from BaseException, so run_audit's handler that turns capture failures into an INCONCLUSIVE report (it catches Exception) cannot swallow it, and the capture's finally blocks close the context and the browser. DELETE removes the job from the index at once and returns 204. A queued task is dropped and its directory deleted immediately. A running task's directory is deleted by the runner after the task stops, never while it is still writing. A step that never calls back finishes first. On shutdown, the lifespan stops the runner and the running job ends as error 'The server stopped during the job.'. On the next start, any job still marked running becomes error with that message, and any job still queued becomes error 'The server stopped before the job started.'. Its half-built <job>/run/ folder is removed and its report, if any, is kept. Consequences: cancel takes effect within one frame or one attribution step. A flashtrace audit running into the same work directory while the server starts would be shown as error until the next restart.

## ADR-037 — Server hardening beyond docs/API.md (2026-10-24)

Context: a loopback-only server can still be reached by web pages through DNS rebinding and cross-site form POSTs. Decision: (1) serve() refuses any host but 127.0.0.1. (2) TrustedHostMiddleware accepts only the Host names 127.0.0.1 and localhost, and answers anything else with 400. (3) A POST or DELETE carrying an Origin header that is not the server's own origin (or, with FLASHTRACE_DEV_CORS=1, http://localhost:3000 or http://127.0.0.1:3000) gets 403. Requests without an Origin header, such as curl, are allowed. (4) CORS is off unless FLASHTRACE_DEV_CORS=1. (5) URLs with a user name or password are refused with 422, because the URL is stored in meta.json and the report. (6) Swagger and ReDoc are disabled because they load scripts from a CDN; /openapi.json stays. (7) A malformed job id returns 422; a frame index must be a whole number, and anything outside 0..frameCount-1 returns 404, so a client never names a path. (8) Relative local paths are resolved against the project directory, and symlinks are resolved before the inside check. The outside check runs before the existence check, so the error never reveals whether an outside file exists. Consequences: two extra status codes (400 bad Host, 403 foreign Origin) that docs/API.md should list.

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
