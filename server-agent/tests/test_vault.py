from __future__ import annotations

import base64
import json
import threading
import urllib.error
import urllib.request

import pytest

from sentinel_agent import vault
from sentinel_agent.server import AgentConfig, build_server

TOKEN = "b" * 40
BLOB = vault.MAGIC + b"\x00" * 60


@pytest.fixture
def server(tmp_path):
    config = AgentConfig(
        "127.0.0.1", 0, "", "", TOKEN, ["127.0.0.1"], services=[], vault_dir=str(tmp_path)
    )
    srv = build_server(config, tls=False)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def call(url: str, body: dict | None = None, token: str | None = TOKEN) -> tuple[int, dict]:
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(url, data=data, method="POST" if data else "GET")  # noqa: S310
    request.add_header("Content-Type", "application/json")
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request, timeout=5) as response:  # noqa: S310
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


def b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode()


def test_store_and_fetch_latest(server: str) -> None:
    assert call(f"{server}/v1/vault/brain")[0] == 404
    status, body = call(f"{server}/v1/vault/brain", {"data": b64(BLOB)})
    assert status == 200 and body["bytes"] == len(BLOB)
    status, body = call(f"{server}/v1/vault/brain")
    assert status == 200 and base64.b64decode(body["data"]) == BLOB


def test_vault_requires_token(server: str) -> None:
    assert call(f"{server}/v1/vault/brain", {"data": b64(BLOB)}, token=None)[0] == 401
    assert call(f"{server}/v1/vault/brain", token="wrong")[0] == 401


@pytest.mark.parametrize(
    ("name", "data"),
    [
        ("brain", "not base64!"),
        ("brain", b64(b"plain text, not encrypted")),
        ("../etc", b64(BLOB)),
        ("BRAIN", b64(BLOB)),
    ],
)
def test_vault_rejects_bad_uploads(server: str, name: str, data: str) -> None:
    assert call(f"{server}/v1/vault/{name}", {"data": data})[0] in (400, 404)


def test_keeps_only_last_versions(tmp_path) -> None:
    folder = tmp_path / "brain"
    folder.mkdir()
    for index in range(vault.KEEP + 2):
        (folder / f"2026010{index}-000000.bin").write_bytes(BLOB)
    vault.store(str(tmp_path), "brain", {"data": b64(BLOB)})
    assert len(list(folder.glob("*.bin"))) == vault.KEEP
