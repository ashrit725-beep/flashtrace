"""Tests for cli/jobs.py: job ids, the job folder layout, meta.json and settings checks."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path

import pytest

from capture.strategies import FALLBACK_ORDER
from cli.jobs import (
    EVIDENCE_DIR_NAME,
    FRAMES_DIR_NAME,
    RED_RULE_CHOICES,
    STRATEGY_CHOICES,
    AuditSettings,
    default_base,
    job_dir_for,
    job_id_for,
    list_jobs,
    new_job_dir,
    parse_viewport,
    read_meta,
    settings_from_dict,
    update_meta,
    write_meta,
)
from engine.constants import RED_RULES

URL = "file:///pages/a.html"
NOON = datetime(2026, 10, 24, 12, 0, 5, tzinfo=timezone.utc)


def test_job_id_is_utc_time_plus_six_hex_of_url_and_settings() -> None:
    settings = AuditSettings()
    canonical = json.dumps(settings.to_dict(), sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256((URL + canonical).encode("utf-8")).hexdigest()
    assert job_id_for(URL, settings, NOON) == f"20261024-120005-{digest[:6]}"


def test_job_id_changes_with_settings_and_url() -> None:
    base = job_id_for(URL, AuditSettings(), NOON)
    assert job_id_for(URL, AuditSettings(fps=30), NOON) != base
    assert job_id_for("file:///pages/b.html", AuditSettings(), NOON) != base
    assert re.fullmatch(r"\d{8}-\d{6}-[0-9a-f]{6}", base)


def test_new_job_dir_creates_layout_and_never_reuses_a_folder(tmp_path: Path) -> None:
    first = new_job_dir(tmp_path, URL, AuditSettings(), NOON)
    second = new_job_dir(tmp_path, URL, AuditSettings(), NOON)  # same page, same second
    assert first.parent == tmp_path / "jobs"
    assert (first / FRAMES_DIR_NAME).is_dir() and (first / EVIDENCE_DIR_NAME).is_dir()
    assert second.name == first.name + "-1"
    assert list_jobs(tmp_path) == [first.name, second.name]


def test_job_dir_for_rejects_bad_ids_and_missing_jobs(tmp_path: Path) -> None:
    job = new_job_dir(tmp_path, URL, AuditSettings(), NOON)
    assert job_dir_for(tmp_path, job.name) == job
    with pytest.raises(ValueError):
        job_dir_for(tmp_path, "../../etc")
    with pytest.raises(FileNotFoundError):
        job_dir_for(tmp_path, "20261024-120005-abcdef")


def test_list_jobs_of_empty_base_is_empty(tmp_path: Path) -> None:
    assert list_jobs(tmp_path / "nothing-here") == []


def test_default_base_is_dot_flashtrace_in_cwd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    assert default_base() == tmp_path / ".flashtrace"


def test_meta_round_trip_and_update(tmp_path: Path) -> None:
    job = new_job_dir(tmp_path, URL, AuditSettings(), NOON)
    write_meta(job, {"status": "running", "settings": AuditSettings().to_dict()})
    update_meta(job, status="done", verdict="PASS")
    meta = read_meta(job)
    assert meta["status"] == "done" and meta["verdict"] == "PASS"
    assert settings_from_dict(meta["settings"]) == AuditSettings()
    assert not list(job.glob("*.tmp"))


def test_settings_problems_name_every_bad_value() -> None:
    bad = AuditSettings(duration_s=0, viewport=(0, 768), fps=0, red_rule="x", attribute="y", strategy="z")
    text = " ".join(bad.problems())
    for word in ("duration", "fps", "viewport", "red rule", "attribute", "strategy"):
        assert word in text
    assert AuditSettings().problems() == []
    assert AuditSettings(duration_s=float("nan")).problems()


def test_settings_problems_reject_frame_counts_capture_cannot_record() -> None:
    # 1e308 s x 60 fps is infinite: capture's round(duration x fps) would raise OverflowError.
    huge = AuditSettings(duration_s=1e308).problems()
    assert len(huge) == 1 and "too many frames" in huge[0]
    # An fps too large to turn into a float must be reported, not raise.
    assert any("too many frames" in p for p in AuditSettings(fps=10**400).problems())
    # 0.001 s x 60 fps rounds to 0 frames: no capture at all.
    tiny = AuditSettings(duration_s=0.001).problems()
    assert len(tiny) == 1 and "less than one frame" in tiny[0]
    # One frame is still a valid request (SPEC 6.4 turns it into an INCONCLUSIVE verdict).
    assert AuditSettings(duration_s=1 / 60).problems() == []
    # A bad fps is reported once, as an fps problem, not also as a frame-count problem.
    assert AuditSettings(fps=0).problems() == ["fps must be a positive whole number, got 0"]


def test_parse_viewport() -> None:
    assert parse_viewport("1024x768") == (1024, 768)
    assert parse_viewport(" 375X812 ") == (375, 812)
    for text in ("1024", "0x768", "axb", "1024x768x2", "-5x10"):
        with pytest.raises(ValueError):
            parse_viewport(text)


def test_choices_match_the_engine_and_capture_layers() -> None:
    assert STRATEGY_CHOICES == ("auto",) + tuple(FALLBACK_ORDER)
    assert RED_RULE_CHOICES == tuple(RED_RULES)
