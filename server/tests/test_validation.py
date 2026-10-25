"""Unit tests for server/validation.py: which targets the server opens, job ids, the Origin rule."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from server.validation import MAX_URL_LENGTH, TargetError, is_job_id, origin_allowed, validate_target


@pytest.fixture
def outside(tmp_path: Path) -> Path:
    """A real HTML file that is NOT inside the project directory."""
    path = tmp_path / "elsewhere" / "secret.html"
    path.parent.mkdir()
    path.write_text("<!doctype html>", encoding="utf-8")
    return path


# ------------------------------------------------------------------------------------ web URLs
@pytest.mark.parametrize("url", ["http://example.com/", "https://example.com/a?b=1#c", "HTTPS://Example.com",
                                 "http://localhost:8765/site/", "http://127.0.0.1:3000/"])
def test_http_and_https_urls_to_any_host_are_accepted_unchanged(url: str, project: Path) -> None:
    assert validate_target(url, project, allow_local_files=False) == url


def test_surrounding_whitespace_is_ignored(project: Path) -> None:
    assert validate_target("  https://example.com/  ", project, False) == "https://example.com/"


@pytest.mark.parametrize("url", ["javascript:alert(1)", "data:text/html,<p>x</p>", "ftp://example.com/x",
                                 "chrome://settings", "about:blank", "ws://example.com/", "mailto:a@b.c"])
def test_other_schemes_are_refused(url: str, project: Path) -> None:
    with pytest.raises(TargetError, match="not supported"):
        validate_target(url, project, allow_local_files=True)


@pytest.mark.parametrize("url", ["http:///path", "https://"])
def test_web_url_without_host_is_refused(url: str, project: Path) -> None:
    with pytest.raises(TargetError, match="host name"):
        validate_target(url, project, False)


def test_bad_port_and_broken_ipv6_are_refused(project: Path) -> None:
    with pytest.raises(TargetError, match="port"):
        validate_target("http://example.com:99999/", project, False)
    with pytest.raises(TargetError, match="could not be read"):
        validate_target("http://[::1/", project, False)


@pytest.mark.parametrize("url", ["https://user:secret@example.com/", "https://user@example.com/"])
def test_urls_with_credentials_are_refused_because_they_would_be_stored(url: str, project: Path) -> None:
    with pytest.raises(TargetError, match="password"):
        validate_target(url, project, False)


@pytest.mark.parametrize("url", ["", "   ", "https://example.com/\nx", "page\x00.html", "x" * (MAX_URL_LENGTH + 1)])
def test_empty_control_characters_and_overlong_input_are_refused(url: str, project: Path) -> None:
    with pytest.raises(TargetError):
        validate_target(url, project, False)


# ------------------------------------------------------------------------------------ local files
def test_relative_path_inside_project_becomes_file_url_of_the_real_file(project: Path) -> None:
    assert validate_target("page.html", project, False) == (project / "page.html").resolve().as_uri()


def test_absolute_path_and_file_url_inside_project_are_accepted(project: Path) -> None:
    expected = (project / "page.html").resolve().as_uri()
    assert validate_target(str(project / "page.html"), project, False) == expected
    assert validate_target((project / "page.html").as_uri(), project, False) == expected
    assert validate_target("file://localhost" + str((project / "page.html").resolve()), project, False) == expected


@pytest.mark.parametrize("url", ["file:///x/%00", "file:///x%00y", "file:///tmp/page%0A.html"])
def test_control_characters_hidden_behind_percent_encoding_are_refused(url: str, project: Path) -> None:
    """'%00' decodes to NUL, which Path.resolve() rejects with a plain ValueError (a 500 before the fix)."""
    with pytest.raises(TargetError, match="control characters"):
        validate_target(url, project, allow_local_files=True)


def test_home_folder_of_an_unknown_user_is_refused(project: Path) -> None:
    """expanduser raises RuntimeError for '~nosuchuser'; that must become a one-sentence refusal."""
    with pytest.raises(TargetError, match="home folder"):
        validate_target("~nosuchuser123/page.html", project, allow_local_files=True)


def test_file_url_naming_another_computer_is_refused(project: Path) -> None:
    with pytest.raises(TargetError, match="this computer"):
        validate_target("file://fileserver/share/page.html", project, True)


def test_outside_path_is_refused_without_allow_local_files(project: Path, outside: Path) -> None:
    for target in (str(outside), outside.as_uri(), os.path.relpath(outside, project)):
        with pytest.raises(TargetError, match="outside the project directory"):
            validate_target(target, project, allow_local_files=False)


def test_outside_path_is_accepted_with_allow_local_files(project: Path, outside: Path) -> None:
    assert validate_target(str(outside), project, allow_local_files=True) == outside.resolve().as_uri()


def test_outside_check_comes_before_the_existence_check(project: Path, tmp_path: Path) -> None:
    """The answer for an outside path must not reveal whether that file exists."""
    with pytest.raises(TargetError, match="outside the project directory"):
        validate_target(str(tmp_path / "does-not-exist.html"), project, False)


def test_missing_file_and_directory_inside_project_are_refused(project: Path) -> None:
    with pytest.raises(TargetError, match="no such file"):
        validate_target("missing.html", project, False)
    (project / "folder").mkdir()
    with pytest.raises(TargetError, match="no such file"):
        validate_target("folder", project, False)


def test_symlink_inside_project_pointing_outside_is_refused(project: Path, outside: Path) -> None:
    link = project / "innocent.html"
    link.symlink_to(outside)
    with pytest.raises(TargetError, match="outside the project directory"):
        validate_target("innocent.html", project, False)
    # With --allow-local-files the link is fine, and the audit opens the real file behind it.
    assert validate_target("innocent.html", project, True) == outside.resolve().as_uri()


def test_symlinked_folder_escape_is_refused(project: Path, outside: Path) -> None:
    (project / "linked-dir").symlink_to(outside.parent, target_is_directory=True)
    with pytest.raises(TargetError, match="outside the project directory"):
        validate_target("linked-dir/secret.html", project, False)


def test_project_root_reached_through_a_symlink_still_accepts_its_own_files(project: Path, tmp_path: Path) -> None:
    alias = tmp_path / "alias"
    alias.symlink_to(project, target_is_directory=True)
    assert validate_target("page.html", alias, False) == (project / "page.html").resolve().as_uri()


# ------------------------------------------------------------------------------------ ids and origins
@pytest.mark.parametrize("job_id", ["20261024-153012-a1b2c3", "20261024-153012-a1b2c3-1", "20261024-153012-a1b2c3-999"])
def test_valid_job_ids(job_id: str) -> None:
    assert is_job_id(job_id)


@pytest.mark.parametrize("job_id", ["", "abc", "../etc", "20261024-153012-a1b2c3\n", "20261024-153012-A1B2C3",
                                    "20261024-153012-a1b2c3-1000", "20261024-153012-a1b2c3/x"])
def test_invalid_job_ids(job_id: str) -> None:
    assert not is_job_id(job_id)


def test_origin_rule() -> None:
    host = "127.0.0.1:8001"
    assert origin_allowed(None, host)  # curl, tests: no Origin header
    assert origin_allowed("http://127.0.0.1:8001", host)  # the server's own UI
    assert not origin_allowed("http://evil.example", host)
    assert not origin_allowed("http://127.0.0.1:9999", host)  # another local program's page
    assert not origin_allowed("null", host)  # sandboxed iframes and file:// pages
    assert not origin_allowed("http://localhost:3000", host)
    assert origin_allowed("http://localhost:3000", host, ("http://localhost:3000",))
