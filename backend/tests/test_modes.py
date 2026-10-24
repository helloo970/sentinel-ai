"""Gaming/focus mode, quiet mode, weekly tidy-up and quick commands."""

from __future__ import annotations

import json
import os
import time
from datetime import datetime
from pathlib import Path

import pytest

from sentinel.automation.modes import parse_modes
from sentinel.automation.programs import Program
from sentinel.chat import quick
from sentinel.permissions import Autonomy, Severity, Source
from sentinel.services import Services


@pytest.fixture
def fake_programs(svc: Services, tmp_path: Path, monkeypatch) -> dict[str, list[str]]:
    """Programs without real processes: remembers starts and closes."""
    calls: dict[str, list[str]] = {"launch": [], "close": []}
    running: set[str] = {"discord"}
    catalog = {
        name: Program(name, name.title(), tmp_path / f"{name}.exe")
        for name in ("steam", "discord", "spotify")
    }

    def launch(program: Program) -> int:
        calls["launch"].append(program.name)
        running.add(program.name)
        return 4242

    def close(program: Program, timeout: float = 3.0) -> int:
        calls["close"].append(program.name)
        was = program.name in running
        running.discard(program.name)
        return int(was)

    monkeypatch.setattr(svc.programs, "all", lambda: dict(catalog))
    monkeypatch.setattr(svc.programs, "launch", launch)
    monkeypatch.setattr(svc.programs, "close", close)
    monkeypatch.setattr(svc.programs, "running", lambda p: [object()] if p.name in running else [])
    return calls


def test_parse_modes_rejects_invalid_entries() -> None:
    modes = parse_modes(
        {
            "ok": {"title": "OK", "launch": ["steam"], "close": [], "quiet": True},
            "Bad Name": {"launch": []},
            "badprog": {"launch": ["C:\\evil.exe"]},
            "toolong": {"launch": [], "minutes": 10_000},
            "boolmin": {"launch": [], "minutes": True},
        }
    )
    assert list(modes) == ["ok"]


def test_default_modes_file_created(svc: Services) -> None:
    raw = json.loads((svc.config.config_dir / "modes.json").read_text(encoding="utf-8"))
    assert {"gaming", "fokus"} <= set(raw)
    assert svc.modes.get("gaming").quiet is True


def test_gaming_mode_start_and_stop_restores_everything(
    svc: Services, fake_programs: dict[str, list[str]]
) -> None:
    outcome = svc.engine.request("start_mode", {"name": "gaming"}, source=Source.USER, actor="user")
    assert outcome.status == "executed"
    # Discord was already running -> not restarted, and not closed on stop either
    assert fake_programs["launch"] == ["steam"]
    assert svc.notifier.quiet is True
    assert svc.modes.active()["launched"] == ["steam"]

    stop = svc.engine.request("stop_mode", {}, source=Source.USER, actor="user")
    assert stop.status == "executed"
    assert fake_programs["close"] == ["steam"]
    assert svc.notifier.quiet is False
    assert svc.modes.active() is None


def test_focus_mode_closes_and_relaunches_with_timer(
    svc: Services, fake_programs: dict[str, list[str]]
) -> None:
    outcome = svc.engine.request(
        "start_mode", {"name": "fokus", "minutes": 50}, source=Source.USER, actor="user"
    )
    assert outcome.status == "executed" and "until" in outcome.message
    state = svc.modes.active()
    assert state["closed"] == ["discord"] and state["launched"] == ["spotify"]
    assert 49 * 60 < state["ends"] - time.time() <= 50 * 60

    assert svc.modes.tick(now=time.time() + 10) is False
    assert svc.modes.tick(now=state["ends"] + 1) is True
    assert "discord" in fake_programs["launch"]  # started again
    assert "spotify" in fake_programs["close"]
    assert svc.modes.active() is None


def test_mode_undo_and_unknown_mode(svc: Services, fake_programs: dict[str, list[str]]) -> None:
    outcome = svc.engine.request("start_mode", {"name": "gaming"}, source=Source.USER, actor="user")
    assert svc.engine.undo(outcome.undo_id).status == "executed"
    assert svc.modes.active() is None
    denied = svc.engine.request("start_mode", {"name": "hacker"}, source=Source.USER, actor="u")
    assert denied.status == "denied"


def test_mode_closing_needs_confirmation_in_assistant_level(
    svc: Services, fake_programs: dict[str, list[str]]
) -> None:
    svc.engine.set_autonomy(Autonomy.ASSISTANT, actor="test")
    outcome = svc.engine.request(
        "start_mode", {"name": "fokus"}, source=Source.ASSISTANT, actor="jarvis"
    )
    assert outcome.status == "executed"
    # Closing is "undoable" -> at level 1 only as an approval
    assert svc.modes.active()["closed"] == []
    assert any(a["action"] == "close_program" for a in svc.engine.list_approvals())


def test_mode_blocked_by_killswitch(svc: Services, fake_programs: dict[str, list[str]]) -> None:
    svc.engine.halt(actor="test")
    outcome = svc.engine.request("start_mode", {"name": "gaming"}, source=Source.USER, actor="user")
    assert outcome.status == "denied" and fake_programs["launch"] == []


def test_quiet_mode_silences_all_but_critical(svc: Services, monkeypatch) -> None:
    spoken: list[str] = []
    monkeypatch.setattr(svc.voice, "say", spoken.append)
    svc.notifier.set_quiet(True)
    svc.notifier.notify("Info", "quiet", speak=True)
    svc.notifier.notify("Alarm", "loud", speak=True, severity=Severity.CRITICAL)
    assert spoken == ["Alarm. loud"]


# ---------------------------------------------------------------------- Tidy-up


def _sunday(hour: int, minute: int = 0) -> float:
    return datetime(2026, 10, 18, hour, minute).timestamp()  # Sunday


def test_cleanup_due_once_per_week(svc: Services) -> None:
    sched = svc.scheduler
    assert sched.cleanup_due(_sunday(9, 59)) is False
    assert sched.cleanup_due(_sunday(10, 0)) is True
    assert sched.cleanup_due(_sunday(18)) is False
    assert sched.cleanup_due(datetime(2026, 10, 20, 12).timestamp()) is False  # Tuesday
    assert sched.cleanup_due(datetime(2026, 10, 25, 11).timestamp()) is True  # next week


def test_cleanup_disabled(svc: Services) -> None:
    svc.config.downloads_cleanup = False
    assert svc.scheduler.cleanup_due(_sunday(11)) is False


def _old_file(path: Path) -> None:
    path.write_bytes(b"x")
    old = time.time() - 3600
    os.utime(path, (old, old))


def test_cleanup_runs_as_rule_with_week_long_undo(svc: Services, managed: Path) -> None:
    _old_file(managed / "photo.jpg")
    _old_file(managed / "invoice.pdf")
    results = svc.scheduler.run_cleanup()
    assert results == [{"folder": str(managed.resolve()), "status": "executed"}]
    assert (managed / "Images" / "photo.jpg").exists()
    undo = svc.engine.list_undo()[0]
    assert undo["action"] == "organize_folder"
    assert undo["expires"] - undo["created"] == pytest.approx(7 * 24 * 3600)
    audit = svc.db.query("SELECT actor, source FROM audit WHERE action = 'organize_folder'")
    assert audit[-1]["actor"] == "rule:weekly_cleanup" and audit[-1]["source"] == "rule"
    assert svc.engine.undo(undo["id"]).status == "executed"
    assert (managed / "photo.jpg").exists()


def test_cleanup_skips_empty_folder_and_asks_in_observer(svc: Services, managed: Path) -> None:
    assert svc.scheduler.run_cleanup() == []
    _old_file(managed / "a.zip")
    svc.engine.set_autonomy(Autonomy.OBSERVER, actor="test")
    assert svc.scheduler.run_cleanup()[0]["status"] == "denied"  # Observer: rules only report


# ---------------------------------------------------------------------- Quick commands


@pytest.mark.parametrize(
    ("text", "action", "params"),
    [
        ("Gaming mode", "start_mode", {"name": "gaming"}),
        ("Jarvis, gaming mode on!", "start_mode", {"name": "gaming"}),
        ("start the focus mode for 45 minutes", "start_mode", {"name": "fokus", "minutes": 45}),
        ("focus mode 30 min", "start_mode", {"name": "fokus", "minutes": 30}),
        ("Mode off", "stop_mode", {}),
        ("Gaming mode off.", "stop_mode", {}),
        ("stop the mode", "stop_mode", {}),
    ],
)
def test_quick_commands(text: str, action: str, params: dict) -> None:
    command = quick.match(text)
    assert command is not None and command.action == action and command.params == params


@pytest.mark.parametrize(
    "text",
    ["What is a gaming mode?", "please explain the focus mode to me", "", "Mode"],
)
def test_quick_commands_ignore_questions(text: str) -> None:
    assert quick.match(text) is None


async def test_quick_command_works_without_llm(
    svc: Services, scripted, fake_programs: dict[str, list[str]]
) -> None:
    result = await svc.chat.send("Gaming mode on")
    assert "Gaming mode on" in result["text"]
    assert fake_programs["launch"] == ["steam"]
    assert scripted.calls == []  # no language model needed
