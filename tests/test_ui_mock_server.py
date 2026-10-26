"""The UI self-audit's stand-in server (tests/ui_mock_server.py) answers like the real one where it can."""

from __future__ import annotations

import json
from pathlib import Path
from urllib.request import urlopen

from engine import ENGINE_VERSION
from tests.ui_mock_server import DEFAULT_FIXTURES, serve


def test_health_reports_the_real_engine_version(tmp_path: Path) -> None:
    """GET /health names the engine this repository ships (server/app.py does the same), never a stale copy."""
    (tmp_path / "index.html").write_text("<!doctype html><title>stand-in export</title>", encoding="utf-8")
    server, base_url = serve(tmp_path, DEFAULT_FIXTURES)
    try:
        with urlopen(f"{base_url}/health", timeout=10) as response:
            health = json.loads(response.read())
    finally:
        server.shutdown()
        server.server_close()
    assert health["engineVersion"] == ENGINE_VERSION
