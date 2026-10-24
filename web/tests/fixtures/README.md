# Test fixtures for the web UI — TEST FIXTURE DATA

**These files are synthetic test data, not measurements.** They exist only so the
Playwright tests in `web/tests/` can render every screen without a running server.

| File | What it is |
|---|---|
| `report-fail.json` | FAIL report. The analysis fields come from the real `engine.analyze()` run on 600 synthetic 1024x768 frames (a 4 Hz black/white 420x300 box at (25, 287) and a 4 Hz red/grey 96x32 badge). The `culprits` and `fixes` are hand-written, fake-but-plausible placeholders. |
| `report-pass.json` | PASS report: the same synthetic page with nothing moving, attribution mode `none`. |
| `report-inconclusive.json` | INCONCLUSIVE report: the PASS analysis with a hand-written real-time (non-deterministic) capture block. |
| `jobs.json` | Five job summaries (error, running, and three done jobs) shaped like `GET /jobs` in docs/API.md. |
| `frame-a.png`, `frame-b.png` | The two states of the synthetic flashing page (served for every frame index of the FAIL job). |
| `frame-still.png` | The still page (served for the PASS and INCONCLUSIVE jobs). |

Nothing here was captured from a real web page, and no number in these files is a
FlashTrace accuracy, agreement or runtime figure (those live only in docs/RESULTS.md).

Regenerate from the repository root (validates every report against
`engine/schema/report.schema.json`):

```sh
.venv/bin/python web/tests/fixtures/make_fixture.py
```

Analysis aid, not a compliance certification.
