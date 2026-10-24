"""Protection of the local API: token, Host header, Origin, CORS."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from sentinel.api.app import create_app
from sentinel.services import Services

TOKEN = "test-token-123"
BASE = "http://127.0.0.1:8765"


@pytest.fixture
def client(svc: Services) -> TestClient:
    app = create_app(svc, token=TOKEN, start_background=False)
    return TestClient(app, base_url=BASE)


def auth() -> dict[str, str]:
    return {"Authorization": f"Bearer {TOKEN}"}


def test_health_is_public(client: TestClient) -> None:
    assert client.get("/health").status_code == 200


@pytest.mark.parametrize(
    "headers",
    [{}, {"Authorization": "Bearer wrong"}, {"Authorization": TOKEN}, {"Authorization": "Basic x"}],
)
def test_requests_without_valid_token_are_rejected(client: TestClient, headers: dict) -> None:
    assert client.get("/status", headers=headers).status_code == 401


def test_valid_token_works(client: TestClient) -> None:
    response = client.get("/status", headers=auth())
    assert response.status_code == 200
    assert response.json()["autonomy"] == 2


@pytest.mark.parametrize("host", ["evil.example", "127.0.0.1", "127.0.0.1:9999", "0.0.0.0:8765"])
def test_wrong_host_header_is_rejected(client: TestClient, host: str) -> None:
    response = client.get("/status", headers={**auth(), "Host": host})
    assert response.status_code == 421


def test_foreign_origin_is_rejected(client: TestClient) -> None:
    response = client.get("/status", headers={**auth(), "Origin": "https://evil.example"})
    assert response.status_code == 403


def test_own_origin_gets_cors_headers(client: TestClient) -> None:
    response = client.options(
        "/status",
        headers={
            "Origin": "http://tauri.localhost",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "authorization",
        },
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://tauri.localhost"


def test_emergency_stop_via_api(client: TestClient, svc: Services) -> None:
    assert client.post("/control/halt", json={"via": "ui"}, headers=auth()).json()["halted"]
    assert svc.engine.halted
    result = client.post(
        "/actions/system_control", json={"params": {"command": "mute"}}, headers=auth()
    ).json()
    assert result["status"] == "denied"
    client.post("/control/resume", headers=auth())
    assert not svc.engine.halted


def test_autonomy_validation(client: TestClient) -> None:
    assert client.put("/control/autonomy", json={"level": 5}, headers=auth()).status_code == 422
    assert client.put("/control/autonomy", json={"level": 1}, headers=auth()).json() == {
        "autonomy": 1
    }


def test_unknown_action_via_api_is_denied(client: TestClient) -> None:
    result = client.post(
        "/actions/run_shell", json={"params": {"cmd": "whoami"}}, headers=auth()
    ).json()
    assert result["status"] == "denied"


def test_settings_do_not_leak_secrets(tmp_path, scripted) -> None:
    from pydantic import SecretStr

    from conftest import make_settings
    from sentinel.services import build_services

    services = build_services(
        make_settings(
            tmp_path,
            anthropic_api_key=SecretStr("sk-ant-supersecretvalue123"),
            ntfy_token=SecretStr("tk_secretntfytoken"),
        ),
        llm=scripted,
    )
    app = create_app(services, token=TOKEN, start_background=False)
    test_client = TestClient(app, base_url=BASE)
    for path in ("/settings", "/status"):
        body = test_client.get(path, headers=auth()).text
        assert "supersecretvalue" not in body
        assert "secretntfytoken" not in body
    services.db.close()


def test_backend_refuses_non_loopback_bind() -> None:
    from pydantic import ValidationError

    from sentinel.config import Settings

    with pytest.raises(ValidationError):
        Settings(_env_file=None, host="0.0.0.0")  # noqa: S104


def test_backend_writes_log_when_started_without_console(tmp_path, monkeypatch) -> None:
    import sys

    from sentinel.__main__ import _ensure_streams

    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)
    _ensure_streams(tmp_path)
    print("hallo", file=sys.stderr)
    sys.stderr.flush()
    assert "hallo" in (tmp_path / "backend.log").read_text(encoding="utf-8")
    sys.stderr.close()
