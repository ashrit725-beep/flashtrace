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
