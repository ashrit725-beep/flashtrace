"""Report assembly: capture metadata + engine analysis -> one report dict (SPEC 6.4, 8 and 10).

``build_report`` is the only place above the engine where a verdict is decided.  The engine says
PASS or FAIL about the frames it was given; this module adds what the engine cannot know: whether the
capture was long enough and deterministic enough for a PASS to mean anything (ADR-008).

Every report is checked against ``engine/schema/report.schema.json`` before it is written.  Reports are
deterministic: the same capture and analysis always give the same JSON except ``capturedAt``, which
``report_fingerprint`` leaves out.
"""

from __future__ import annotations

import copy
import functools
import hashlib
import json
import re
import shutil
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime, timezone
from importlib import resources
from pathlib import Path
from typing import Any, Protocol

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError, best_match

from cli.jobs import EVIDENCE_DIR_NAME, FRAMES_DIR_NAME, AuditSettings
from engine import ENGINE_VERSION, AnalysisResult, evidence_frame_indices
from engine.cells import window_px as engine_window_px
from engine.constants import (
    CELL_PX,
    LEGACY_RED_MIN_DELTA,
    LEGACY_RED_VALUE_SCALE,
    RED_MIN_DELTA_UV,
    RED_MIN_R8,
    RED_SATURATION_MIN_RATIO,
    REFERENCE_VIEWPORT,
    WINDOW_PX,
)

REPORT_VERSION = "1.0"
DISCLAIMER = "Analysis aid, not a compliance certification."

# SPEC 6.4 / ADR-008: "Capture error or fewer than 2*fps frames => INCONCLUSIVE".  Two seconds of
# animation is the shortest capture after which FlashTrace will call a page PASS.
MIN_SECONDS_FOR_PASS = 2

NON_DETERMINISTIC_NOTE = "page passed under non-deterministic capture; frames may have been missed"

# SPEC section 10, copied verbatim (wrapped lines joined with one space).  A test re-reads SPEC.md and
# fails if this list and section 10 ever disagree.
SPEC_LIMITATIONS: tuple[str, ...] = (
    "Headless sRGB rendering is not a physical display; brightness, gamma and viewing distance differ per user.",
    "Canvas/WebGL attribution is element-level only (what is drawn inside cannot be attributed).",
    "Cross-origin iframes are unattributable and cannot be clock-controlled.",
    "Animated GIF/WebP/APNG, animated SVG images and Web Workers force non-deterministic capture (strategy C) "
    "and a PASS becomes INCONCLUSIVE.",
    "Under realtime capture (strategy C) a measured rate can be one flash per second higher or lower than the "
    "page's, because a compositor frame drawn late or dropped moves a transition by one frame.",
    "Under strategy A, work that the page starts and the browser finishes on the real clock is not waited for "
    "(ADR-052): React scheduler tasks (MessageChannel messages), the page's handling of `fetch` and `XMLHttpRequest` "
    "answers, history traversals (`history.back()`, `forward()`, `go()`), script, style and image load events, "
    "`window.postMessage`, WebSocket and EventSource messages, storage callbacks and Navigation API traversals. A page "
    "that changes what it shows on such an event can show that change one frame earlier or later in another capture "
    "(several frames when the page also holds the change behind a gate on its own timers), although both captures are "
    "reported as deterministic.",
    "Under strategy A, IntersectionObserver is FlashTrace's own implementation (ADR-052). It clips a target by its "
    "ancestors' overflow along an approximated containing-block chain and ignores transforms, `clip-path`, `clip`, "
    "`border-radius`, `overflow-clip-margin` and `scrollMargin`, so a reveal that depends on those can start at a "
    "different scroll position than in a browser; `isVisible` (`trackVisibility`) equals `isIntersecting`, without an "
    "occlusion test.",
    "The fine-pattern exception (T6) and spatial-pattern hazards are not evaluated.",
    "Only the captured viewport and duration are analysed; content revealed by scrolling or interaction is "
    "analysed only if an interaction script drives it.",
    "Window size for viewports other than 1024 px wide is scaled by the width ratio (an assumption).",
    "The 341×256 px window is scored in whole 4×4 px cells and slides in 4 px steps, so a flashing area wider or "
    "taller than the window can be under-measured by up to 3 px along such an edge.",
    "No numbers in this repository are estimates: accuracy, agreement and runtime figures exist only in "
    "`docs/RESULTS.md`, produced by `flashtrace bench`, with date and commit hash.",
)

# The file capture.attribute writes into the job directory (its ATTRIBUTION_JSON_NAME; a test checks that the
# two agree).  Named here too because cli.report must not load the capture package (Playwright), and
# server/jobs.py imports it from here.
ATTRIBUTION_NOTES_NAME = "attribution.json"
# An attribution.json "inspectionNotes" entry that is copied into the limitations: the inspection page did
# not reach every violation's frame, so those violations have no culprits.  The other inspection notes are
# the capture set-up's routine notes, which the report's capture block already carries.
INCOMPLETE_INSPECTION_NOTE = re.compile(r"^inspection reached \d+ of \d+ target frame")
# Limitation lines that say why a report with culprits has no fixes start with this (cli/main.py looks for it).
FIX_NOTE_PREFIX = "fixes: "

EVIDENCE_NAME_PATTERN = re.compile(r"^\d{6}\.png$")
WALL_CLOCK_NOTE = re.compile(r"^frame loop took \d")


class CaptureMetaLike(Protocol):
    """What build_report needs from capture.CaptureMeta: ``to_dict()`` (and ``to_report_dict()`` when present)."""

    def to_dict(self) -> dict[str, object]: ...


# --------------------------------------------------------------------------------------- schema
@functools.lru_cache(maxsize=1)
def load_schema() -> dict[str, Any]:
    """engine/schema/report.schema.json as a dict (read once)."""
    text = resources.files("engine.schema").joinpath("report.schema.json").read_text(encoding="utf-8")
    return json.loads(text)


@functools.lru_cache(maxsize=1)
def _validator() -> Draft202012Validator:
    return Draft202012Validator(load_schema(), format_checker=Draft202012Validator.FORMAT_CHECKER)


def _json_path(parts: Iterable[object]) -> str:
    """('settings', 'viewport', 'width') -> 'report.settings.viewport.width'; list indexes as [i]."""
    text = "report"
    for part in parts:
        text += f"[{part}]" if isinstance(part, int) else f".{part}"
    return text


def validate_report(report: Mapping[str, Any]) -> None:
    """Raise jsonschema.ValidationError naming the first bad field when ``report`` breaks the schema."""
    error = best_match(_validator().iter_errors(report))
    if error is not None:
        raise ValidationError(f"report is not valid at {_json_path(error.absolute_path)}: {error.message}")


# --------------------------------------------------------------------------------------- capture block
def capture_keys() -> tuple[str, ...]:
    """The keys the schema's ``capture`` object allows, in schema order."""
    return tuple(load_schema()["properties"]["capture"]["properties"].keys())


def capture_block(meta: CaptureMetaLike | None, capture_error: str | None = None) -> dict[str, object]:
    """The report's ``capture`` object.

    Uses ``CaptureMeta.to_report_dict()`` when the capture layer provides it, otherwise keeps only the
    schema's keys from ``to_dict()``.  With no metadata (the capture itself failed) the strategy is
    "none" and the error is the only note.
    """
    if meta is None:
        return {
            "strategy": "none",
            "deterministic": False,
            "frameCount": 0,
            "hash": "",
            "frameHashes": [],
            "fallbackReason": None,
            "uncontrollable": [],
            "notes": [f"capture failed: {capture_error}"] if capture_error else [],
        }
    to_report = getattr(meta, "to_report_dict", None)
    full = dict(to_report()) if callable(to_report) else dict(meta.to_dict())
    block = {key: full[key] for key in capture_keys() if key in full}
    # The capture layer adds a wall-clock note ("frame loop took 1.23 s ..."). It changes on every run,
    # which would make two reports of identical frames differ, so it is left out of the report; the
    # benchmark measures wall time itself.
    block["notes"] = [note for note in block.get("notes", []) if not WALL_CLOCK_NOTE.match(str(note))]
    return block


# --------------------------------------------------------------------------------------- verdict
def decide_verdict(
    analysis: AnalysisResult | None,
    capture: Mapping[str, Any],
    fps: int,
    capture_error: str | None = None,
) -> tuple[str, str]:
    """Verdict and reason after the escalation rules of SPEC 6.4 / ADR-008.

    1. The capture failed or gave fewer than 2 frames (the engine could not run): INCONCLUSIVE.
    2. Engine FAIL: FAIL under any strategy (what was observed did flash).
    3. Fewer than 2*fps frames: INCONCLUSIVE (too short for a PASS to mean anything).
    4. Engine PASS under non-deterministic capture: INCONCLUSIVE.
    5. Otherwise PASS.
    """
    frames = int(capture.get("frameCount", 0))
    needed = MIN_SECONDS_FOR_PASS * fps
    if capture_error is not None:
        return "INCONCLUSIVE", f"Inconclusive: the capture failed ({capture_error})."
    if analysis is None:
        return "INCONCLUSIVE", f"Inconclusive: only {frames} frame(s) were captured, so nothing could be analysed."
    engine_reason = analysis.reason()
    if analysis.verdict == "FAIL":
        if capture.get("deterministic"):
            return "FAIL", engine_reason
        return "FAIL", (
            f"{engine_reason} The capture was not deterministic (strategy {capture.get('strategy')}), "
            "but the flashes it recorded did happen, so the verdict stands."
        )
    if analysis.frame_count < needed:
        return "INCONCLUSIVE", (
            f"{engine_reason} Inconclusive: only {analysis.frame_count} frames were captured; a PASS needs at "
            f"least {needed} ({MIN_SECONDS_FOR_PASS} s at {fps} fps)."
        )
    if not capture.get("deterministic"):
        return "INCONCLUSIVE", f"{engine_reason} Inconclusive: {NON_DETERMINISTIC_NOTE}."
    return "PASS", engine_reason


# --------------------------------------------------------------------------------------- evidence
def evidence_labels(analysis: AnalysisResult, indices: Sequence[int]) -> dict[int, str]:
    """'frame 15 (t = 0.250 s) - first qualifying general flash' for each evidence frame."""
    roles: dict[int, list[str]] = {index: [] for index in indices}

    def add(index: int, role: str) -> None:
        if index in roles and role not in roles[index]:
            roles[index].append(role)

    add(0, "baseline")
    for violation in analysis.violations:
        first = violation.frame_indices[0] if violation.frame_indices else violation.start
        add(first, f"first qualifying {violation.type} flash")
        add(first - 1, "just before")
    return {
        index: f"frame {index} (t = {index / analysis.fps:.3f} s) - {'; '.join(roles[index]) or 'evidence'}"
        for index in indices
    }


def copy_evidence(analysis: AnalysisResult, job_dir: Path) -> list[dict[str, object]]:
    """Copy the engine's evidence frames from frames/ into evidence/ and describe them for the report.

    ``pngPath`` is relative to the job directory, where report.json lives ('evidence/000015.png').
    A frame whose PNG is missing is left out rather than referenced.
    """
    indices = evidence_frame_indices(analysis)
    labels = evidence_labels(analysis, indices)
    (job_dir / EVIDENCE_DIR_NAME).mkdir(parents=True, exist_ok=True)
    frames: list[dict[str, object]] = []
    for index in indices:
        name = f"{index:06d}.png"
        source = job_dir / FRAMES_DIR_NAME / name
        if not source.is_file():
            continue
        shutil.copyfile(source, job_dir / EVIDENCE_DIR_NAME / name)
        frames.append({"index": int(index), "pngPath": f"{EVIDENCE_DIR_NAME}/{name}", "label": labels[index]})
    return frames


# --------------------------------------------------------------------------------------- notes
def capture_limitations(capture: Mapping[str, Any], fps: int) -> list[str]:
    """Limitations that depend on how this particular capture was made."""
    notes: list[str] = []
    if capture.get("strategy") == "realtime":
        notes.append(
            "This capture used strategy C (realtime screencast): screenshots were taken on the real clock and "
            "resampled, so flashes between screenshots may have been missed."
        )
    uncontrollable = list(capture.get("uncontrollable") or [])
    if uncontrollable:
        notes.append(
            "The page contains content FlashTrace cannot clock-control (" + "; ".join(uncontrollable)
            + "); it ran on its own clock during the capture."
        )
    if capture.get("strategy") == "waapi_step":
        notes.append(
            "Math.random was replaced by a fixed-seed generator for this capture, but crypto.getRandomValues "
            "was not, so content driven by it may differ between runs (ADR-022)."
        )
    # Sampling limit (ADR-019): a flash needs at least two frames (one per state) to be seen at all.
    notes.append(
        f"Flashing faster than {fps / 2:g} per second (half the {fps} fps capture rate) cannot be seen in "
        "the frames (ADR-019)."
    )
    return notes


def _attribution_items(data: Mapping[str, Any]) -> list[Any]:
    """The sentences of one attribution.json object that belong in the report's limitations."""
    items = list(data.get("limitations") or [])
    items += [f"attribution: selector {warning}" for warning in data.get("selectorWarnings") or []
              if isinstance(warning, str)]
    items += [f"attribution: {note.strip()}, so violations whose frame was not reached have no culprits"
              for note in data.get("inspectionNotes") or []
              if isinstance(note, str) and INCOMPLETE_INSPECTION_NOTE.match(note.strip())]
    if isinstance(data.get("error"), str) and data["error"].strip():
        items.append(f"attribution stopped early, so the culprit list may be incomplete: {data['error'].strip()}")
    return items


def attribution_notes(job_dir: str | Path) -> list[str]:
    """Notes the attribution step left in ``<job>/attribution.json`` (empty when there is none).

    capture.attribute writes an object: its "limitations" sentences, its "selectorWarnings" (a culprit
    selector that matched more than one element, which a CSS fix would also change), the
    "inspectionNotes" entry saying the inspection did not reach every violation's frame, and its
    "error" (attribution stopped early) are copied.  A plain list of sentences is accepted too.
    Anything that is not a non-empty string is ignored, and repeats are dropped.
    """
    path = Path(job_dir) / ATTRIBUTION_NOTES_NAME
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return [f"{ATTRIBUTION_NOTES_NAME} could not be read, so attribution notes are missing from this report."]
    if isinstance(data, Mapping):
        items = _attribution_items(data)
    else:
        items = list(data) if isinstance(data, list) else []
    notes: list[str] = []
    for item in items:
        if isinstance(item, str) and item.strip() and item.strip() not in notes:
            notes.append(item.strip())
    return notes


def fix_notes(culprits: Sequence[Mapping[str, Any]], fixes: Sequence[Mapping[str, Any]]) -> list[str]:
    """Why a report that names culprits has no fixes, when no culprit met capture.fix's rule (else empty)."""
    if not culprits or fixes:
        return []
    from capture.fix import unfixed_note  # imported here: the capture package loads Playwright

    note = unfixed_note(culprits)
    return [FIX_NOTE_PREFIX + note] if note else []


def report_limitations(capture: Mapping[str, Any], fps: int, job_dir: str | Path,
                       culprits: Sequence[Mapping[str, Any]] = (),
                       fixes: Sequence[Mapping[str, Any]] = ()) -> list[str]:
    """SPEC section 10, then what this capture adds, then the attribution and fix steps' notes."""
    return (list(SPEC_LIMITATIONS) + capture_limitations(capture, fps) + attribution_notes(job_dir)
            + fix_notes(culprits, fixes))


def standard_assumptions(viewport: tuple[int, int], window: tuple[int, int], red_rule: str) -> list[str]:
    """Assumptions that hold for every report with these settings (ADR-002, 003, 007, 020)."""
    width, height = viewport
    notes: list[str] = []
    if width != REFERENCE_VIEWPORT[0]:
        # WCAG 2.2 glossary Note 1: "a 341 x 256 pixel rectangle anywhere on the displayed screen area
        # when the content is viewed at 1024 x 768 pixels".
        notes.append(
            f"The viewport is {width} px wide, not the {REFERENCE_VIEWPORT[0]} px of WCAG's reference screen, "
            f"so the {WINDOW_PX[0]}x{WINDOW_PX[1]} px window was scaled by {width}/{REFERENCE_VIEWPORT[0]} to "
            f"{window[0]}x{window[1]} px (ADR-007)."
        )
    if width % CELL_PX or height % CELL_PX:
        notes.append(
            f"The viewport {width}x{height} px is not a whole number of {CELL_PX} px cells, so the last "
            f"{width % CELL_PX} px column(s) and {height % CELL_PX} px row(s) were not analysed (ADR-002)."
        )
    notes.append(_red_rule_assumption(red_rule))
    notes.append(
        f"The redness ratio R/(R+G+B) is computed on each {CELL_PX}x{CELL_PX} px cell's colour re-encoded to "
        f"whole 0-255 sRGB values, and is 0 when R < {RED_MIN_R8}: a red that dark has no visible hue "
        "(ADR-003, ADR-020)."
    )
    return notes


def _red_rule_assumption(red_rule: str) -> str:
    """One sentence naming the red-flash rule that produced the verdict (ADR-001)."""
    if red_rule == "wcag21-legacy":
        # Pre-2022 working definition: "(R - G - B) x 320", a change "greater than 20".
        return (
            f"Red flashes were judged with the pre-2022 rule (R/(R+G+B) >= {RED_SATURATION_MIN_RATIO} and a change "
            f"in (R-G-B)x{LEGACY_RED_VALUE_SCALE} greater than {LEGACY_RED_MIN_DELTA}), kept only for comparison "
            "with older tools (ADR-001)."
        )
    # WCAG 2.2 glossary Note 3: "R/(R + G + B) that is greater than or equal to 0.8, and the difference
    # between states is more than 0.2 (unitless) in the CIE 1976 UCS chromaticity diagram".
    return (
        f"Red flashes were judged with the WCAG 2.2 working definition: one state has R/(R+G+B) >= "
        f"{RED_SATURATION_MIN_RATIO} and the two states differ by more than {RED_MIN_DELTA_UV} in CIE 1976 "
        "u'v' chromaticity (ADR-001)."
    )


# --------------------------------------------------------------------------------------- assembly
def utc_now_iso() -> str:
    """Current UTC time as ISO-8601 with a 'Z', to the second ('2026-10-24T17:05:09Z')."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def settings_block(settings: AuditSettings, window: tuple[int, int]) -> dict[str, object]:
    """The report's ``settings`` object."""
    return {
        "duration": float(settings.duration_s),
        "viewport": {"width": int(settings.viewport[0]), "height": int(settings.viewport[1])},
        "fps": int(settings.fps),
        "redRule": settings.red_rule,
        "attribute": settings.attribute,
        "windowPx": {"width": int(window[0]), "height": int(window[1])},
        "cellPx": int(CELL_PX),
    }


def engine_block(analysis: AnalysisResult | None) -> dict[str, Any]:
    """violations, advisory, timeline and heatmap from the engine (empty when it did not run)."""
    if analysis is None:
        return {"violations": [], "advisory": [], "timeline": [],
                "heatmap": {"cellSize": int(CELL_PX), "w": 0, "h": 0, "data": ""}}
    data = analysis.to_dict()
    return {key: data[key] for key in ("violations", "advisory", "timeline", "heatmap")}


def build_report(
    url: str,
    settings: AuditSettings,
    capture_meta: CaptureMetaLike | None,
    analysis: AnalysisResult | None,
    job_dir: str | Path,
    culprits: Sequence[Mapping[str, Any]] | None = None,
    fixes: Sequence[Mapping[str, Any]] | None = None,
    assumptions: Sequence[str] | None = None,
    *,
    capture_error: str | None = None,
    captured_at: str | None = None,
) -> dict[str, Any]:
    """Assemble the full report.  Copies evidence PNGs into ``<job_dir>/evidence/`` as a side effect."""
    capture = capture_block(capture_meta, capture_error)
    verdict, reason = decide_verdict(analysis, capture, settings.fps, capture_error)
    window = analysis.window_px if analysis is not None else engine_window_px(settings.viewport[0])
    report: dict[str, Any] = {
        "version": REPORT_VERSION,
        "url": url,
        "capturedAt": captured_at or utc_now_iso(),
        "engineVersion": ENGINE_VERSION,
        "settings": settings_block(settings, window),
        "capture": capture,
        "verdict": verdict,
        "reason": reason,
        **engine_block(analysis),
        "culprits": [dict(c) for c in culprits or []],
        "fixes": [dict(f) for f in fixes or []],
        "evidenceFrames": copy_evidence(analysis, Path(job_dir)) if analysis is not None else [],
        "limitations": report_limitations(capture, settings.fps, job_dir, culprits or [], fixes or []),
        "assumptions": standard_assumptions(settings.viewport, window, settings.red_rule) + list(assumptions or []),
        "disclaimer": DISCLAIMER,
    }
    return report


def with_attribution(
    report: Mapping[str, Any],
    culprits: Sequence[Mapping[str, Any]],
    fixes: Sequence[Mapping[str, Any]],
    mode: str,
    job_dir: str | Path,
) -> dict[str, Any]:
    """A copy of ``report`` with new culprits and fixes from attribution mode ``mode``.

    Used when attribution is re-run on a finished job (cli.audit.reattribute): the frames, verdict and
    evidence stay as they were; the attribution notes in the limitations are read again.
    """
    updated = copy.deepcopy(dict(report))
    updated["culprits"] = [dict(c) for c in culprits]
    updated["fixes"] = [dict(f) for f in fixes]
    updated["settings"]["attribute"] = mode
    updated["limitations"] = report_limitations(updated["capture"], int(updated["settings"]["fps"]), job_dir,
                                                culprits, fixes)
    return updated


# --------------------------------------------------------------------------------------- output
def report_json_text(report: Mapping[str, Any]) -> str:
    """The exact text written to a report file: sorted keys, 2-space indent, UTF-8, trailing newline."""
    return json.dumps(report, sort_keys=True, indent=2, ensure_ascii=False) + "\n"


def write_report(report: Mapping[str, Any], path: str | Path) -> Path:
    """Write ``report`` as JSON to ``path`` (parent folders are created)."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(report_json_text(report), encoding="utf-8")
    return target


def report_fingerprint(report: Mapping[str, Any]) -> str:
    """sha256 of the canonical JSON of ``report`` without ``capturedAt`` (the benchmark's determinism check)."""
    stable = {key: value for key, value in report.items() if key != "capturedAt"}
    canonical = json.dumps(stable, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def export_report(report: Mapping[str, Any], job_dir: str | Path, out_path: str | Path) -> dict[str, Any]:
    """Write ``report`` to ``out_path`` so that its evidence paths still work from there.

    Inside the job directory nothing changes.  Anywhere else the evidence PNGs are copied into a folder
    named ``<out stem>-evidence`` next to the file and ``pngPath`` is rewritten to point into it; a
    per-report folder means two reports exported to one directory never overwrite each other's frames.
    Returns the report exactly as written.
    """
    job = Path(job_dir).resolve()
    target = Path(out_path).resolve()
    exported = copy.deepcopy(dict(report))
    if target.parent != job and exported["evidenceFrames"]:
        folder_name = f"{target.stem}-evidence"
        folder = target.parent / folder_name
        _reset_evidence_folder(folder)
        for frame in exported["evidenceFrames"]:
            name = Path(str(frame["pngPath"])).name
            shutil.copyfile(job / str(frame["pngPath"]), folder / name)
            frame["pngPath"] = f"{folder_name}/{name}"
    write_report(exported, target)
    return exported


def _reset_evidence_folder(folder: Path) -> None:
    """Create ``folder`` and delete frame files (NNNNNN.png) left there by an earlier export."""
    folder.mkdir(parents=True, exist_ok=True)
    for stale in folder.iterdir():
        if stale.is_file() and EVIDENCE_NAME_PATTERN.match(stale.name):
            stale.unlink()
