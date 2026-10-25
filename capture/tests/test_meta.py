"""CaptureMeta's report object against the report schema, and argument checks that need no browser."""

from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest

from capture import CaptureMeta, capture, resolve_url

SCHEMA_PATH = Path(__file__).resolve().parents[2] / "engine" / "schema" / "report.schema.json"
REPORT_CAPTURE_KEYS = {"strategy", "deterministic", "frameCount", "hash", "frameHashes", "fallbackReason", "uncontrollable", "notes"}


def capture_sub_schema() -> dict[str, object]:
    """The ``capture`` object of the report schema (SPEC 8: every report is validated against it)."""
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    return schema["properties"]["capture"]


def sample_meta() -> CaptureMeta:
    return CaptureMeta(
        strategy="waapi_step",
        deterministic=True,
        frame_hashes=["a" * 64, "b" * 64],
        hash="c" * 64,
        uncontrollable=[],
        fallback_reason=None,
        notes=["2 animation(s) found at frame 0, 0 discovered later"],
        viewport=(1024, 768),
        fps=60,
        duration_s=2 / 60,
        frame_count=2,
        url="file:///tmp/page.html",
        wall_time_s=0.5,
    )


def test_to_report_dict_is_exactly_the_schema_capture_object() -> None:
    report = sample_meta().to_report_dict()
    assert set(report) == REPORT_CAPTURE_KEYS
    jsonschema.validate(report, capture_sub_schema())


def test_to_dict_keeps_the_settings_and_is_not_a_report_object() -> None:
    data = sample_meta().to_dict()
    assert {"viewport", "fps", "durationS", "url", "wallTimeS"} <= set(data)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(data, capture_sub_schema())


def test_local_path_refused_before_its_existence_is_checked(tmp_path: Path) -> None:
    existing = tmp_path / "page.html"
    existing.write_text("<!doctype html>", encoding="utf-8")
    # Same error whether or not the file exists, so the answer reveals nothing about this machine.
    for path in (existing, tmp_path / "missing.html"):
        with pytest.raises(ValueError, match="local files are not allowed"):
            resolve_url(str(path), allow_local_files=False)
    with pytest.raises(FileNotFoundError):
        resolve_url(str(tmp_path / "missing.html"), allow_local_files=True)


@pytest.mark.parametrize("selectors", [[""], ["  "], ["#box", ""]])
def test_empty_suppress_selector_is_rejected_before_any_browser_work(selectors: list[str]) -> None:
    with pytest.raises(ValueError, match="suppress_selectors"):
        capture("ignored.html", suppress_selectors=selectors)
