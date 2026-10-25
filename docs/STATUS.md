# STATUS

One entry per milestone: what passed, what failed, measured numbers or "not measured", open questions.
No number here is an estimate; measured figures also live in docs/RESULTS.md with date and commit.

## Step 0 — SPEC written before any code (2026-10-24)

* Passed: docs/SPEC.md written with the W3C passages extracted verbatim from the downloaded WCAG 2.2 and
  Understanding HTML (script: docs/tools/w3c_extract.py). Report schema written. 11 ADRs recorded.
* Correction found: WCAG 2.2 (and the 2025 republication of 2.1) define the red-flash transition with CIE 1976
  UCS chromaticity (Δu′v′ > 0.2), not (R−G−B)×320 > 20 as in the brief's working values (ADR-001).
* Environment: Python 3.14.0 (venv), NumPy 2.5.4, Playwright 1.63.0 + Chromium 1243 (already installed),
  Node 26.7.0, no system ffmpeg (Playwright's bundled ffmpeg n7.0.1 will be used, ADR-010).
* Measured: nothing yet.
* Open questions: whether Chromium's `HeadlessExperimental.beginFrame` is available in the Playwright
  headless shell (decides whether strategy B is more than virtual-time budgets); whether animated GIFs
  follow virtual time (expected: no).

## M1 — engine + unit tests (2026-10-24)

* Passed: `engine/` streams frames through colour → 4×4 cells → per-cell transition tracker → flash pairing →
  window scoring → rate → verdict. All SPEC §5.10 acceptance tests 1–14 pass, plus unit and property tests.
* Found by review and fixed (ADR-018 – ADR-021): a full-screen 30 Hz strobe was reported as PASS and 24 Hz as
  12 per second (event merging); 834 of 2 079 colours with a redness ratio of exactly 0.8 measured just below it;
  black ↔ rgb(1,0,0) noise counted as a red flash; a short viewport shrank the 25% denominator.
* Measured (development laptop, other jobs running): engine suite 223 tests green; a 600-frame 1024×768 probe
  took 6.63 s of wall time. Runtime figures for the record are only those in docs/RESULTS.md.
* Open: none blocking.

## M2 — capture strategies + determinism (2026-10-24)

* Passed: strategy A (Playwright fake clock + Web Animations seeking) now also drives shadow-root animations,
  SVG SMIL, animations the page restarts, chained animations, page-set phases, `document.timeline` and a seeded
  `Math.random`, and waits for network-loaded media (ADR-022). SPEC §6.3 determinism tests pass for A on the
  CSS, WAAPI and rAF pages. Strategy C (screencast) works and is marked non-deterministic.
* Failed / not possible here: strategy B cannot be deterministic on macOS because Chromium refuses
  BeginFrameControl ("not supported on MacOS yet"); its determinism tests are skipped with that message and auto
  mode uses C for uncontrollable content (ADR-023).
* Measured: two separate captures of bench pages 0001–0003 (1.5 s, 90 frames each) gave 90/90 identical frames
  per page; a 2 s capture took 3.93 s of wall time.
* Open: confirm B on Linux, where BeginFrameControl exists (CI runs on ubuntu-latest).

## M3 — benchmark generator, `flashtrace audit`, first bench (2026-10-24)

* Passed: `flashtrace audit` (exit codes 0/1/2, `--no-fail`, JSON and HTML output, schema validation of every
  report), `flashtrace bench`, `flashtrace demo`; full Python suite 433 passed, 3 skipped, identical outcomes on two
  consecutive runs.
* Fixed during review: the bench's ffmpeg lookup picked a newer Playwright ffmpeg without the VP8 encoder; it now
  checks for `libvpx`.
* Measured: first full 200-page run is in progress in a clean worktree of commit 8674c95; its numbers will appear in
  docs/RESULTS.md only.
