"""Defense commands as plugins: loading, approval per file version, PIN, isolation."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from sentinel.api.app import create_app
from sentinel.monitor.rules import Finding
from sentinel.permissions import Risk, Severity, Source
from sentinel.pin import PinError
from sentinel.response.base import ResponseCommand, RiskLevel
from sentinel.services import Services

PIN = "482913"

GOOD_PLUGIN = """
from sentinel.response.base import CommandResult, ResponseCommand, RiskLevel

class Notice(ResponseCommand):
    name = "notice"
    description = "Shows a notice."
    risk_level = RiskLevel.LOW
    requires_confirm = False

    def can_run(self, event):
        return True

    def run(self, event, ctx):
        return CommandResult(ok=True, message="Notice shown")
"""


def write_plugin(svc: Services, name: str, body: str) -> Path:
    folder = svc.config.data_dir / "response_plugins"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    path.write_text(textwrap.dedent(body), encoding="utf-8")
    return path


def info(svc: Services, file: str) -> dict:
    return next(i for i in svc.responses.list() if i["file"] == file)


def make_alert(svc: Services, detail: dict, severity: Severity = Severity.CRITICAL) -> int:
    finding = Finding(
        rule="test",
        severity=severity,
        title="Test",
        detail=detail,
        fingerprint=f"fp-{time.time_ns()}",
        notify=False,
    )
    alert_id = svc.alerts.add(finding)
    assert alert_id
    return alert_id


@pytest.fixture
def pinned(svc: Services) -> Services:
    svc.pin.set_pin(PIN)
    return svc


# ---------------------------------------------------------------------- Laden


def test_builtin_plugins_are_loaded_and_hidden_from_the_model(svc: Services) -> None:
    loaded = {i["name"] for i in svc.responses.list() if i["status"] == "loaded"}
    assert {"kill_process", "quarantine_file", "rescue_files", "raise_alarm"} <= loaded
    assert not {"block_ip", "isolate_network"} & loaded
    for name in loaded:
        spec = svc.registry.get(f"response_{name}")
        assert spec is not None and spec.llm_exposed is False
    assert not any(t["name"].startswith("response_") for t in svc.chat.tools())


def test_kill_plugin_always_needs_pin_and_confirmation(svc: Services) -> None:
    spec = svc.registry.get("response_kill_process")
    assert spec.requires_pin and spec.risk is Risk.HIGH


def test_class_validation() -> None:
    class Critical(ResponseCommand):
        name = "critical"
        description = "x"
        risk_level = RiskLevel.CRITICAL
        requires_pin = False

        def can_run(self, event):
            return True

        def run(self, event, ctx):
            return None

    class NoUndo(ResponseCommand):
        name = "no_undo"
        description = "x"
        risk_level = RiskLevel.MEDIUM

        def can_run(self, event):
            return True

        def run(self, event, ctx):
            return None

    class Empty(ResponseCommand):
        name = "Bad Name!"

    assert any("critical" in e for e in Critical.validate_class())
    assert any("undo" in e for e in NoUndo.validate_class())
    errors = Empty.validate_class()
    assert any("name" in e for e in errors) and any("run()" in e for e in errors)


def test_user_plugin_needs_approval_of_exact_version(pinned: Services) -> None:
    path = write_plugin(pinned, "notice.py", GOOD_PLUGIN)
    pinned.responses.reload()
    entry = info(pinned, "notice.py")
    assert entry["status"] == "pending_approval"
    assert pinned.registry.get("response_notice") is None

    assert not pinned.responses.approve("notice.py", entry["sha256"], "000000")
    assert not pinned.responses.approve("notice.py", "0" * 64, PIN)
    assert pinned.responses.approve("notice.py", entry["sha256"], PIN)
    assert info(pinned, "notice.py")["status"] == "loaded"
    assert pinned.registry.get("response_notice") is not None

    # Any change locks the plugin again
    path.write_text(path.read_text(encoding="utf-8") + "\n# changed\n", encoding="utf-8")
    pinned.responses.reload()
    assert info(pinned, "notice.py")["status"] == "pending_approval"
    assert pinned.registry.get("response_notice") is None


def test_unapproved_plugin_code_is_never_executed(pinned: Services, tmp_path: Path) -> None:
    marker = tmp_path / "executed.txt"
    write_plugin(pinned, "boese.py", f"open({str(marker)!r}, 'w').write('x')\n")
    pinned.responses.reload()
    assert not marker.exists()
    assert info(pinned, "boese.py")["status"] == "pending_approval"


def approve_file(svc: Services, file: str) -> None:
    svc.responses.reload()
    assert svc.responses.approve(file, info(svc, file)["sha256"], PIN)


def test_broken_plugins_are_reported_and_core_keeps_running(pinned: Services) -> None:
    write_plugin(pinned, "syntax.py", "def kaputt(:\n")
    write_plugin(pinned, "two.py", GOOD_PLUGIN + GOOD_PLUGIN.replace("Notice", "Second"))
    write_plugin(pinned, "import_error.py", "import gibt_es_nicht\n")
    write_plugin(pinned, "_vorlage.py", "raise SystemExit")
    for file in ("syntax.py", "two.py", "import_error.py"):
        approve_file(pinned, file)
        assert info(pinned, file)["status"] == "error"
    assert all(i["file"] != "_vorlage.py" for i in pinned.responses.list())
    assert info(pinned, "kill_process.py")["status"] == "loaded"


def test_reload_removes_deleted_plugins(pinned: Services) -> None:
    path = write_plugin(pinned, "notice.py", GOOD_PLUGIN)
    approve_file(pinned, "notice.py")
    path.unlink()
    pinned.responses.reload()
    assert pinned.registry.get("response_notice") is None


def test_order_and_disable_via_config(svc: Services) -> None:
    svc.responses.config_file.write_text(
        json.dumps({"order": ["raise_alarm"], "disabled": ["rescue_files"]}), encoding="utf-8"
    )
    svc.responses.reload()
    assert svc.responses.list()[0]["name"] == "raise_alarm"
    assert info(svc, "rescue_files.py")["status"] == "disabled"
    assert svc.registry.get("response_rescue_files") is None


# ---------------------------------------------------------------------- execution


def test_plugin_errors_and_timeouts_never_crash_the_core(pinned: Services) -> None:
    write_plugin(
        pinned,
        "crash.py",
        GOOD_PLUGIN.replace(
            'return CommandResult(ok=True, message="Notice shown")', 'raise RuntimeError("boom")'
        ).replace('"notice"', '"crash"'),
    )
    write_plugin(
        pinned,
        "slow.py",
        GOOD_PLUGIN.replace(
            "    def run(self, event, ctx):",
            "    timeout_s = 0.3\n\n    def run(self, event, ctx):\n        import time; time.sleep(2)",
        ).replace('"notice"', '"slow"'),
    )
    approve_file(pinned, "crash.py")
    approve_file(pinned, "slow.py")
    alert_id = make_alert(pinned, {})
    crash = pinned.engine.request(
        "response_crash", {"alert_id": alert_id}, source=Source.USER, actor="user"
    )
    slow = pinned.engine.request(
        "response_slow", {"alert_id": alert_id}, source=Source.USER, actor="user"
    )
    assert crash.status == "failed" and "boom" in crash.message
    assert slow.status == "failed" and "Time limit" in slow.message
    assert pinned.audit.verify()


def test_raise_alarm_runs_and_is_audited(svc: Services) -> None:
    events: list[tuple[str, dict]] = []
    svc.bus.publish = lambda kind, data=None: events.append((kind, data or {}))  # type: ignore
    alert_id = make_alert(svc, {}, Severity.WARN)
    outcome = svc.engine.request(
        "response_raise_alarm", {"alert_id": alert_id}, source=Source.USER, actor="user"
    )
    assert outcome.status == "executed"
    assert any(k == "notification" for k, _ in events)
    assert svc.audit.list(1)[0]["action"] == "response_raise_alarm"


def test_plugin_only_gets_alert_id_and_validates_match(svc: Services) -> None:
    alert_id = make_alert(svc, {}, Severity.INFO)
    wrong = svc.engine.request(
        "response_raise_alarm", {"alert_id": alert_id}, source=Source.USER, actor="user"
    )
    assert wrong.status == "denied"
    extra = svc.engine.request(
        "response_raise_alarm",
        {"alert_id": alert_id, "path": "C:/x"},
        source=Source.USER,
        actor="user",
    )
    assert extra.status == "denied"
    missing = svc.engine.request(
        "response_raise_alarm", {"alert_id": 99999}, source=Source.USER, actor="user"
    )
    assert missing.status == "denied"


def test_quarantine_plugin_respects_managed_folders(
    svc: Services, managed: Path, tmp_path: Path
) -> None:
    inside = managed / "invoice.pdf.exe"
    inside.write_text("x")
    outside = tmp_path / "anderswo.exe"
    outside.write_text("x")
    ok = svc.engine.request(
        "response_quarantine_file",
        {"alert_id": make_alert(svc, {"path": str(inside)})},
        source=Source.USER,
        actor="user",
    )
    assert ok.status == "pending"  # confirmation needed
    assert svc.engine.approve(ok.approval_id).status == "executed"
    assert not inside.exists()
    denied = svc.engine.request(
        "response_quarantine_file",
        {"alert_id": make_alert(svc, {"path": str(outside)})},
        source=Source.USER,
        actor="user",
    )
    approval = svc.engine.approve(denied.approval_id)
    assert approval.status == "denied" and outside.exists()


@pytest.fixture
def victim():
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    time.sleep(0.3)
    yield proc
    if proc.poll() is None:
        proc.kill()
        proc.wait()


def test_kill_needs_correct_pin_even_in_guardian_mode(pinned: Services, victim) -> None:
    pinned.engine.set_autonomy(3, actor="user")
    alert_id = make_alert(pinned, {"pid": victim.pid})
    outcome = pinned.engine.request(
        "response_kill_process",
        {"alert_id": alert_id},
        source=Source.RULE,
        actor="rule:test",
        severity=Severity.CRITICAL,
    )
    assert outcome.status == "pending"
    assert pinned.engine.approve(outcome.approval_id).status == "denied"
    assert pinned.engine.approve(outcome.approval_id, pin="111111").status == "denied"
    assert victim.poll() is None
    assert pinned.engine.approve(outcome.approval_id, pin=PIN).status == "executed"
    victim.wait(timeout=5)


def test_kill_plugin_refuses_own_process(pinned: Services) -> None:
    alert_id = make_alert(pinned, {"pid": os.getpid()})
    outcome = pinned.engine.request(
        "response_kill_process", {"alert_id": alert_id}, source=Source.USER, actor="user"
    )
    assert pinned.engine.approve(outcome.approval_id, pin=PIN).status == "denied"


def test_emergency_stop_blocks_plugins(svc: Services) -> None:
    alert_id = make_alert(svc, {})
    svc.engine.halt(actor="user")
    outcome = svc.engine.request(
        "response_raise_alarm", {"alert_id": alert_id}, source=Source.USER, actor="user"
    )
    assert outcome.status == "denied"


def test_rescue_files_copies_without_touching_originals(svc: Services, tmp_path: Path) -> None:
    source = tmp_path / "Documents"
    source.mkdir()
    (source / "a.txt").write_text("important")
    svc.config.rescue_folders = [source]
    outcome = svc.engine.request(
        "response_rescue_files", {"alert_id": make_alert(svc, {})}, source=Source.USER, actor="user"
    )
    assert svc.engine.approve(outcome.approval_id).status == "executed"
    copies = list((svc.config.data_dir / "rescue").rglob("a.txt"))
    assert len(copies) == 1 and (source / "a.txt").read_text() == "important"


# ---------------------------------------------------------------------- PIN


def test_pin_rules(svc: Services) -> None:
    for bad in ("123", "abcdef", "1234567890123"):
        with pytest.raises(PinError):
            svc.pin.set_pin(bad)
    svc.pin.set_pin(PIN)
    stored = svc.store.get("owner_pin") or ""
    assert PIN not in stored
    with pytest.raises(PinError):
        svc.pin.set_pin("654321")  # alte PIN fehlt
    svc.pin.set_pin("654321", PIN)
    assert svc.pin.verify("654321")
    assert PIN not in json.dumps(svc.audit.list(50))


def test_pin_lockout_after_five_failures(svc: Services) -> None:
    svc.pin.set_pin(PIN)
    for _ in range(5):
        assert not svc.pin.verify("000000")
    assert not svc.pin.verify(PIN)  # locked, even with the correct PIN
    assert svc.pin.status()["locked_until"] is not None


# ---------------------------------------------------------------------- API


def test_api_flow(pinned: Services) -> None:
    client = TestClient(
        create_app(pinned, token="t", start_background=False), base_url="http://127.0.0.1:8765"
    )
    headers = {"Authorization": "Bearer t"}
    alert_id = make_alert(pinned, {}, Severity.WARN)
    matching = client.get(f"/security/alerts/{alert_id}/responses", headers=headers).json()
    assert [m["name"] for m in matching] == ["raise_alarm"]

    path = write_plugin(pinned, "notice.py", GOOD_PLUGIN)
    plugins = client.post("/response/plugins/reload", headers=headers).json()
    entry = next(p for p in plugins if p["file"] == "notice.py")
    body = {"file": "notice.py", "sha256": entry["sha256"], "pin": "000000"}
    assert client.post("/response/plugins/approve", json=body, headers=headers).status_code == 403
    body["pin"] = PIN
    assert client.post("/response/plugins/approve", json=body, headers=headers).status_code == 200
    bad = {"file": "../evil.py", "sha256": "0" * 64, "pin": PIN}
    assert client.post("/response/plugins/approve", json=bad, headers=headers).status_code == 422
    assert hashlib.sha256(path.read_bytes()).hexdigest() == entry["sha256"]
