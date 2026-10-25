# STATUS

One entry per milestone: what passed, what failed, measured numbers or "not measured", open questions.
No number here is an estimate. Test counts come from the runs named in each entry. Accuracy, agreement (including
run-to-run determinism) and runtime figures are not repeated here: they exist only in docs/RESULTS.md, written by
`flashtrace bench` with date and commit (ADR-041). Development timings and determinism counts that earlier
versions of this file quoted were removed on 2026-10-25 for that reason.

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
* Measured: engine suite 223 tests green. Runtime: not recorded here (docs/RESULTS.md only, ADR-041).
* Open: none blocking.

## M2 — capture strategies + determinism (2026-10-24)

* Passed: strategy A (Playwright fake clock + Web Animations seeking) now also drives shadow-root animations,
  SVG SMIL, animations the page restarts, chained animations, page-set phases, `document.timeline` and a seeded
  `Math.random`, and waits for network-loaded media (ADR-022). SPEC §6.3 determinism tests pass for A on the
  CSS, WAAPI and rAF pages. Strategy C (screencast) works and is marked non-deterministic.
* Failed / not possible here: strategy B cannot be deterministic on macOS because Chromium refuses
  BeginFrameControl ("not supported on MacOS yet"); its determinism tests are skipped with that message and auto
  mode uses C for uncontrollable content (ADR-023).
* Checked: two separate captures of bench pages 0001–0003 gave identical frames (a development check, not a figure
  of record; capture/tests/test_determinism.py pins identical repeat captures under A for the three synthetic
  pages). Runtime: not recorded here (docs/RESULTS.md only, ADR-041).
* Open: confirm B on Linux, where BeginFrameControl exists (CI runs on ubuntu-latest).

## M3 — benchmark generator, `flashtrace audit`, first bench (2026-10-24)

* Passed: `flashtrace audit` (exit codes 0/1/2, `--no-fail`, JSON and HTML output, schema validation of every
  report), `flashtrace bench`, `flashtrace demo`; full Python suite 433 passed, 3 skipped, identical outcomes on two
  consecutive runs.
* Fixed during review: the bench's ffmpeg lookup picked a newer Playwright ffmpeg without the VP8 encoder; it now
  checks for `libvpx`.
* Measured: first full 200-page run is in progress in a clean worktree of commit 8674c95; its numbers will appear in
  docs/RESULTS.md only.

### M3 — first full benchmark (2026-10-25)

* Ran: `flashtrace bench --n 200 --seed 1 --duration 10 --workers 4 --determinism-sample 20` in a clean worktree
  of commit 8674c95; the generated docs/RESULTS.md was committed unchanged as the M3 record (commit 0761f45; it is
  in the git history of docs/RESULTS.md, which a later run replaced).
* Passed: clear-case verdict accuracy and violation-type agreement as listed in that M3 docs/RESULTS.md.
* Failed: determinism; some re-run pages had different frame hashes (the Determinism line of that M3 RESULTS.md).
  Root cause found and fixed in ADR-025 (threaded animation under load); capture/tests/test_rendering_switches.py
  now requires four concurrent captures of the marquee page to be identical. The determinism figure after the fix
  is the one in the re-run below, not a development re-run.
* Defect in the record: the M3 RESULTS.md cited its per-page JSON at a scratch path outside the repository, so its
  sha256 could not be checked from a clone. The re-run below keeps docs/RESULTS.json next to docs/RESULTS.md.
* Not measured in this run: attribution accuracy (`--attribute none`; see the re-run below).

The entries for M4–M8 were written on 2026-10-25, after the work had landed (commits 29c5743, 044830e, 49ccb74,
d6e7c34, ca753d4, 0b35e5b). They were missing until then. Their test results come from one run of the Python suite
on a clean worktree of commit 808cd30, which contains all five milestones. That run is recorded in the last entry.

## M4 — attribution stage 1: candidates and bounding-box ranking (2026-10-25, commit 29c5743)

* Passed: candidates are collected at each violation's first event frame from these sources:
  * `getAnimations()` targets, including shadow roots and same-origin frames;
  * `elementsFromPoint` on a 5×5 grid in the window;
  * video, canvas, animated img and iframe elements.

  Each candidate gets a `data-ft-id` tag, a unique selector and a box. Candidates are ranked by intersection over
  union of 4 px cell masks against the violation's cells (ADR-026). Cross-origin iframes are reported as
  unattributable. Tests: capture/tests/test_attribute.py (30 tests, 14 of them in a browser) and
  engine/tests/test_violation_cells.py, both green at 808cd30.
* Found and fixed: plain coverage gave every enclosing ancestor the same score as the culprit (ADR-026). A static
  picture covering the same cells as the culprit tied with it, which the own-motion tie-break and ADR-031 settle.
* Measured: top-1 attribution accuracy is in docs/RESULTS.md (benchmark re-run, last entry).
* Open: canvas and WebGL attribution is element-level only (a listed limitation).

## M5 — attribution stage 2: ablation and the minimal culprit set (2026-10-25, commits 29c5743, ca753d4)

* Passed:
  * up to 6 fresh captures, each with one candidate suppressed by its selector inside the capturer;
  * a greedy minimal set, then pruned; runs memoised by their selector set; everything recorded in
    attribution.json (ADR-027);
  * benchmark scoring of top-1 and minimal-set accuracy on truth geometry, with a separate ablation sample
    (`--ablate-sample`, `--ablate-duration`; ADR-028).

  Tests: the browser tests in capture/tests/test_attribute.py (minimal set; a wrapper gets no credit) and
  bench/tests/test_run.py.
* Found 2026-10-25: ADR-027's consequence paragraph said that suppression also holds an element's descendants. The
  code suppresses the element itself only. The ADR was corrected (ADR-040).
* Measured: minimal-set accuracy on the ablation sample is in docs/RESULTS.md, with its sample size and duration.
* Open: ablation costs about k + 2 captures per page, so the benchmark measures it on a sample, not on all pages.

## M6 — CSS fixes, verification and `flashtrace fix` (2026-10-25, commit 29c5743)

* Passed:
  * fixes derived from measured colours and rates (ADR-030), and which culprits get them (ADR-031);
  * verification by re-capture under the forced original strategy, with companion fixes for minimal sets
    (ADR-032);
  * `flashtrace fix` listing, `--apply N` and `--verify` (ADR-033);
  * fix CSS in the HTML export (ADR-034).

  Tests: capture/tests/test_fix.py (43, 5 of them in a browser), cli/tests/test_fix_cli.py (11, 1 in a browser) and
  cli/tests/test_html.py (12). The browser tests require the rate and luminance fixes on css_keyframes.html to verify.
* Found and fixed during review: a Playwright browser-sharing bug that silently turned in-process audits
  INCONCLUSIVE (AI_USAGE.md). Found the same day, by checking reported rates against pages whose rate is known: two
  sampling errors, fixed in ADR-029 (commit d6e7c34; capture/tests/test_sampling.py pins both pages).
* Measured: no figures. Fix verification is a pass/fail check that the tests pin.
* Open: a fix can only be verified under a deterministic capture. On this Mac, strategy B is never deterministic
  (ADR-023), so a page that needs B or C cannot get a verified fix here. Area fixes are never applied (by design).

## M7 — local server and `flashtrace serve` (2026-10-25, commit 29c5743)

* Passed:
  * the FastAPI app of docs/API.md, serving the API and the static UI from one loopback origin;
  * one FIFO runner thread, with the job directory made at submit time (ADR-035);
  * cancelling and restart recovery (ADR-036);
  * hardening (ADR-037): Host check, Origin check, no CDN docs pages, and validated ids and paths.

  Tests: server/tests (150 tests, 1 of them a browser end-to-end test).
* Found 2026-10-25: docs/API.md did not list the 400 (bad Host) and 403 (foreign Origin) responses that ADR-037
  adds. They are now listed.
* Measured: no figures.
* Open: none blocking.

## M8 — web viewer (2026-10-24, commit 044830e; motion: 49ccb74, 0b35e5b)

* Passed (as committed): the four screens (new audit, running, report, history); a static export served by
  `flashtrace serve`; frame stepping by hand only (previous/next); the disclaimer in a persistent footer. Motion
  follows ADR-024 and its amendment, and tests/test_ui_self_audit.py audits the UI with FlashTrace's own capture and
  engine.
* Not re-run for this note:
  * the web Playwright + axe suite (web/tests, run through npm);
  * the UI self-audit, which needs the built web/out.

  A separate pass on web/ was in progress on 2026-10-25, and web/out must not be rebuilt during it. The clean
  808cd30 worktree has no web/out, so tests/test_ui_self_audit.py skipped there with "web/out is missing".
* Open: run the web suite and the self-audit once the web pass lands, and record their counts here.

## Benchmark re-run and suite check at commit 808cd30 (2026-10-25; the re-run planned for M9)

* Ran in a clean `git worktree` of 808cd30 (no uncommitted changes; the commit in the Run table has no `-dirty`):
  `flashtrace bench --n 200 --seed 1 --duration 10 --fps 60 --workers 4 --determinism-sample 20 --attribute bbox
  --ablate-sample 20 --ablate-duration 4 --out docs/RESULTS.md --work-dir .flashtrace/bench`. The generated
  docs/RESULTS.md and docs/RESULTS.json were copied into the repository unchanged. RESULTS.md now cites the JSON by
  its repository path, with the sha256 that run printed.
* Measured: every figure is in docs/RESULTS.md and nowhere else (ADR-041). That covers clear-case and boundary
  verdicts, violation types, advisories, top-1 attribution on all FAIL pages (bbox), top-1 and minimal-set
  accuracy on the 20-page ablation sample at 4 s, determinism on 20 re-run pages, and runtime. While it ran, the
  machine was also running another full benchmark (from another session) and test suites, so the runtime table
  describes a loaded machine.
* Python suite in the same clean worktree:
  * without the browser marker: 616 passed, 1 skipped;
  * with it: 77 passed, 4 skipped.

  The skips: tests/test_ui_self_audit.py, because the worktree has no web/out (see M8), and three strategy-B
  determinism tests, because Chromium on macOS refuses BeginFrameControl (ADR-023). Nothing failed.
* Not run: the web Playwright + axe suite (see M8).
* Open:
  * Review fixes to engine/, capture/, bench/, cli/ and server/ were being made in the working tree on 2026-10-25,
    after this run. Once they are committed, the benchmark must be run again from a clean worktree of that commit,
    and docs/RESULTS.md and docs/RESULTS.json replaced together. Until then, RESULTS.md describes 808cd30.
  * Strategy B on Linux, where BeginFrameControl exists (from M2).
