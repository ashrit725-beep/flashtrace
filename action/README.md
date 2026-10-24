# FlashTrace GitHub Action

Runs `flashtrace audit` against a URL (or a file in the checkout) inside a GitHub Actions job, uploads the
JSON/HTML report and the evidence frames as an artifact, and fails the job on FAIL (and on INCONCLUSIVE
unless `fail-on-inconclusive: false`).

```yaml
jobs:
  flash-audit:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: <owner>/flashtrace/action@main
        with:
          url: https://staging.example.com/
          duration: 10
          attribute: bbox
```

Outputs: `verdict` (PASS / FAIL / INCONCLUSIVE), `reason`, `exit-code` (0 PASS, 1 FAIL or INCONCLUSIVE,
2 error).

Notes
* The runner's headless Chromium renders in sRGB; it is not a physical display. See docs/SPEC.md §10.
* Use `attribute: ablate` only when you can afford several re-captures per run.
* Analysis aid, not a compliance certification.
