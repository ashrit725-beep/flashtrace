"""Benchmark scoring: compare FlashTrace's outcome on each page with the truth file.

Pure functions over plain dicts (no browser, no files), so every number in docs/RESULTS.md can be
re-derived from the JSON the same run writes next to it (docs/RESULTS.json for the official run; the
Markdown names the file and its sha256) and is unit-tested on hand-made outcomes.

A *row* is one benchmark page::

    {"id": "bench-0001", "mechanism": "css", "expected": {verdict, types, advisory, boundary,
     boundaryReasons}, "truthCulprits": ["#flash-1"], "verdict": "PASS" | "FAIL" | "INCONCLUSIVE" |
     "ERROR" | "SKIP", "violationTypes": [...], "advisory": bool, "culprits": [{"selector",
     "inMinimalSet"}], "strategy": "waapi_step" | ... | "none" (capture failed), "captureWallS": float,
     "analysisWallS": float}
"""

from __future__ import annotations

import statistics
from collections.abc import Mapping, Sequence
from typing import Any

from bench.truth import (
    AREA_BAND,
    AREA_LIMIT,
    DELTA_L_BAND,
    GENERAL_MIN_DELTA_L,
    MAX_FLASHES_PER_SECOND,
    RATE_BAND_HZ,
    VIDEO_SOFT_AREAS,
    VIDEO_SOFT_DELTA_L,
)
from engine.constants import ADVISORY_MIN_DURATION_SECONDS, RATE_WINDOW_SECONDS

Row = Mapping[str, Any]

EXPECTED_VERDICTS: tuple[str, ...] = ("PASS", "FAIL")
GOT_VERDICTS: tuple[str, ...] = ("PASS", "FAIL", "INCONCLUSIVE", "ERROR")

# SPEC T7 (an advisory, not a WCAG rule): "the trailing-one-second qualifying-flash count is >= 2 at
# every frame of a span longer than 5 s".  WCAG 2.3.1 sets the one-second window ("any one second
# period", RATE_WINDOW_SECONDS).  The span cannot start until one full trailing second holds two
# flashes, which at 2 flashes per second takes about one second, and it must then last longer than
# 5 s.  So only a capture longer than 5 s + 1 s can be expected to raise the advisory on every page
# the truth file marks for one; a shorter capture can miss it while the engine is right.
ADVISORY_MIN_CAPTURE_SECONDS: float = ADVISORY_MIN_DURATION_SECONDS + RATE_WINDOW_SECONDS

# cli/report.py capture_block writes this strategy name when the capture itself failed (no frames,
# no metadata).  Such a page is INCONCLUSIVE, not ERROR, but it recorded no animation.
FAILED_CAPTURE_STRATEGY = "none"

# Each boundary reason written by bench/truth.py starts with one of these prefixes; the label groups
# pages by *why* they are near a limit (the numbers come from bench/truth.py, not typed here).
BOUNDARY_KINDS: tuple[tuple[str, str], ...] = (
    ("rate ", f"flash rate within {RATE_BAND_HZ} Hz of {MAX_FLASHES_PER_SECOND} per second"),
    ("area fraction", f"area fraction within {AREA_BAND} of {AREA_LIMIT}"),
    ("actual delta L", f"luminance step within {DELTA_L_BAND} of {GENERAL_MIN_DELTA_L}"),
    ("video: lossy", f"video with a luminance step of {VIDEO_SOFT_DELTA_L} or less (lossy codec)"),
    ("video: chroma", "video at area " + " / ".join(str(a) for a in VIDEO_SOFT_AREAS) + " (chroma subsampling)"),
)


def expected_verdict(row: Row) -> str:
    return str(row["expected"]["verdict"])


def is_audited(row: Row) -> bool:
    """SKIP pages (e.g. video without ffmpeg) are never audited."""
    return expected_verdict(row) != "SKIP"


def is_clear(row: Row) -> bool:
    """Not a boundary case and not skipped."""
    return is_audited(row) and not row["expected"]["boundary"]


def is_boundary(row: Row) -> bool:
    return is_audited(row) and bool(row["expected"]["boundary"])


def fraction(numerator: int, denominator: int) -> float | None:
    """numerator / denominator, or None when there is nothing to divide by."""
    return numerator / denominator if denominator else None


def confusion_table(rows: Sequence[Row]) -> dict[str, dict[str, int]]:
    """counts[expected][got] for expected PASS/FAIL and got PASS/FAIL/INCONCLUSIVE/ERROR."""
    table = {expected: {got: 0 for got in GOT_VERDICTS} for expected in EXPECTED_VERDICTS}
    for row in rows:
        expected, got = expected_verdict(row), str(row["verdict"])
        if expected in table and got in table[expected]:
            table[expected][got] += 1
    return table


def verdict_accuracy(rows: Sequence[Row]) -> dict[str, Any]:
    """Share of rows whose verdict equals the expected one.  ERROR rows are left out (they are not
    an answer); INCONCLUSIVE counts as not correct."""
    answered = [row for row in rows if row["verdict"] != "ERROR"]
    correct = sum(1 for row in answered if row["verdict"] == expected_verdict(row))
    return {"correct": correct, "total": len(answered), "accuracy": fraction(correct, len(answered)),
            "errors": len(rows) - len(answered)}


def clear_case_scores(rows: Sequence[Row]) -> dict[str, Any]:
    clear = [row for row in rows if is_clear(row)]
    return {"pages": len(clear), "accuracy": verdict_accuracy(clear), "confusion": confusion_table(clear)}


def boundary_kind(reason: str) -> str:
    """Group label for one boundary reason sentence."""
    for prefix, label in BOUNDARY_KINDS:
        if reason.startswith(prefix):
            return label
    return reason


def boundary_scores(rows: Sequence[Row]) -> dict[str, Any]:
    """Boundary pages: confusion table plus, per kind of boundary, how many matched the expectation.

    A page can be near more than one limit, so it can appear under several kinds.
    """
    boundary = [row for row in rows if is_boundary(row)]
    kinds: dict[str, dict[str, int]] = {}
    for row in boundary:
        for kind in sorted({boundary_kind(r) for r in row["expected"]["boundaryReasons"]}):
            entry = kinds.setdefault(kind, {"pages": 0, "matching": 0})
            entry["pages"] += 1
            entry["matching"] += int(row["verdict"] == expected_verdict(row))
    by_reason = [{"reason": kind, **counts} for kind, counts in sorted(kinds.items())]
    return {"pages": len(boundary), "accuracy": verdict_accuracy(boundary),
            "confusion": confusion_table(boundary), "byReason": by_reason}


def mechanism_scores(rows: Sequence[Row]) -> list[dict[str, Any]]:
    """Clear-case verdict accuracy for each page mechanism (css, waapi, raf, canvas, video, ...)."""
    clear = [row for row in rows if is_clear(row)]
    names = sorted({str(row["mechanism"]) for row in clear})
    return [{"mechanism": name, **verdict_accuracy([r for r in clear if r["mechanism"] == name])} for name in names]


def type_agreement(rows: Sequence[Row]) -> dict[str, Any]:
    """On pages expected FAIL that FlashTrace also failed (boundary or not): do the violation types
    (general / red) equal the expected types?"""
    both_fail = [row for row in rows if expected_verdict(row) == "FAIL" and row["verdict"] == "FAIL"]
    disagree = [str(row["id"]) for row in both_fail
                if sorted(set(row["expected"]["types"])) != sorted(set(row.get("violationTypes", [])))]
    agree = len(both_fail) - len(disagree)
    return {"agree": agree, "total": len(both_fail), "accuracy": fraction(agree, len(both_fail)),
            "disagreements": disagree}


def advisory_agreement(rows: Sequence[Row], duration_s: float) -> dict[str, Any]:
    """Advisory present == truth.expected.advisory, on clear pages expected to PASS.

    Only expected-PASS pages are scored: the truth marks an advisory only for 2-3 flashes per second,
    which never FAILs, and on a failing page the advisory adds nothing to the verdict.  The truth marks
    those pages whatever the capture length, but the engine can only raise the advisory when the
    capture is longer than ADVISORY_MIN_CAPTURE_SECONDS (see its comment); for a shorter capture the
    row is reported as not measured rather than counted as disagreement.
    """
    if duration_s <= ADVISORY_MIN_CAPTURE_SECONDS:
        return {"measured": False, "agree": 0, "total": 0, "accuracy": None,
                "note": (f"not measured: the advisory needs more than {ADVISORY_MIN_DURATION_SECONDS:g} s of "
                         f"sustained flashing, which can only start once a full {RATE_WINDOW_SECONDS:g} s window "
                         f"holds two flashes, so a capture must be longer than {ADVISORY_MIN_CAPTURE_SECONDS:g} s; "
                         f"this one was {duration_s:g} s")}
    scored = [row for row in rows if is_clear(row) and expected_verdict(row) == "PASS" and row["verdict"] != "ERROR"]
    agree = sum(1 for row in scored if bool(row.get("advisory")) == bool(row["expected"]["advisory"]))
    return {"measured": True, "agree": agree, "total": len(scored), "accuracy": fraction(agree, len(scored)),
            "note": "clear pages expected to PASS only"}


def attribution_scores(rows: Sequence[Row], attribute: str) -> dict[str, Any]:
    """Top-1 and minimal-set agreement with truth.culprits, on pages whose report has culprits."""
    with_culprits = [row for row in rows if row.get("culprits") and row.get("truthCulprits")]
    if not with_culprits:
        why = f"attribution: {attribute}" if attribute == "none" else "no report contained culprits"
        return {"measured": False, "note": f"not measured ({why})"}
    top1 = sum(1 for row in with_culprits if row["culprits"][0]["selector"] in row["truthCulprits"])
    minimal = sum(
        1 for row in with_culprits
        if {c["selector"] for c in row["culprits"] if c["inMinimalSet"]} == set(row["truthCulprits"])
    )
    total = len(with_culprits)
    return {"measured": True, "pages": total,
            "top1": {"agree": top1, "total": total, "accuracy": fraction(top1, total)},
            "minimalSet": {"agree": minimal, "total": total, "accuracy": fraction(minimal, total)}}


def runtime_stats(values: Sequence[float]) -> dict[str, float] | None:
    """mean / median / min / max of measured wall times; None when nothing was measured."""
    if not values:
        return None
    return {"count": len(values), "mean": statistics.fmean(values), "median": statistics.median(values),
            "min": min(values), "max": max(values)}


def capture_failed(row: Row) -> bool:
    """The page's capture itself failed, so its wall time is time-to-failure, not capture time."""
    return row.get("strategy") == FAILED_CAPTURE_STRATEGY


def runtime_scores(rows: Sequence[Row]) -> dict[str, Any]:
    """Capture and analysis wall time per audited page that did not error and whose capture worked.

    RESULTS.md labels these figures as the time for the full capture duration, which a failed capture
    never recorded; those pages are left out and counted in ``failedCaptures``.
    """
    answered = [row for row in rows if is_audited(row) and row["verdict"] != "ERROR"]
    measured = [row for row in answered if not capture_failed(row)]
    return {"capture": runtime_stats([float(row["captureWallS"]) for row in measured]),
            "analysis": runtime_stats([float(row["analysisWallS"]) for row in measured]),
            "failedCaptures": len(answered) - len(measured)}


def score_all(rows: Sequence[Row], duration_s: float, attribute: str) -> dict[str, Any]:
    """Every score in one dict (what results.json and RESULTS.md report)."""
    return {
        "clear": clear_case_scores(rows),
        "boundary": boundary_scores(rows),
        "mechanisms": mechanism_scores(rows),
        "types": type_agreement(rows),
        "advisory": advisory_agreement(rows, duration_s),
        "attribution": attribution_scores(rows, attribute),
        "runtime": runtime_scores(rows),
    }
