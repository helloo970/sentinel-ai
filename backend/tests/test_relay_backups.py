"""Phone view (relay to the server) and backup watcher on the PC side."""

from __future__ import annotations

import pytest

from sentinel.monitor import rules
from sentinel.permissions import Severity
from sentinel.services import Services

NOW = 2_000_000_000.0
DAY = int(NOW // 86400)


def test_snapshot_contains_no_secrets(svc: Services) -> None:
    svc.alerts.handle(
        rules.Finding(
            rule="test",
            severity=Severity.WARN,
            title="Token sk-ant-api03-" + "x" * 40 + " gefunden",
            detail={"secret": "geheim"},
            fingerprint="fp1",
        )
    )
    snap = svc.relay.snapshot()
    assert snap["alerts"]["warn"] == 1 and snap["halted"] is False
    assert "sk-ant" not in str(snap) and "geheim" not in str(snap)
    assert set(snap) == {
        "halted",
        "autonomy",
        "alerts",
        "recent",
        "mode",
        "quiet",
        "paired",
        "results",
    }


def test_halt_command_from_phone(svc: Services, monkeypatch) -> None:
    spoken: list[str] = []
    monkeypatch.setattr(svc.voice, "say", spoken.append)
    assert svc.relay.handle([{"id": "a1", "action": "halt"}]) == 1
    assert svc.engine.halted
    assert spoken and "phone" in spoken[0]
    audit = svc.db.query("SELECT actor, action FROM audit WHERE action = 'emergency_stop'")
    assert audit[-1]["actor"] == "phone"
    # same ID not twice
    assert svc.relay.handle([{"id": "a1", "action": "halt"}]) == 0


@pytest.mark.parametrize("action", ["resume", "release", "run_routine", "shell"])
def test_phone_can_never_release_or_run_actions(svc: Services, action: str) -> None:
    svc.engine.halt(actor="user")
    assert svc.relay.handle([{"id": "x", "action": action, "params": {}}]) == 0
    assert svc.engine.halted  # stays locked
    denied = svc.db.query("SELECT decision FROM audit WHERE action = ?", (f"relay:{action}",))
    assert denied[-1]["decision"] == "deny"


async def test_tick_without_server_config_does_nothing(svc: Services) -> None:
    assert svc.relay.enabled is False
    assert await svc.relay.tick() > 5


async def test_tick_old_agent_backs_off(svc: Services, monkeypatch) -> None:
    monkeypatch.setattr(type(svc.relay), "enabled", property(lambda self: True))

    async def old_agent(payload):
        return None

    monkeypatch.setattr(svc.server, "post_relay", old_agent)
    assert await svc.relay.tick() == 300
    assert svc.relay.status()["supported"] is False


async def test_tick_delivers_halt(svc: Services, monkeypatch) -> None:
    monkeypatch.setattr(type(svc.relay), "enabled", property(lambda self: True))
    sent: list[dict] = []

    async def agent(payload):
        sent.append(payload)
        return [{"id": "z", "action": "halt"}] if len(sent) == 1 else []

    monkeypatch.setattr(svc.server, "post_relay", agent)
    assert await svc.relay.tick() < 1  # report again right away
    assert svc.engine.halted
    await svc.relay.tick()
    assert sent[-1]["halted"] is True


# ---------------------------------------------------------------------- Backups


def backups(**entry) -> dict:
    return {"available": True, "stale": False, "paths": [{"path": "/srv/backup", **entry}]}


def test_backup_fresh_is_quiet() -> None:
    data = backups(exists=True, newest=NOW - 3600, files=10)
    assert rules.check_backups(data, max_age_h=26, now=NOW, day=DAY) == []


def test_backup_too_old_missing_empty_and_stale() -> None:
    old = rules.check_backups(
        backups(exists=True, newest=NOW - 50 * 3600), max_age_h=26, now=NOW, day=DAY
    )
    assert old[0].severity is Severity.WARN and "50 hours" in old[0].title
    missing = rules.check_backups(backups(exists=False), max_age_h=26, now=NOW, day=DAY)
    assert "is missing" in missing[0].title
    empty = rules.check_backups(backups(exists=True, newest=None), max_age_h=26, now=NOW, day=DAY)
    assert "is empty" in empty[0].title
    stale = rules.check_backups(
        {"available": True, "stale": True, "paths": []}, max_age_h=26, now=NOW, day=DAY
    )
    assert "no longer being updated" in stale[0].title
    assert rules.check_backups({"available": False}, max_age_h=26, now=NOW, day=DAY) == []


def test_backup_rule_in_server_extras() -> None:
    data = {
        "backups": backups(exists=True, newest=NOW - 100 * 3600),
        "backup_max_age_h": 48,
        "now": NOW,
        "day_bucket": DAY,
    }
    found = rules.check_server_extras(data, {})
    assert [f.rule for f in found] == ["server_backup"]


async def test_relay_backs_off_when_server_rejects_pc(svc, monkeypatch) -> None:
    from sentinel.server_agent import relay as relay_mod
    from sentinel.server_agent.client import ServerForbidden, http_error

    assert isinstance(http_error("relay", 403), ServerForbidden)
    assert "IP" in str(http_error("relay", 403))
    monkeypatch.setattr(type(svc.relay), "enabled", property(lambda self: True))

    async def rejected(payload):
        raise http_error("relay", 403)

    monkeypatch.setattr(svc.server, "post_relay", rejected)
    assert await svc.relay.tick() == relay_mod.FORBIDDEN_BACKOFF_S
    assert "rejects this PC" in svc.relay.error
