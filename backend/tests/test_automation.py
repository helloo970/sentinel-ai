"""Automation: programs (allowlist), reminders, routines, plugins."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest

from sentinel.actions.automation import ReminderParams, due_time
from sentinel.automation.programs import parse_programs
from sentinel.automation.routines import parse_routines
from sentinel.permissions import Source
from sentinel.services import Services


def test_program_entries_are_validated(tmp_path: Path) -> None:
    exe = tmp_path / "tool.exe"
    exe.write_text("")
    programs = parse_programs(
        {
            "tool": {"path": str(exe), "args": ["--quiet"]},
            "Bad Name": {"path": str(exe)},
            "relative": {"path": "tool.exe"},
            "script": {"path": str(tmp_path / "x.ps1")},
            "badargs": {"path": str(exe), "args": "rm -rf"},
        }
    )
    assert list(programs) == ["tool"]
    assert programs["tool"].args == ("--quiet",)


@pytest.mark.parametrize("name", ["cmd", "powershell", "../notepad", "notepad; calc", "NOTEPAD"])
def test_unknown_or_malformed_program_is_denied(svc: Services, name: str) -> None:
    outcome = svc.engine.request(
        "launch_program", {"name": name}, source=Source.ASSISTANT, actor="jarvis"
    )
    assert outcome.status == "denied"


def test_program_path_cannot_be_chosen_by_ai(svc: Services) -> None:
    outcome = svc.engine.request(
        "launch_program",
        {"name": "notepad", "path": "C:/evil.exe"},
        source=Source.ASSISTANT,
        actor="jarvis",
    )
    assert outcome.status == "denied"


def test_due_time() -> None:
    now = datetime(2026, 10, 20, 10, 0)
    assert (
        due_time(ReminderParams(text="a", minutes=30), now)
        == datetime(2026, 10, 20, 10, 30).timestamp()
    )
    assert (
        due_time(ReminderParams(text="a", at="09:00"), now)
        == datetime(2026, 10, 21, 9, 0).timestamp()
    )
    with pytest.raises(ValueError):
        ReminderParams(text="a")
    with pytest.raises(ValueError):
        ReminderParams(text="a", minutes=5, at="10:00")


def test_reminder_fires_and_notifies(svc: Services) -> None:
    events: list[tuple[str, dict]] = []
    svc.bus.publish = lambda kind, data=None: events.append((kind, data or {}))  # type: ignore
    reminder = svc.reminders.add("Tee", due=1.0)
    fired = svc.reminders.fire_due(now=2.0)
    assert [r["id"] for r in fired] == [reminder["id"]]
    assert any(k == "notification" and d["message"] == "Tee" for k, d in events)
    assert svc.reminders.fire_due(now=3.0) == []


def test_reminder_with_secret_is_denied(svc: Services) -> None:
    outcome = svc.engine.request(
        "set_reminder",
        {"text": "Passwort: hunter2x", "minutes": 5},
        source=Source.ASSISTANT,
        actor="jarvis",
    )
    assert outcome.status == "denied"


def write_routines(svc: Services, data: dict) -> None:
    svc.routines.file.write_text(json.dumps(data), encoding="utf-8")


def test_routine_runs_each_step_through_engine(svc: Services) -> None:
    write_routines(
        svc,
        {
            "abend": {
                "title": "Abend",
                "steps": [
                    {"action": "set_reminder", "params": {"text": "Schlafen", "minutes": 60}},
                    {"action": "list_reminders", "params": {}},
                ],
            }
        },
    )
    outcome = svc.engine.request(
        "run_routine", {"name": "abend"}, source=Source.ASSISTANT, actor="jarvis"
    )
    assert outcome.status == "executed"
    assert [s["status"] for s in outcome.data] == ["executed", "executed"]
    actors = {row["actor"] for row in svc.audit.list(20)}
    assert "routine:abend" in actors


def test_routine_with_high_risk_step_is_denied(svc: Services) -> None:
    write_routines(
        svc,
        {
            "boese": {"steps": [{"action": "kill_process", "params": {"pid": 999}}]},
            "loop": {"steps": [{"action": "run_routine", "params": {"name": "loop"}}]},
            "unbekannt": {"steps": [{"action": "shell", "params": {}}]},
        },
    )
    for name in ("boese", "loop", "unbekannt", "fehlt"):
        outcome = svc.engine.request(
            "run_routine", {"name": name}, source=Source.USER, actor="user"
        )
        assert outcome.status == "denied", name


def test_parse_routines_rejects_invalid() -> None:
    parsed = parse_routines(
        {
            "ok": {"steps": [{"action": "system_info"}]},
            "leer": {"steps": []},
            "kaputt": {"steps": ["x"]},
            "Bad Name": {"steps": [{"action": "system_info"}]},
        }
    )
    assert list(parsed) == ["ok"]


def make_plugin(svc: Services, name: str, data: dict) -> None:
    folder = svc.config.plugins_dir / name
    folder.mkdir(parents=True)
    (folder / "plugin.json").write_text(json.dumps(data), encoding="utf-8")


def test_plugins_are_disabled_by_default(svc: Services) -> None:
    make_plugin(
        svc,
        "demo",
        {
            "version": "1.0.0",
            "routines": {"start": {"steps": [{"action": "system_info"}]}},
            "feeds": [
                {"region": "de", "name": "Demo", "url": "https://example.org/rss"},
                {"region": "de", "name": "Unsicher", "url": "http://example.org/rss"},
            ],
        },
    )
    assert svc.plugins.list()[0]["enabled"] is False
    assert "demo-start" not in svc.routines.all()
    svc.plugins.set_enabled("demo", True)
    assert "demo-start" in svc.routines.all()
    assert [f["name"] for f in svc.plugins.feeds()] == ["Demo"]


def test_invalid_plugin_is_reported(svc: Services) -> None:
    folder = svc.config.plugins_dir / "kaputt"
    folder.mkdir(parents=True)
    (folder / "plugin.json").write_text("{nope", encoding="utf-8")
    assert svc.plugins.list()[0]["error"]
    with pytest.raises(KeyError):
        svc.plugins.set_enabled("gibtsnicht", True)
