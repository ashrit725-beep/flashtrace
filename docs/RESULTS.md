# RESULTS

Written by `flashtrace bench` from one run; nothing on this page is typed by hand. Full per-page results: `/private/tmp/claude-501/-Users-ashrittalluri-my-new-project/770b568b-56bd-49be-84a6-40989ae6934b/scratchpad/bench-wt/docs/RESULTS.json`, written by the same run (its sha256 is in the Run table).

## Run

| Field | Value |
|---|---|
| Date (UTC) | 2026-10-25 01:10:10 UTC |
| Commit | 8674c95 |
| Command | `flashtrace bench --n 200 --seed 1 --duration 10 --fps 60 --workers 4 --determinism-sample 20 --attribute none --out /private/tmp/claude-501/-Users-ashrittalluri-my-new-project/770b568b-56bd-49be-84a6-40989ae6934b/scratchpad/bench-wt/docs/RESULTS.md --work-dir /private/tmp/claude-501/-Users-ashrittalluri-my-new-project/770b568b-56bd-49be-84a6-40989ae6934b/scratchpad/bench-wt/.flashtrace/bench` |
| Machine | macOS-26.5.1-arm64-arm-64bit-Mach-O; Python 3.14.0; Chromium 153.0.8010.12; 11 CPUs |
| Per-page results | `/private/tmp/claude-501/-Users-ashrittalluri-my-new-project/770b568b-56bd-49be-84a6-40989ae6934b/scratchpad/bench-wt/docs/RESULTS.json` (sha256 `3bbe7f0a285b076352ab0bc02f21f6351f35e9ef1d91c98c94462ac45e69f4e1`) |

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
| Attribution | none |
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

Matching the expected verdict: 77/78 (98.7%)

|  | got PASS | got FAIL | got INCONCLUSIVE | got ERROR |
|---|---|---|---|---|
| expected PASS | 60 | 1 | 0 | 0 |
| expected FAIL | 0 | 17 | 0 | 0 |

| Boundary reason | Pages | Matching expected |
|---|---|---|
| area fraction within 0.0125 of 0.25 | 55 | 54/55 (98.2%) |
| flash rate within 0.15 Hz of 3 per second | 20 | 19/20 (95.0%) |
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

Agreement: 81/83 (97.6%) (clear pages expected to PASS only)

## Attribution

Not measured (attribution: none)

## Determinism

Determinism: 9/20 compared pages identical on a second run (frame hashes 9/20, report fingerprints 9/20; a fingerprint is the sha256 of the report without capturedAt).

Not compared: 0 of 20 sampled pages (one of the two runs raised an error or its capture produced fewer than two frames).

## Runtime

Wall time per page in seconds (time.perf_counter) for 10 s of animation at 60 fps, 1024x768 px, with up to 4 pages audited at the same time (4 worker processes). Measured at this duration; not scaled to any other. Left out: pages whose audit raised an error (0) and pages whose capture failed, so no animation was recorded and their time is not a capture time (0).

| Stage | Pages | Mean | Median | Min | Max |
|---|---|---|---|---|---|
| Capture | 200 | 26.359 | 26.370 | 20.916 | 30.488 |
| Analysis | 200 | 11.448 | 10.944 | 8.495 | 17.289 |

## Mismatches

| Page | Mechanism | Key parameters | Expected | Got | Reason |
|---|---|---|---|---|---|
| bench-0073 | raf | 3 Hz, area 0.259926, dL 0.302318, red yes, boundary | PASS | FAIL (general, red) | General flashes reached 4 per second in a 341x256 px window at frames 61-120 (red flashes also exceeded the limit); WCAG 2.3.1 allows at most 3. |

Analysis aid, not a compliance certification.
