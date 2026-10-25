"""Loopback binding, Host/Origin checks, the dev-only CORS switch, and serving the static UI."""

from __future__ import annotations

import html
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from cli.report import DISCLAIMER
from server import app as server_app
from server.app import DEV_CORS_VARIABLE, serve
from server.validation import host_allowed

DEV_UI = "http://localhost:3000"


# ------------------------------------------------------------------------------------ binding
@pytest.mark.parametrize("host", ["0.0.0.0", "::", "192.168.1.10", "localhost", "example.com"])
def test_serve_refuses_any_host_but_127_0_0_1(host: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(server_app, "create_app", lambda *a, **k: pytest.fail("must refuse before building the app"))
    with pytest.raises(ValueError, match="127.0.0.1"):
        serve(port=8001, host=host)


def test_serve_builds_the_app_and_runs_uvicorn_on_loopback(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import uvicorn

    seen: dict = {}

    def fake_run(server: uvicorn.Server, sockets: object = None) -> None:
        seen.update(host=server.config.host, port=server.config.port, app=server.config.app)
        server.started = True  # as if it had served until Ctrl-C

    monkeypatch.setattr(uvicorn.Server, "run", fake_run)
    serve(port=8123, work_dir=tmp_path / "work", allow_local_files=True)
    assert seen["host"] == "127.0.0.1" and seen["port"] == 8123
    assert seen["app"].state.server.allow_local_files is True


def test_serve_exits_with_uvicorns_startup_failure_code_when_it_could_not_start(monkeypatch: pytest.MonkeyPatch,
                                                                                tmp_path: Path) -> None:
    import uvicorn

    monkeypatch.setattr(uvicorn.Server, "run", lambda server, sockets=None: None)  # e.g. the port is in use
    with pytest.raises(SystemExit) as stopped:
        serve(port=8123, work_dir=tmp_path / "work")
    assert stopped.value.code == server_app.STARTUP_FAILURE_EXIT_CODE


def test_serve_treats_the_ctrl_c_uvicorn_raises_again_after_shutdown_as_handled(monkeypatch: pytest.MonkeyPatch,
                                                                               tmp_path: Path) -> None:
    import uvicorn

    def run_then_interrupt(server: uvicorn.Server, sockets: object = None) -> None:
        server.started = True
        raise KeyboardInterrupt  # uvicorn re-raises the captured SIGINT once it has shut down

    monkeypatch.setattr(uvicorn.Server, "run", run_then_interrupt)
    serve(port=8123, work_dir=tmp_path / "work")  # returns normally


# ------------------------------------------------------------------------------------ Host / Origin
def test_foreign_host_header_is_refused(client: TestClient) -> None:
    """A DNS-rebinding page reaches 127.0.0.1 under its own name; the Host header gives it away."""
    refused = client.get("/health", headers={"host": "attacker.example:8001"})
    assert refused.status_code == 400
    assert refused.json() == {"detail": "The Host header must be 127.0.0.1 or localhost."}  # docs/API.md error shape
    assert client.post("/jobs", json={"url": "https://example.test/"}, headers={"host": "attacker.example"}).status_code == 400
    assert client.get("/health", headers={"host": "localhost:8001"}).status_code == 200
    assert client.get("/health", headers={"host": "127.0.0.1"}).status_code == 200


@pytest.mark.parametrize("host, allowed", [
    ("127.0.0.1:8001", True), ("localhost", True), ("localhost:3000", True),
    ("attacker.example", False), ("127.0.0.1.attacker.example:8001", False), ("", False), (None, False),
])
def test_host_allowed(host: str | None, allowed: bool) -> None:
    assert host_allowed(host) is allowed


FRAMING_HEADERS = {"content-security-policy": "frame-ancestors 'none'", "x-frame-options": "DENY",
                   "x-content-type-options": "nosniff"}


def test_every_response_forbids_framing_and_sniffing(make_client, ui_build: Path) -> None:
    """Another site must not show the UI in a frame and trick a click on Verify or Ablate (clickjacking)."""
    client = make_client(static_dir=ui_build)
    responses = [
        client.get("/"), client.get("/report/?id=20261024-153012-a1b2c3"), client.get("/health"),
        client.get("/jobs"), client.get("/jobs/20261024-153012-a1b2c3"), client.get("/jobs/not-an-id"),
        client.get("/health", headers={"host": "attacker.example"}),
        client.post("/jobs", json={"url": "https://example.test/"}, headers={"origin": "http://attacker.example"}),
    ]
    assert [response.status_code for response in responses] == [200, 200, 200, 200, 404, 422, 400, 403]
    for response in responses:
        assert {name: response.headers.get(name) for name in FRAMING_HEADERS} == FRAMING_HEADERS


def test_the_ui_scripts_are_served_as_javascript(make_client, ui_build: Path) -> None:
    """With nosniff a browser runs a script only when it is served with a JavaScript type."""
    (ui_build / "app.js").write_text("console.log(1);", encoding="utf-8")
    (ui_build / "app.css").write_text("body{}", encoding="utf-8")
    client = make_client(static_dir=ui_build)
    assert client.get("/app.js").headers["content-type"].startswith("text/javascript")
    assert client.get("/app.css").headers["content-type"].startswith("text/css")


def test_post_and_delete_from_another_origin_are_refused(client: TestClient) -> None:
    evil = {"origin": "http://attacker.example"}
    response = client.post("/jobs", json={"url": "https://example.test/"}, headers=evil)
    assert response.status_code == 403 and isinstance(response.json()["detail"], str)
    assert client.delete("/jobs/20200101-000000-abcdef", headers=evil).status_code == 403
    assert client.post("/jobs", json={"url": "https://example.test/"}, headers={"origin": DEV_UI}).status_code == 403


def test_post_from_the_servers_own_origin_is_accepted(client: TestClient) -> None:
    response = client.post("/jobs", json={"url": "https://example.test/"}, headers={"origin": "http://127.0.0.1:8001"})
    assert response.status_code == 201


def test_no_cors_headers_by_default(client: TestClient) -> None:
    response = client.get("/health", headers={"origin": DEV_UI})
    assert "access-control-allow-origin" not in response.headers


def test_dev_cors_allows_only_the_next_dev_server(make_client, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(DEV_CORS_VARIABLE, "1")
    client = make_client()
    assert client.get("/health", headers={"origin": DEV_UI}).headers["access-control-allow-origin"] == DEV_UI
    assert "access-control-allow-origin" not in client.get("/health", headers={"origin": "http://attacker.example"}).headers
    preflight = client.options("/jobs", headers={"origin": DEV_UI, "access-control-request-method": "POST",
                                                 "access-control-request-headers": "content-type"})
    assert preflight.status_code == 200
    assert client.post("/jobs", json={"url": "https://example.test/"}, headers={"origin": DEV_UI}).status_code == 201


def test_interactive_docs_that_load_cdn_scripts_are_off(client: TestClient) -> None:
    assert client.get("/docs").status_code == 404
    assert client.get("/redoc").status_code == 404


# ------------------------------------------------------------------------------------ static UI
@pytest.fixture
def ui_build(tmp_path: Path) -> Path:
    """A miniature static export laid out like web/out."""
    out = tmp_path / "out"
    for folder, title in (("", "New audit"), ("run", "Running"), ("report", "Report"), ("history", "History")):
        (out / folder).mkdir(parents=True, exist_ok=True)
        (out / folder / "index.html").write_text(f"<!doctype html><title>{title}</title>", encoding="utf-8")
    (out / "404.html").write_text("<!doctype html><title>Not found</title>", encoding="utf-8")
    return out


def test_static_ui_is_served_at_root_next_to_the_api(make_client, ui_build: Path) -> None:
    client = make_client(static_dir=ui_build)
    assert "New audit" in client.get("/").text
    assert "Running" in client.get("/run/?id=20261024-153012-a1b2c3").text
    assert "Report" in client.get("/report/").text and "History" in client.get("/history/").text
    redirect = client.get("/run?id=20261024-153012-a1b2c3", follow_redirects=False)
    assert redirect.status_code in (301, 307, 308)
    assert redirect.headers["location"].endswith("/run/?id=20261024-153012-a1b2c3")
    assert client.get("/health").json()["ok"] is True  # API routes win over the static mount
    assert client.get("/jobs").json() == []


def test_static_ui_does_not_serve_files_outside_its_folder(make_client, ui_build: Path) -> None:
    (ui_build.parent / "secret.txt").write_text("secret", encoding="utf-8")
    client = make_client(static_dir=ui_build)
    for path in ("/../secret.txt", "/%2e%2e/secret.txt", "/run/..%2F..%2Fsecret.txt"):
        assert "secret" not in client.get(path).text


def test_without_a_ui_build_root_says_how_to_build_it(client: TestClient) -> None:
    for route in ("/", "/run/", "/report/", "/history/"):
        response = client.get(route)
        assert response.status_code == 200 and response.headers["content-type"].startswith("text/html")
        text = html.unescape(response.text)
        assert "Build the UI with: cd web && npm run build" in text
        assert DISCLAIMER in text
