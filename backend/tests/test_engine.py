"""Permissions engine: allowlist, parameters, emergency stop, approvals, undo, rate limit, audit."""

from __future__ import annotations

import sqlite3
from typing import Any

import pytest
from pydantic import Field

from sentinel.permissions import (
    ActionDenied,
    ActionRegistry,
    ActionResult,
    ActionSpec,
    PermissionEngine,
    Risk,
    Severity,
    Source,
    StrictParams,
)
from sentinel.services import Services


class CountParams(StrictParams):
    value: int = Field(ge=0, le=100)


class Clock:
    def __init__(self) -> None:
        self.now = 1_000_000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def setup(svc: Services) -> tuple[PermissionEngine, dict[str, Any], Clock, list[tuple[str, dict]]]:
    state: dict[str, Any] = {"value": 0, "runs": 0}
    events: list[tuple[str, dict]] = []
    clock = Clock()
    registry = ActionRegistry()

    def set_value(p: CountParams, _: Services) -> ActionResult:
        old = state["value"]
        state["value"] = p.value
        state["runs"] += 1
        return ActionResult(f"value={p.value}", undo={"old": old})

    def undo_value(data: dict[str, Any], _: Services) -> str:
        state["value"] = data["old"]
        return "restored"

    def check_value(p: CountParams, _: Services) -> None:
        if p.value == 13:
            raise ActionDenied("13 is forbidden")

    for name, risk in [
        ("low", Risk.LOW),
        ("rev", Risk.REVERSIBLE),
        ("high", Risk.HIGH),
        ("final", Risk.FINAL),
        ("read", Risk.READ),
    ]:
        registry.register(
            ActionSpec(
                name,
                name,
                name,
                risk,
                CountParams,
                set_value,
                undo=undo_value,
                check=check_value,
                guardian_auto=name == "high",
            )
        )
    engine = PermissionEngine(
        registry=registry,
        db=svc.db,
        audit=svc.audit,
        store=svc.store,
        services=svc,
        default_autonomy=2,
        max_autonomous_per_hour=10,
        undo_window_s=60,
        approval_ttl_s=600,
        on_event=lambda k, d: events.append((k, d)),
        clock=clock,
    )
    return engine, state, clock, events


def test_unknown_action_is_denied_and_audited(setup, svc: Services) -> None:
    engine, *_ = setup
    outcome = engine.request("format_disk", {}, source=Source.USER, actor="user")
    assert outcome.status == "denied"
    assert "allowlist" in outcome.message
    assert svc.audit.list(1)[0]["action"] == "format_disk"
    assert svc.audit.list(1)[0]["decision"] == "deny"


@pytest.mark.parametrize("params", [{"value": 101}, {"value": "x"}, {"value": 1, "cmd": "rm"}, {}])
def test_invalid_params_are_denied(setup, params: dict) -> None:
    engine, state, *_ = setup
    outcome = engine.request("low", params, source=Source.USER, actor="user")
    assert outcome.status == "denied"
    assert state["runs"] == 0


def test_check_can_deny(setup) -> None:
    engine, state, *_ = setup
    outcome = engine.request("low", {"value": 13}, source=Source.USER, actor="user")
    assert outcome.status == "denied" and "forbidden" in outcome.message
    assert state["runs"] == 0


def test_emergency_stop_blocks_everything_but_user_reads(setup) -> None:
    engine, state, *_ = setup
    engine.halt(actor="user")
    assert engine.request("low", {"value": 1}, source=Source.USER, actor="user").status == "denied"
    assert engine.request("read", {"value": 1}, source=Source.RULE, actor="r").status == "denied"
    assert engine.request("read", {"value": 1}, source=Source.USER, actor="u").status == "executed"
    engine.resume(actor="user")
    assert (
        engine.request("low", {"value": 2}, source=Source.USER, actor="user").status == "executed"
    )


def test_approval_flow(setup) -> None:
    engine, state, *_ = setup
    pending = engine.request("high", {"value": 7}, source=Source.ASSISTANT, actor="jarvis")
    assert pending.status == "pending" and state["runs"] == 0
    assert len(engine.list_approvals()) == 1
    done = engine.approve(pending.approval_id)
    assert done.status == "executed" and state["value"] == 7
    # Approving a second time does not work
    assert engine.approve(pending.approval_id).status == "denied"


def test_reject_and_expiry(setup) -> None:
    engine, state, clock, _ = setup
    first = engine.request("final", {"value": 1}, source=Source.USER, actor="user")
    assert engine.reject(first.approval_id)
    assert engine.approve(first.approval_id).status == "denied"
    second = engine.request("final", {"value": 2}, source=Source.USER, actor="user")
    clock.now += 601
    assert engine.approve(second.approval_id).status == "denied"
    assert state["runs"] == 0


def test_halt_blocks_approval(setup) -> None:
    engine, state, *_ = setup
    pending = engine.request("high", {"value": 5}, source=Source.ASSISTANT, actor="jarvis")
    engine.halt(actor="user")
    assert engine.approve(pending.approval_id).status == "denied"
    assert state["runs"] == 0


def test_undo_within_window(setup) -> None:
    engine, state, clock, events = setup
    outcome = engine.request("rev", {"value": 9}, source=Source.ASSISTANT, actor="jarvis")
    assert outcome.status == "executed" and outcome.undo_id
    assert any(k == "action" and d["undo_id"] == outcome.undo_id for k, d in events)
    clock.now += 30
    assert engine.undo(outcome.undo_id).status == "executed"
    assert state["value"] == 0
    assert engine.undo(outcome.undo_id).status == "denied"


def test_undo_after_window_is_denied(setup) -> None:
    engine, state, clock, _ = setup
    outcome = engine.request("rev", {"value": 9}, source=Source.ASSISTANT, actor="jarvis")
    clock.now += 61
    assert engine.undo(outcome.undo_id).status == "denied"
    assert state["value"] == 9


def test_rate_limit_drops_to_level_1(setup) -> None:
    engine, state, clock, events = setup
    for i in range(10):
        assert engine.request("rev", {"value": i}, source=Source.ASSISTANT, actor="j").status == (
            "executed"
        )
    eleventh = engine.request("rev", {"value": 50}, source=Source.ASSISTANT, actor="j")
    assert eleventh.status == "pending"
    assert int(engine.autonomy) == 1
    assert any(k == "warning" for k, _ in events)
    # Even low risk now needs confirmation until the hour is over
    assert engine.request("low", {"value": 1}, source=Source.ASSISTANT, actor="j").status == (
        "pending"
    )
    clock.now += 3601
    assert engine.request("low", {"value": 1}, source=Source.ASSISTANT, actor="j").status == (
        "executed"
    )


def test_user_actions_do_not_count_as_autonomous(setup) -> None:
    engine, *_ = setup
    for i in range(15):
        engine.request("rev", {"value": i}, source=Source.USER, actor="user")
    assert engine.autonomous_count() == 0
    assert int(engine.autonomy) == 2


def test_guardian_rule_executes_high(setup) -> None:
    engine, state, *_ = setup
    engine.set_autonomy(3, actor="user")
    outcome = engine.request(
        "high", {"value": 3}, source=Source.RULE, actor="rule:x", severity=Severity.CRITICAL
    )
    assert outcome.status == "executed"


def test_audit_chain_is_valid_and_append_only(setup, svc: Services) -> None:
    engine, *_ = setup
    engine.request("low", {"value": 1}, source=Source.USER, actor="user")
    engine.request("nope", {}, source=Source.USER, actor="user")
    assert svc.audit.verify()
    with pytest.raises(sqlite3.DatabaseError):
        svc.db.execute("UPDATE audit SET decision = 'execute'")
    with pytest.raises(sqlite3.DatabaseError):
        svc.db.execute("DELETE FROM audit")


def test_audit_redacts_secrets(setup, svc: Services) -> None:
    svc.audit.append(
        actor="user",
        source="user",
        action="x",
        params={"note": "token=abcd1234efgh5678ijkl"},  # gitleaks:allow
        decision="deny",
        detail="Bearer abcdefghijklmnopqrstuvwxyz123456",
    )
    row = svc.audit.list(1)[0]
    assert "abcd1234efgh5678ijkl" not in row["params"]
    assert "abcdefghijklmnopqrstuvwxyz123456" not in row["detail"]
    assert svc.audit.verify()


def test_no_approve_or_shell_action_is_registered(svc: Services) -> None:
    names = {s.name for s in svc.registry.all()}
    forbidden = {
        "approve",
        "shell",
        "run_command",
        "exec",
        "eval",
        "powershell",
        "cmd",
        "delete_file",
        "firewall",
    }
    assert not names & forbidden
    tool_names = {t["name"] for t in svc.chat.tools()}
    assert "restore_quarantine" not in tool_names
