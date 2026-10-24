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
