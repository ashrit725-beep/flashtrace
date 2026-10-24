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
