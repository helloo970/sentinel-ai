"""Hermes as an external agent: permissions, own token only for /agent/, MCP bridge."""

from __future__ import annotations

import json
from io import StringIO
from typing import Any

import pytest
from fastapi.testclient import TestClient

from sentinel import hermes
from sentinel.api.app import create_app
from sentinel.hermes.mcp import BackendError, McpServer
from sentinel.permissions.engine import decide
from sentinel.permissions.models import Autonomy, Outcome, Risk, Source
from sentinel.services import Services


def spec_with(svc: Services, risk: Risk):
    return next(s for s in svc.registry.all() if s.risk is risk)


@pytest.mark.parametrize(
    ("risk", "autonomy", "tainted", "outcome"),
    [
        (Risk.READ, Autonomy.OBSERVER, False, Outcome.EXECUTE),
        (Risk.LOW, Autonomy.AUTOPILOT, False, Outcome.EXECUTE),
        (Risk.LOW, Autonomy.ASSISTANT, False, Outcome.CONFIRM),
        (Risk.LOW, Autonomy.AUTOPILOT, True, Outcome.CONFIRM),
        (Risk.REVERSIBLE, Autonomy.GUARDIAN, False, Outcome.CONFIRM),
        (Risk.HIGH, Autonomy.GUARDIAN, False, Outcome.CONFIRM),
        (Risk.FINAL, Autonomy.GUARDIAN, False, Outcome.CONFIRM),
    ],
)
def test_external_agent_rights(svc: Services, risk, autonomy, tainted, outcome) -> None:
    assert (
        decide(spec_with(svc, risk), Source.EXTERNAL, autonomy, tainted=tainted).outcome is outcome
    )


def client(svc: Services, agent_token: str | None) -> TestClient:
    return TestClient(
        create_app(svc, token="t-ui", start_background=False, agent_token=agent_token),
        base_url="http://127.0.0.1:8765",
    )


def test_agent_token_only_opens_agent_paths(svc: Services) -> None:
    api = client(svc, "a-agent")
    agent = {"Authorization": "Bearer a-agent"}
    assert api.get("/agent/status", headers=agent).status_code == 200
    assert api.get("/status", headers=agent).status_code == 401
    assert api.get("/approvals", headers=agent).status_code == 401
    assert api.get("/agent/status", headers={"Authorization": "Bearer wrong"}).status_code == 401
    # without a configured agent token /agent/ only works with the app token
    assert client(svc, None).get("/agent/status", headers=agent).status_code == 401


def test_agent_call_goes_through_engine(svc: Services) -> None:
    api = client(svc, "a-agent")
    agent = {"Authorization": "Bearer a-agent"}
    tools = api.get("/agent/tools", headers=agent).json()
    assert any(t["name"] == "brain_lookup" for t in tools)
    result = api.post(
        "/agent/call",
        json={"action": "brain_remember", "params": {"label": "Hermes-Test", "summary": "x"}},
        headers=agent,
    ).json()
    assert result["status"] == "executed"
    node = svc.brain.store.find("Hermes-Test")
    assert node["origin"] == "ai" and node["source"] == "hermes"
    result = api.post(
        "/agent/call",
        json={"action": "brain_forget", "params": {"node_id": node["id"]}},
        headers=agent,
    ).json()
    assert result["status"] == "pending"
    assert (
        api.post("/agent/call", json={"action": "rm_rf", "params": {}}, headers=agent).json()[
            "status"
        ]
        == "denied"
    )
    entry = svc.audit.list(limit=5)[0]
    assert entry["actor"] == "agent:hermes" and entry["source"] == "external"


def test_halt_blocks_hermes_even_for_reading(svc: Services) -> None:
    svc.engine.halt(actor="user", via="test")
    outcome = svc.engine.request(
        "brain_lookup", {"query": "x"}, source=Source.EXTERNAL, actor="agent:hermes"
    )
    assert outcome.status == "denied"


def fake_transport(calls: list[tuple[str, str, Any]]):
    def call(method: str, path: str, body: Any) -> Any:
        calls.append((method, path, body))
        if path == "/agent/tools":
            return [
                {
                    "name": "brain_lookup",
                    "description": "[read] Suche",
                    "input_schema": {"type": "object"},
                }
            ]
        if path == "/agent/status":
            return {"halted": False, "autonomy": 2}
        if body and body["action"] == "offline":
            raise BackendError("SENTINEL is not reachable")
        return {"status": "denied" if body["action"] == "nope" else "executed", "message": "ok"}

    return call


def run_mcp(lines: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list]:
    calls: list = []
    out = StringIO()
    McpServer(fake_transport(calls)).serve(
        StringIO("\n".join(json.dumps(m) for m in lines) + "\nkaputt\n"), out
    )
    return [json.loads(line) for line in out.getvalue().splitlines()], calls


def test_mcp_protocol() -> None:
    replies, calls = run_mcp(
        [
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"protocolVersion": "2025-06-18"},
            },
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": "brain_lookup", "arguments": {"query": "Luna"}},
            },
            {
                "jsonrpc": "2.0",
                "id": 4,
                "method": "tools/call",
                "params": {"name": "nope", "arguments": {}},
            },
            {
                "jsonrpc": "2.0",
                "id": 5,
                "method": "tools/call",
                "params": {"name": "offline", "arguments": {}},
            },
            {"jsonrpc": "2.0", "id": 6, "method": "unknown/thing"},
        ]
    )
    by_id = {r.get("id"): r for r in replies}
    assert by_id[1]["result"]["serverInfo"]["name"] == "sentinel"
    names = [t["name"] for t in by_id[2]["result"]["tools"]]
    assert names == ["sentinel_status", "brain_lookup"]
    assert by_id[3]["result"]["isError"] is False
    assert (
        "POST",
        "/agent/call",
        {"action": "brain_lookup", "params": {"query": "Luna"}, "reason": "Hermes"},
    ) in calls
    assert by_id[4]["result"]["isError"] is True
    assert "not reachable" in by_id[5]["result"]["content"][0]["text"]
    assert by_id[6]["error"]["code"] == -32601
    assert by_id[None]["error"]["code"] == -32700  # broken line
    assert len(replies) == 7  # no answer to notifications


def test_token_and_port_files(tmp_path) -> None:
    assert hermes.stored_token() is None
    token = hermes.create_token()
    assert hermes.stored_token() == token and len(token) > 30
    hermes.write_info(tmp_path, 8800)
    assert hermes.read_port(tmp_path) == 8800
    assert token not in (tmp_path / "agent.json").read_text()
    hermes.delete_token()
    assert hermes.stored_token() is None
