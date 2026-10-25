# AI_USAGE.md — disclosure log

The Congressional App Challenge requires full disclosure of AI assistance. This file is a dated log of what
AI assistance did in this repository. It is updated at every milestone. Everything in this repository is
meant to be read, understood and explained line by line by the student; AI output was reviewed against
docs/SPEC.md and the tests before being kept.

Tool: Claude Code (Anthropic, model "Fable 5.1"), run locally from a written build brief authored by the
student. No AI/LLM is used *inside* FlashTrace; the product is fully offline and deterministic.

## 2026-10-24 — Step 0

* AI read the student's build brief and inspected the laptop's toolchain (Python 3.14, Node 26, Playwright
  1.63 with Chromium, no ffmpeg).
* AI downloaded the WCAG 2.2 Recommendation, the Understanding document for SC 2.3.1 and WCAG 2.1, extracted
  the relevant passages programmatically and assembled docs/SPEC.md around them. The interpretation sections
  (§4–§10) were written by AI from the W3C text and the brief; the verbatim blocks were not retyped.
* AI identified that the brief's red-flash working value was the pre-2022 definition and corrected it to the
  WCAG 2.2 wording (ADR-001).
* AI read the open-source EA IRIS code to align conventions (transition counting, extended-failure window)
  and recorded where FlashTrace deliberately differs (ADR-003, ADR-009).
* AI wrote: pyproject.toml, .gitignore, engine/schema/report.schema.json, docs/DECISIONS.md (ADR-001–011),
  docs/STATUS.md, docs/RESULTS.md (placeholder), docs/tools/w3c_extract.py, this file, README stub.
* AI will continue to write code, tests and docs for milestones M1–M9; each milestone appends an entry here
  naming the files produced and what was verified by running tests.

## 2026-10-24 — Demo site, benchmark generator, GitHub Action, CI

* An AI agent built `demo/` (interstitial page, the "Courtside Live" test page with a 4 Hz red LIVE badge,
  a 5 Hz strobing loader and a 0.5 Hz fade, and a loopback-only server) to behaviours the student specified
  (ids, rates, sizes, colours). It verified the page headlessly with Playwright: three animations with the
  expected durations, the pause button, `prefers-reduced-motion`, zero console errors.
* An AI agent built `bench/` (seeded generator of 200 synthetic pages with exact cell-based truth, WebM videos
  through Playwright's bundled ffmpeg). It found that the brief's WAAPI recipe never flashes (ADR-016) and that
  the bundled ffmpeg has no raw-video input (ADR-015). Determinism of the generated files was checked by
  hashing two full runs.
* AI wrote `action/action.yml`, `action/README.md`, `.github/workflows/ci.yml` and `tests/test_repo_rules.py`
  (the rule test caught one explanatory sentence in the demo that used the forbidden phrase; it was reworded).

## 2026-10-24 — M1 engine and M2 capture

* Engine: one AI agent implemented `engine/` from a fixed module contract while two other AI agents, who could
  not see the implementation, wrote the unit tests and the SPEC §5.10 acceptance tests from the same contract.
  A fourth agent ran the suite and arbitrated disagreements by the SPEC (three tests were corrected: two called a
  method as a property, one used 0.5 − 0.4, which is 0.09999999999999998 in floating point).
* Capture: AI agents built strategy A (Playwright fake clock + Web Animations seeking), then B (CDP virtual time)
  and C (screencast). They measured, rather than assumed, how Chromium behaves on this laptop (ADR-022, ADR-023).
* Review: four AI reviewers (SPEC fidelity, numerical, clarity, capture) reported 43 findings. The verification
  step ran out of usage credits, so the orchestrating AI re-checked the high-severity claims itself with small
  experiments: a full-screen 30 Hz strobe was reported as PASS, 834 of 2 079 colours with a redness ratio of
  exactly 0.8 were measured as 0.7999999999999999, shadow-DOM animations were invisible, and SVG animations ran
  on the real clock. The decisions were recorded as SPEC amendments and ADR-018 – ADR-023 before any fix.
* The student's role: wrote the brief and the thresholds to check, decides which decisions to keep, and must be
  able to explain each engine function (docs/ENGINE.md is written for that purpose).

## 2026-10-24 – 2026-10-25 — M3 to M8, motion pass, determinism investigations

* **M3 (CLI, report, bench runner).** AI agents wrote `cli/` (audit, fix, bench, demo, serve commands; report
  assembly with schema validation; the self-contained HTML export) and the bench runner and scorer. Two reviewers and
  one verifier per finding checked them; confirmed findings were fixed with tests.
* **First full benchmark.** The orchestrating AI ran `flashtrace bench` on 200 pages in a clean git worktree so the
  recorded commit is exact. It found that only 9 of 20 re-runs were identical. The AI then ran its own experiments
  (frame diffs, concurrent captures, Chromium switch sets) and traced the cause to Chromium's threaded animation
  under machine load (ADR-025). The fix was validated at benchmark scale (20 of 20 identical) before it was kept.
* **Measurement accuracy.** The AI noticed that the demo's 5 Hz strobe was reported as 6 per second, traced it to
  floating-point state switches exactly at sample instants and to Playwright's 16 ms animation-frame timer, and
  changed sampling and animation-frame handling (ADR-029). An exact 3 Hz benchmark page that had failed now passes.
* **M4–M6 (attribution, ablation, fixes).** AI agents built candidate collection, overlap ranking, ablation with a
  minimal culprit set, CSS fix generation from measured colours and rates, and fix verification by re-capture.
  Reviews found and fixed, among others, a Playwright browser-sharing bug that silently turned audits INCONCLUSIVE.
* **M8 (server, viewer).** AI agents built the FastAPI server and the Next.js viewer against `docs/API.md`, with
  Playwright + axe-core tests (0 violations on every screen and state).
* **Motion and look-and-feel (student request).** The student asked for reactive components, transitions and a
  better look and feel using their installed motion and component skills. AI agents wrote a motion spec, had it
  critiqued for photosensitivity safety, implemented it with the Motion library, and built a self-audit in which
  FlashTrace's own capture and engine measure the UI. That self-audit caught real problems (for example a History
  delete at phone width counted as flashing) which were fixed before the work was kept (ADR-024 amendment).
* **What the AI did not do.** It did not type any number into docs/RESULTS.md; that file is written by
  `flashtrace bench`. It did not change a WCAG threshold; corrections to the brief's working values are ADRs.

## 2026-10-25 — Documentation review fixes and benchmark re-run

* AI reviewers compared the docs with the code and the brief. The confirmed findings were fixed in the docs only:
  * SPEC §5.4, §5.6 step 4, §5.8 and §5.10 (items 15–16), §6.4 and §7 now say what the code does. ADR-038 to
    ADR-041 record the decisions, and ADR-008, 013, 019, 025, 027, 029, 030, 031 and 032 carry dated corrections.
  * docs/API.md lists the 400 (bad Host) and 403 (foreign Origin) responses.
  * docs/STATUS.md has the missing M4–M8 entries.
  * README.md describes determinism as measured and has a CI example that starts the site first.
  * action/README.md lists the inputs and the exit code for INCONCLUSIVE.
  * demo/README.md no longer says the CLI is still to come.
* The AI ran `flashtrace bench` in a clean git worktree of commit 808cd30 and copied the generated docs/RESULTS.md and
  docs/RESULTS.json into the repository unchanged. It typed no number into either file. It also ran the Python test
  suite in that worktree for the STATUS entry.
* Correction to the entry "M3 to M8, motion pass, determinism investigations" above: the parenthesised
  benchmark-scale count after the ADR-025 fix came from a development re-run that `flashtrace bench` did not
  produce, so it is not a figure of record (ADR-041). The count before the fix is the Determinism line of the M3
  docs/RESULTS.md (commit 0761f45). The determinism figure of record is the Determinism line of the current
  docs/RESULTS.md. That entry stays as written because this file is append-only.
