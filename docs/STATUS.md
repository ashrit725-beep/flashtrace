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
