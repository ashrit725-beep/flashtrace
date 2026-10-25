# FlashTrace GitHub Action

Runs `flashtrace audit` against a URL (or a file in the checkout) inside a GitHub Actions job, uploads the
JSON/HTML report and the evidence frames as an artifact, and fails the job on FAIL (and on INCONCLUSIVE
unless `fail-on-inconclusive: false`).

The action lives in the `action/` folder of the FlashTrace repository and installs FlashTrace from the
repository root next to it. Refer to it either as `<owner>/flashtrace/action@<ref>`, where `<owner>` is the
GitHub account that hosts your copy of FlashTrace, or as `./action` when your workflow runs inside a
checkout of FlashTrace itself.

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

The page must be reachable from the runner. To audit a site that the same job builds, start it in an earlier
step (for example `npm start &` followed by a wait for its port) and pass `url: http://localhost:<port>/`, or
pass a path to an HTML file in the checkout.

## Inputs

| Input | Default | Meaning |
|---|---|---|
| `url` | (required) | Page to audit: an `http(s)` URL, or a file path inside the checkout. |
| `duration` | `10` | Seconds of animation time to capture. |
| `viewport` | `1024x768` | Viewport as WIDTHxHEIGHT in CSS px; 1024x768 is the WCAG reference size. |
| `fps` | `60` | Frames per second of animation time. |
| `attribute` | `bbox` | Attribution mode: `none`, `bbox` or `ablate` (ablate re-captures the page several times). |
| `fail-on-inconclusive` | `true` | When `true`, an INCONCLUSIVE verdict fails the job, like FAIL. When `false`, the audit runs with `--no-fail`. |
| `report-json` | `flashtrace-report.json` | Where to write the JSON report. |
| `report-html` | `flashtrace-report.html` | Where to write the HTML report. |
| `python-version` | `3.12` | Python version to set up (3.11 or newer). |

## Outputs

* `verdict`: PASS, FAIL or INCONCLUSIVE (ERROR when no report was written).
* `reason`: the one-sentence reason from the report.
* `exit-code`: the exit code of `flashtrace audit`: 0 PASS, 1 FAIL, 2 error. INCONCLUSIVE gives 1 with the
  default `fail-on-inconclusive: true`, and 0 with `fail-on-inconclusive: false` (the audit then runs with
  `--no-fail`). The job fails whenever `exit-code` is not 0.

Notes
* The runner's headless Chromium renders in sRGB; it is not a physical display. See docs/SPEC.md §10.
* Use `attribute: ablate` only when you can afford several re-captures per run.
* Analysis aid, not a compliance certification.
