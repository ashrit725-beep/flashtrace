"""What the local server agrees to open, and which requests it accepts (docs/API.md, "Validation").

A user may submit:

* an ``http://`` or ``https://`` URL to any host;
* a ``file://`` URL or a local path, but only for a file inside the project directory, unless the
  server was started with ``--allow-local-files``;
* nothing else: ``javascript:``, ``data:``, ``ftp:`` and every other scheme are refused.

Symbolic links are resolved *before* the inside-the-project check, so a link that sits inside the
project but points outside it is refused just like the outside file itself.  Every refusal is a
``TargetError`` whose message is one sentence; the API sends it back with status 422.

The module also holds the small security checks the app uses: the job-id format, the one address the
server may bind to, and the Origin check for requests that change something.
"""

from __future__ import annotations

from pathlib import Path
from urllib.parse import SplitResult, unquote, urlsplit

from cli.jobs import JOB_ID_PATTERN

# The server listens on the IPv4 loopback address only, so no other computer can reach it.
LOOPBACK_HOST = "127.0.0.1"

# Host names a browser on this computer may send in the Host header.  Anything else (for example an
# attacker's domain that has been re-pointed at 127.0.0.1, "DNS rebinding") is refused.
ALLOWED_HOST_NAMES: tuple[str, ...] = ("127.0.0.1", "localhost")

# With FLASHTRACE_DEV_CORS=1 the Next.js development server (port 3000) may call the API.
DEV_UI_ORIGINS: tuple[str, ...] = ("http://localhost:3000", "http://127.0.0.1:3000")

WEB_SCHEMES: tuple[str, ...] = ("http", "https")
MAX_URL_LENGTH = 2048


class TargetError(ValueError):
    """The submitted ``url`` cannot be audited; the message is one sentence for the user."""


def validate_target(raw: str, project_root: Path, allow_local_files: bool) -> str:
    """The URL the audit should open for the user's input ``raw``, or raise TargetError.

    http(s) URLs come back unchanged.  Local files come back as the ``file://`` URL of the real file
    (every symbolic link resolved), so the audit opens exactly the file that passed the check.
    """
    text = raw.strip()
    _check_text(text)
    parts = _split(text)
    scheme = parts.scheme.lower()
    if scheme in WEB_SCHEMES:
        _check_web_url(parts)
        return text
    if scheme == "file":
        path = _path_from_file_url(parts)
    elif scheme:
        raise TargetError(
            f"the URL scheme {scheme!r} is not supported; use http://, https://, file:// or a local path"
        )
    else:
        path = _expand_home(text)
    return _check_local_file(path, Path(project_root), allow_local_files)


def _has_control_characters(text: str) -> bool:
    """True for characters below 32 (line breaks, tabs, NUL) and DEL: they never belong in a URL or a
    file name we open."""
    return any(ord(character) < 32 or ord(character) == 127 for character in text)


def _check_text(text: str) -> None:
    """Refuse empty, overlong or control-character input before trying to parse it."""
    if not text:
        raise TargetError("the url is empty; enter an http(s):// URL, a file:// URL or a local path")
    if len(text) > MAX_URL_LENGTH:
        raise TargetError(f"the url is longer than {MAX_URL_LENGTH} characters")
    if _has_control_characters(text):
        raise TargetError("the url contains control characters such as a line break")


def _expand_home(text: str) -> Path:
    """A local path with a leading '~' or '~name' replaced by that user's home folder.

    expanduser raises RuntimeError when the user named after '~' does not exist on this computer.
    """
    try:
        return Path(text).expanduser()
    except RuntimeError:
        raise TargetError("the home folder in the path could not be found") from None


def _split(text: str) -> SplitResult:
    """urlsplit, with its ValueError (for example a broken IPv6 address) turned into a TargetError."""
    try:
        return urlsplit(text)
    except ValueError:
        raise TargetError("the url could not be read as a URL or a path") from None


def _check_web_url(parts: SplitResult) -> None:
    """An http(s) URL needs a host, a valid port if any, and no user name or password."""
    try:
        _ = parts.port  # reading .port raises ValueError when the port is not a number from 0 to 65535
    except ValueError:
        raise TargetError("the URL's port is not a number between 0 and 65535") from None
    if not parts.hostname:
        raise TargetError("an http(s) URL needs a host name, for example https://example.com/")
    # The URL is written into meta.json and the report, so a password in it would be stored on disk.
    if parts.username is not None or parts.password is not None:
        raise TargetError(
            "URLs with a user name or password are not accepted, because FlashTrace saves the URL in the report"
        )


def _path_from_file_url(parts: SplitResult) -> Path:
    """The local path inside a file:// URL; file URLs that name another computer are refused."""
    if parts.netloc not in ("", "localhost"):
        raise TargetError("file:// URLs must name a file on this computer, for example file:///path/to/page.html")
    decoded = unquote(parts.path)
    # Percent-decoding can produce characters _check_text never saw, for example "%00" becomes NUL.
    if _has_control_characters(decoded):
        raise TargetError("the url contains control characters such as a line break")
    return Path(decoded)


def _check_local_file(path: Path, project_root: Path, allow_local_files: bool) -> str:
    """The file:// URL of the real file behind ``path``, after the inside-the-project rule.

    Relative paths are taken relative to the project directory.  The outside check comes before the
    "does it exist" check, so the answer for a file outside the project never reveals whether it exists.
    """
    root = project_root.resolve()
    candidate = path if path.is_absolute() else root / path
    try:
        real = candidate.resolve()  # follows every symbolic link, in the file name and in its folders
    except (OSError, RuntimeError, ValueError):  # ValueError: a name the operating system cannot hold
        raise TargetError("the local path could not be resolved") from None
    if not allow_local_files and not real.is_relative_to(root):
        raise TargetError(
            "local files outside the project directory are not allowed; "
            "start the server with --allow-local-files to audit them"
        )
    if not real.is_file():
        raise TargetError(f"no such file: {str(path)!r}")
    return real.as_uri()


def is_job_id(text: str) -> bool:
    """True when ``text`` has the job-id format of cli/jobs.py (so it can never contain '/' or '..').

    fullmatch, not match: the pattern ends in '$', which would also accept a trailing line break.
    """
    return JOB_ID_PATTERN.fullmatch(text) is not None


def origin_allowed(origin: str | None, host: str | None, extra_origins: tuple[str, ...] = ()) -> bool:
    """True when a request that changes something (POST, DELETE) may go ahead.

    Browsers put an Origin header on every POST and DELETE.  It must name this server itself (the same
    host and port as the Host header) or one of ``extra_origins``.  A request without an Origin header
    comes from a program such as curl or the tests, not from a web page, and is allowed.
    """
    if origin is None:
        return True
    if origin in extra_origins:
        return True
    parts = urlsplit(origin)
    return parts.scheme == "http" and bool(host) and parts.netloc == host
