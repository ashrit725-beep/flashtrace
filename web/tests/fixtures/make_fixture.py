"""Generate the web UI's test fixtures from the REAL FlashTrace engine.

TEST FIXTURE DATA. Everything written here is synthetic: frames are drawn with
NumPy, not captured from a browser, and the culprits/fixes are hand-written
placeholders (the attribution code is not part of this script). Only the
analysis fields (verdict, reason, violations, advisory, timeline, heatmap,
windowPx, frame counts) come from engine.analyze() run on those synthetic
frames. These files are test data for web/tests, not measurements.

Run from the repository root:
    .venv/bin/python web/tests/fixtures/make_fixture.py

Writes (next to this file): report-fail.json, report-pass.json,
report-inconclusive.json, jobs.json, frame-a.png, frame-b.png, frame-still.png.
Every report is validated against engine/schema/report.schema.json.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import numpy as np
from jsonschema import Draft202012Validator
from PIL import Image

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from engine import ENGINE_VERSION, analyze, evidence_frame_indices  # noqa: E402

OUT = Path(__file__).resolve().parent
SCHEMA = json.loads((ROOT / "engine" / "schema" / "report.schema.json").read_text())

W, H, FPS, DURATION = 1024, 768, 60, 10
FRAMES = FPS * DURATION

# Geometry mirrors the demo page (demo/site): a large strobing box and a small badge.
BOX = (25, 287, 420, 300)      # x, y, w, h
BADGE = (880, 14, 96, 32)

LIMITATIONS = [
    "Headless sRGB rendering is not a physical display; brightness, gamma and viewing distance differ per user.",
    "Canvas/WebGL attribution is element-level only (what is drawn inside cannot be attributed).",
    "Cross-origin iframes are unattributable and cannot be clock-controlled.",
    "Animated GIF/WebP/APNG force non-deterministic capture (strategy C) and a PASS becomes INCONCLUSIVE.",
    "The fine-pattern exception (T6) and spatial-pattern hazards are not evaluated.",
    "Only the captured viewport and duration are analysed; content revealed by scrolling or interaction is "
    "analysed only if an interaction script drives it.",
    "Window size for viewports other than 1024 px wide is scaled by the width ratio (an assumption).",
    "No numbers in this repository are estimates: accuracy, agreement and runtime figures exist only in "
    "docs/RESULTS.md, produced by flashtrace bench, with date and commit hash.",
]

ASSUMPTIONS = [
    "TEST FIXTURE: frames are synthetic NumPy images, not a browser capture.",
    "Frame i is at i / 60 s of animation time.",
    "The 341 x 256 px window corresponds to a 1024 px wide viewport viewed at a typical distance.",
    "Red flashes use the WCAG 2.2 red-flash definition (redRule wcag22).",
]


def page(box_on: bool | None, badge_red: bool | None) -> np.ndarray:
    """One 1024x768 RGB frame of a plain 'page'. None = element absent/still."""
    img = np.full((H, W, 3), (246, 243, 236), dtype=np.uint8)
    img[0:60, :] = (23, 22, 19)                       # masthead
    for row in range(110, 250, 28):                   # text-like grey bars
        img[row:row + 10, 25:25 + 600 - (row % 3) * 80] = (190, 184, 170)
    img[287:587, 480:1000] = (235, 230, 217)          # sidebar panel
    if box_on is not None:
        x, y, w, h = BOX
        img[y:y + h, x:x + w] = (255, 255, 255) if box_on else (20, 20, 20)
    if badge_red is not None:
        x, y, w, h = BADGE
        img[y:y + h, x:x + w] = (255, 0, 0) if badge_red else (40, 40, 40)
    return img


def state_at(i: int) -> int:
    """4 Hz square wave at 60 fps: 8 half-periods per second."""
    return (i * 8 // FPS) % 2


def sha(a: np.ndarray) -> str:
    return hashlib.sha256(a.tobytes()).hexdigest()


def run(frame_for) -> tuple[dict, object, list[str]]:
    hashes: list[str] = []

    def gen():
        for i in range(FRAMES):
            f = frame_for(i)
            hashes.append(sha(f))
            yield f

    result = analyze(gen(), fps=FPS, viewport=(W, H), red_rule="wcag22")
    return result.to_dict(), result, hashes


def measurement(a: dict, verdict: str | None = None) -> dict:
    return {
        "verdict": verdict or a["verdict"],
        "generalMaxRatePerSec": a["generalMaxRatePerSec"],
        "redMaxRatePerSec": a["redMaxRatePerSec"],
        "qualifyingGeneral": len(a["qualifyingGeneralFrames"]),
        "qualifyingRed": len(a["qualifyingRedFrames"]),
        "deterministic": True,
    }


def report(a: dict, *, url: str, captured_at: str, attribute: str, capture: dict,
           verdict: str | None = None, reason: str | None = None,
           culprits: list | None = None, fixes: list | None = None, evidence: list) -> dict:
    return {
        "version": "1.0",
        "url": url,
        "capturedAt": captured_at,
        "engineVersion": ENGINE_VERSION,
        "settings": {
            "duration": DURATION,
            "viewport": {"width": W, "height": H},
            "fps": FPS,
            "redRule": a["redRule"],
            "attribute": attribute,
            "windowPx": a["windowPx"],
            "cellPx": a["heatmap"]["cellSize"],
        },
        "capture": capture,
        "verdict": verdict or a["verdict"],
        "reason": reason or a["reason"],
        "violations": a["violations"],
        "advisory": a["advisory"],
        "timeline": a["timeline"],
        "heatmap": a["heatmap"],
        "culprits": culprits or [],
        "fixes": fixes or [],
        "evidenceFrames": evidence,
        "limitations": LIMITATIONS,
        "assumptions": ASSUMPTIONS,
        "disclaimer": "Analysis aid, not a compliance certification.",
    }


def capture_block(hashes: list[str], strategy: str, deterministic: bool, *,
                  fallback: str | None = None, uncontrollable: list | None = None) -> dict:
    return {
        "strategy": strategy,
        "deterministic": deterministic,
        "frameCount": len(hashes),
        "hash": hashlib.sha256("".join(hashes).encode()).hexdigest(),
        "frameHashes": hashes,
        "fallbackReason": fallback,
        "uncontrollable": uncontrollable or [],
        "notes": ["TEST FIXTURE: synthetic frames generated by web/tests/fixtures/make_fixture.py."],
    }


def evidence_for(result, a: dict) -> list[dict]:
    first_events = {}
    for n, v in enumerate(a["violations"], start=1):
        first = v["frameIndices"][0] if v["frameIndices"] else v["start"]
        first_events.setdefault(first, (n, v["type"]))
    out = []
    for idx in evidence_frame_indices(result):
        if idx == 0:
            label = "Page at rest (frame 0)"
        elif idx in first_events:
            n, kind = first_events[idx]
            label = f"First qualifying {kind} flash of violation {n}"
        elif idx + 1 in first_events:
            label = "Frame just before that flash event"
        else:
            label = f"Evidence frame {idx}"
        out.append({"index": idx, "pngPath": f"frames/{idx:06d}.png", "label": label})
    return out


def main() -> None:
    validator = Draft202012Validator(SCHEMA)
    frame_a, frame_b = page(True, True), page(False, False)
    still = page(True, None)

    # ---- FAIL: 4 Hz 420x300 black/white box + 4 Hz 96x32 red/grey badge -----
    a_fail, r_fail, h_fail = run(lambda i: frame_a if state_at(i) == 0 else frame_b)
    before = measurement(a_fail)
    # bbox mode: ablation did not run, so there is no minimal set yet. inMinimalSet is
    # meaningful only when ablationDelta is non-null, hence False on every culprit here.
    culprits = [
        {"rank": 1, "selector": "#loader", "nodeType": "DIV", "ftId": "ft-12",
         "rect": {"x": 25.0, "y": 287.0, "w": 420.0, "h": 300.0},
         "animation": {"name": "loader-strobe", "duration": 250.0, "keyframes": [
             {"offset": 0, "backgroundColor": "rgb(255, 255, 255)"},
             {"offset": 0.5, "backgroundColor": "rgb(20, 20, 20)"},
             {"offset": 1, "backgroundColor": "rgb(255, 255, 255)"}]},
         "overlap": 0.98, "ablationDelta": None, "inMinimalSet": False},
        {"rank": 2, "selector": "#live-badge", "nodeType": "SPAN", "ftId": "ft-7",
         "rect": {"x": 880.0, "y": 14.0, "w": 96.0, "h": 32.0},
         "animation": {"name": "live-pulse", "duration": 250.0, "keyframes": [
             {"offset": 0, "backgroundColor": "rgb(255, 0, 0)"},
             {"offset": 0.5, "backgroundColor": "rgb(40, 40, 40)"},
             {"offset": 1, "backgroundColor": "rgb(255, 0, 0)"}]},
         "overlap": 0.02, "ablationDelta": None, "inMinimalSet": False},
        {"rank": 3, "selector": "main > section.scores", "nodeType": "SECTION", "ftId": None,
         "rect": {"x": 0.0, "y": 270.0, "w": 1024.0, "h": 340.0},
         "animation": None, "overlap": 0.36, "ablationDelta": None, "inMinimalSet": False},
    ]
    fixes = [
        {"culprit": 1, "kind": "rate",
         "css": "#loader {\n  animation-duration: 1s !important;\n}",
         "rationale": "Slows the strobe from 4 cycles per second to 1, so it can produce at most 1 flash per second.",
         "before": before, "after": None, "verified": False},
        {"culprit": 1, "kind": "luminance",
         "css": ("#loader {\n  animation-name: loader-strobe-soft !important;\n}\n"
                 "@keyframes loader-strobe-soft {\n  0%, 100% { background-color: rgb(255, 255, 255); }\n"
                 "  50% { background-color: rgb(235, 235, 235); }\n}"),
         "rationale": "Keeps the rhythm but holds both states above 0.80 relative luminance, so no transition counts as a general flash.",
         "before": before,
         "after": {"verdict": "PASS", "generalMaxRatePerSec": 0, "redMaxRatePerSec": 0,
                   "qualifyingGeneral": 0, "qualifyingRed": 0, "deterministic": True},
         "verified": True},
        {"culprit": 1, "kind": "area",
         "css": "/* Not auto-applied: an area fix changes layout. */\n#loader {\n  max-width: 160px;\n  max-height: 120px;\n}",
         "rationale": "Shrinks the box below 25% of a 341 x 256 px window, so its flashes no longer meet the area rule.",
         "before": before, "after": None, "verified": False},
    ]
    fail = report(a_fail, url="http://127.0.0.1:8765/site/", captured_at="2026-10-24T15:30:12Z",
                  attribute="bbox", capture=capture_block(h_fail, "waapi_step", True),
                  culprits=culprits, fixes=fixes, evidence=evidence_for(r_fail, a_fail))

    # ---- PASS: the same page with nothing moving -----------------------------
    a_pass, r_pass, h_pass = run(lambda i: still)
    passed = report(a_pass, url="http://127.0.0.1:8765/calm/", captured_at="2026-10-24T14:02:40Z",
                    attribute="none", capture=capture_block(h_pass, "waapi_step", True),
                    evidence=evidence_for(r_pass, a_pass))

    # ---- INCONCLUSIVE: no violation measured, but capture fell back to real time
    inconclusive = report(
        a_pass, url="http://127.0.0.1:8765/gif-hero/", captured_at="2026-10-24T12:45:03Z",
        attribute="bbox",
        capture=capture_block(h_pass, "realtime", False,
                              fallback="An animated GIF (img.hero) cannot be clock-controlled, so frames were recorded in real time.",
                              uncontrollable=["img.hero (animated GIF)"]),
        verdict="INCONCLUSIVE",
        reason="No 2.3.1 violation was detected, but capture was not deterministic, so a PASS cannot be given.",
        evidence=evidence_for(r_pass, a_pass))

    for name, rep in (("report-fail.json", fail), ("report-pass.json", passed),
                      ("report-inconclusive.json", inconclusive)):
        errors = sorted(validator.iter_errors(rep), key=lambda e: list(e.path))
        if errors:
            raise SystemExit(f"{name} is not schema-valid: {errors[0].message} at {list(errors[0].path)}")
        (OUT / name).write_text(json.dumps(rep, indent=1) + "\n")
        print(f"wrote {name}: verdict={rep['verdict']} violations={len(rep['violations'])} "
              f"frames={rep['capture']['frameCount']}")

    def job(jid, url, created, status, phase, progress, attribute, verdict=None, reason=None, error=None):
        return {"id": jid, "url": url, "createdAt": created, "status": status, "phase": phase,
                "progress": progress,
                "settings": {"duration": DURATION, "viewport": {"width": W, "height": H}, "attribute": attribute},
                "verdict": verdict, "reason": reason, "error": error}

    done = {"current": 1, "total": 1, "label": "done"}
    jobs = [
        job("20261024-163005-aa0005", "https://example.test/broken", "2026-10-24T16:30:05Z", "error", "error",
            {"current": 0, "total": 0, "label": ""}, "bbox",
            error="Navigation to https://example.test/broken failed: the server did not respond within 30 s."),
        job("20261024-161511-aa0004", "http://127.0.0.1:8765/site/", "2026-10-24T16:15:11Z", "running", "capturing",
            {"current": 312, "total": 600, "label": "frames 312/600"}, "ablate"),
        job("20261024-124503-aa0003", inconclusive["url"], "2026-10-24T12:45:03Z", "done", "done", done, "bbox",
            "INCONCLUSIVE", inconclusive["reason"]),
        job("20261024-140240-aa0002", passed["url"], "2026-10-24T14:02:40Z", "done", "done", done, "none",
            "PASS", passed["reason"]),
        job("20261024-153012-aa0001", fail["url"], "2026-10-24T15:30:12Z", "done", "done", done, "bbox",
            "FAIL", fail["reason"]),
    ]
    jobs.sort(key=lambda j: j["createdAt"], reverse=True)
    (OUT / "jobs.json").write_text(json.dumps(jobs, indent=1) + "\n")
    print("wrote jobs.json")

    for name, arr in (("frame-a.png", frame_a), ("frame-b.png", frame_b), ("frame-still.png", still)):
        Image.fromarray(arr).save(OUT / name, optimize=True)
        print(f"wrote {name}")


if __name__ == "__main__":
    main()
