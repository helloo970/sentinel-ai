"""Protective measures: end a process (system protection, guardian level), rule responses."""

from __future__ import annotations

import os
import subprocess
import sys
import time

import psutil
import pytest

from sentinel.monitor.processes import protection_reason
from sentinel.monitor.rules import Finding
from sentinel.permissions import Severity, Source
from sentinel.services import Services


@pytest.fixture
def victim():
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    time.sleep(0.3)
    yield proc
    if proc.poll() is None:
        proc.kill()
        proc.wait()


def test_system_and_own_processes_are_protected() -> None:
    assert protection_reason(4)
    assert protection_reason(os.getpid())
    assert protection_reason(os.getppid())


@pytest.mark.parametrize("pid_source", ["self", "parent"])
def test_kill_own_process_is_denied(svc: Services, pid_source: str) -> None:
    pid = os.getpid() if pid_source == "self" else os.getppid()
    outcome = svc.engine.request("kill_process", {"pid": pid}, source=Source.USER, actor="user")
    assert outcome.status == "denied"


def kill_finding(pid: int) -> Finding:
    return Finding(
        rule="test_rule",
        severity=Severity.CRITICAL,
        title="Test",
        detail={"pid": pid},
        fingerprint=f"fp-{pid}",
        response=("kill_process", {"pid": pid, "reason": "test"}),
    )


def test_guardian_level_kills_on_critical_rule(svc: Services, victim) -> None:
    svc.engine.set_autonomy(3, actor="user")
    svc.alerts.handle(kill_finding(victim.pid))
    victim.wait(timeout=5)
    assert victim.poll() is not None
    alert = svc.alerts.list()[0]
    assert alert["response"]["status"] == "executed"


def test_autopilot_only_asks_for_kill(svc: Services, victim) -> None:
    svc.engine.set_autonomy(2, actor="user")
    svc.alerts.handle(kill_finding(victim.pid))
    assert psutil.pid_exists(victim.pid) and victim.poll() is None
    approvals = svc.engine.list_approvals()
    assert approvals and approvals[0]["action"] == "kill_process"
    # After confirmation it is ended
    assert svc.engine.approve(approvals[0]["id"]).status == "executed"
    victim.wait(timeout=5)


def test_observer_rule_only_reports(svc: Services, victim) -> None:
    svc.engine.set_autonomy(0, actor="user")
    svc.alerts.handle(kill_finding(victim.pid))
    assert victim.poll() is None
    assert svc.engine.list_approvals() == []
    assert svc.alerts.list()[0]["response"]["status"] == "denied"


def test_duplicate_findings_are_ignored(svc: Services) -> None:
    finding = Finding(rule="r", severity=Severity.WARN, title="t", detail={}, fingerprint="same")
    assert svc.alerts.handle(finding) is not None
    assert svc.alerts.handle(finding) is None
    assert svc.alerts.counts()["warn"] == 1
