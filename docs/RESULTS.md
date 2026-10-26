# RESULTS

Written by `flashtrace bench` from one run; nothing on this page is typed by hand. Full per-page results: [`docs/RESULTS.json`](RESULTS.json), written by the same run (its sha256 is in the Run table).

## Run

| Field | Value |
|---|---|
| Date (UTC) | 2026-10-26 03:02:35 UTC |
| Commit | 6a838cc |
| Command | `flashtrace bench --n 200 --seed 1 --duration 10 --fps 60 --workers 4 --determinism-sample 20 --attribute bbox --ablate-sample 20 --ablate-duration 4 --out docs/RESULTS.md --work-dir .flashtrace/bench --quiet` |
| Machine | macOS-26.5.1-arm64-arm-64bit-Mach-O; Python 3.14.0; Chromium 153.0.8010.12; 11 CPUs |
| Per-page results | [`docs/RESULTS.json`](RESULTS.json) (sha256 `be41f016354b02c134daecab8ecbd09db8a2955e013770ea521594cc203018aa`) |
| Record warnings | none |

## Settings

| Setting | Value |
|---|---|
| Pages | 200 (audited 200, skipped 0, errors 0) |
| Seed | 1 |
| Duration per page | 10 s |
| Frame rate | 60 fps |
| Viewport | 1024x768 px |
| Capture strategy | auto |
| Red-flash rule | wcag22 |
| Attribution | bbox |
| Worker processes | 4 used (4 requested) |
| Determinism sample | 20 |
| Truth file | bench/truth.json |

## Verdict accuracy on clear cases

Clear cases are audited pages that are not near any limit. INCONCLUSIVE counts as not correct; pages whose audit raised an error appear in the table but not in the accuracy.

**Accuracy: 122/122 (100.0%)** (0 errors left out)

|  | got PASS | got FAIL | got INCONCLUSIVE | got ERROR |
|---|---|---|---|---|
| expected PASS | 83 | 0 | 0 | 0 |
| expected FAIL | 0 | 39 | 0 | 0 |

## Boundary cases

Pages within a small band of a limit (see bench/truth.py). Reported separately and not part of the accuracy above.

Matching the expected verdict: 78/78 (100.0%)

|  | got PASS | got FAIL | got INCONCLUSIVE | got ERROR |
|---|---|---|---|---|
| expected PASS | 61 | 0 | 0 | 0 |
| expected FAIL | 0 | 17 | 0 | 0 |

| Boundary reason | Pages | Matching expected |
|---|---|---|
| area fraction within 0.0125 of 0.25 | 55 | 55/55 (100.0%) |
| flash rate within 0.15 Hz of 3 per second | 20 | 20/20 (100.0%) |
| video at area 0.24 / 0.26 (chroma subsampling) | 15 | 15/15 (100.0%) |
| video with a luminance step of 0.11 or less (lossy codec) | 25 | 25/25 (100.0%) |

## Accuracy by mechanism (clear cases)

| Mechanism | Correct | Errors (left out) |
|---|---|---|
| canvas | 32/32 (100.0%) | 0 |
| css | 20/20 (100.0%) | 0 |
| raf | 27/27 (100.0%) | 0 |
| video | 8/8 (100.0%) | 0 |
| waapi | 35/35 (100.0%) | 0 |

## Violation types on FAIL pages

Pages expected to FAIL that FlashTrace also failed: do the violation types (general / red) equal the expected types?

Agreement: 56/56 (100.0%)

## Advisory agreement

Only clear pages expected to PASS are scored: the truth marks an advisory only for 2-3 flashes per second, which never fails, and on a failing page the advisory adds nothing.

Agreement: 83/83 (100.0%) (clear pages expected to PASS only)

## Attribution

Main pass, attribution mode bbox, scored on the 56 pages expected to FAIL that FlashTrace also failed.

Top-1 culprit in the truth culprits: 56/56 (100.0%)
Minimal culprit set: needs ablation (attribution: ablate)

Ablation sample: the first 20 pages that FAILed in the main pass were audited again with attribute ablate at 4 s per capture. Sampled 20; scored 20 (the ones that FAILed again at that duration; 0 did not). This is a small sample, not the whole benchmark.

Top-1 culprit in the truth culprits: 20/20 (100.0%)
Minimal culprit set correct: 20/20 (100.0%)
Re-captures per page: mean 3.2, min 1, max 5 (20 pages)
Wall time per sampled audit, baseline and ablation together (s): mean 87.7, median 90.5, max 132.4 (20 pages)
Sampled pages: bench-0003, bench-0005, bench-0006, bench-0008, bench-0014, bench-0025, bench-0026, bench-0030, bench-0033, bench-0039, bench-0040, bench-0050, bench-0055, bench-0058, bench-0062, bench-0068, bench-0072, bench-0075, bench-0076, bench-0079

A minimal set counts as correct when every element marked as in the minimal set is a truth culprit, suppressing them leaves at most 1360 flashing cells of the truth elements (25% of the 85 x 64-cell window) or a rate of 3 per second or less, and putting back any one of them makes the page fail again. It is not compared with the truth list for equality: elements that flash in sync can be explained by more than one minimal set.

## Determinism

Determinism: 20/20 compared pages identical on a second run (frame hashes 20/20, report fingerprints 20/20; a fingerprint is the sha256 of the report without capturedAt).

Not compared: 0 of 20 sampled pages (one of the two runs raised an error or its capture produced fewer than two frames).

## Runtime

Wall time per page in seconds (time.perf_counter) for 10 s of animation at 60 fps, 1024x768 px, with up to 4 pages audited at the same time (4 worker processes). Measured at this duration; not scaled to any other. Left out: pages whose audit raised an error (0) and pages whose capture failed, so no animation was recorded and their time is not a capture time (0).

| Stage | Pages | Mean | Median | Min | Max |
|---|---|---|---|---|---|
| Capture | 200 | 30.852 | 28.320 | 22.311 | 76.960 |
| Analysis | 200 | 17.834 | 16.089 | 12.278 | 51.721 |

## Mismatches

None.

Analysis aid, not a compliance certification.
