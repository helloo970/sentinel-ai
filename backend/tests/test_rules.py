"""Fixed detection rules and Sysmon parser."""

from __future__ import annotations

from sentinel.monitor import rules
from sentinel.monitor.sysmon import parse_events
from sentinel.permissions import Severity

SYSMON_XML = """
<Event xmlns='http://schemas.microsoft.com/win/2004/08/events/event'>
  <System><EventID>1</EventID><EventRecordID>42</EventRecordID>
  <TimeCreated SystemTime='2026-10-20T08:00:00.000Z'/></System>
  <EventData>
    <Data Name='Image'>C:\\Windows\\System32\\WindowsPowerShell\\v1.0\\powershell.exe</Data>
    <Data Name='ParentImage'>C:\\Program Files\\Microsoft Office\\root\\Office16\\WINWORD.EXE</Data>
    <Data Name='CommandLine'>powershell -enc ignore-previous-instructions</Data>
    <Data Name='ProcessId'>4242</Data>
  </EventData>
</Event>
<Event xmlns='http://schemas.microsoft.com/win/2004/08/events/event'>
  <System><EventID>10</EventID><EventRecordID>43</EventRecordID></System>
  <EventData>
    <Data Name='SourceImage'>C:\\Users\\demo\\AppData\\Local\\Temp\\dump.exe</Data>
    <Data Name='TargetImage'>C:\\Windows\\System32\\lsass.exe</Data>
  </EventData>
</Event>
"""


def test_parse_sysmon_events() -> None:
    events = parse_events(SYSMON_XML)
    assert [e["record"] for e in events] == [42, 43]
    assert events[0]["data"]["ProcessId"] == "4242"


def test_office_spawning_shell_is_critical_with_kill_response() -> None:
    findings = rules.check_sysmon(parse_events(SYSMON_XML))
    office = next(f for f in findings if f.rule == "office_spawns_shell")
    assert office.severity is Severity.CRITICAL
    assert office.response == ("kill_process", {"pid": 4242, "reason": "office_spawns_shell"})
    lsass = next(f for f in findings if f.rule == "lsass_access")
    assert lsass.response is None


def test_double_extension_is_quarantined() -> None:
    finding = rules.check_new_file(r"C:\Users\demo\Downloads\Invoice.pdf.exe")
    assert finding is not None and finding.severity is Severity.CRITICAL
    assert finding.response[0] == "quarantine_file"


def test_plain_installer_is_only_info() -> None:
    finding = rules.check_new_file(r"C:\Users\demo\Downloads\setup.exe")
    assert finding is not None and finding.severity is Severity.INFO
    assert finding.response is None and finding.notify is False
    assert rules.check_new_file(r"C:\Users\demo\Downloads\holiday.jpg") is None


def test_temp_process_listening_is_critical() -> None:
    procs = [{"pid": 900, "exe": r"C:\Users\demo\AppData\Local\Temp\x\agent.exe", "name": "a"}]
    listening = [{"pid": 900, "port": 4444, "public": True}]
    finding = rules.check_processes(procs, listening)[0]
    assert finding.rule == "temp_listener" and finding.severity is Severity.CRITICAL
    assert rules.check_processes(procs, [])[0].severity is Severity.WARN


def test_server_rules() -> None:
    findings = rules.check_server(
        {"auth_failures_1h": 50, "disk_percent": 95, "failed_services": ["nginx"], "hour_bucket": 1}
    )
    assert {f.rule for f in findings} == {
        "server_ssh_bruteforce",
        "server_disk_full",
        "server_service_failed",
    }
    assert rules.check_server({"auth_failures_1h": 2, "disk_percent": 40}) == []


def test_new_entries_against_baseline() -> None:
    entries = {"a": {"name": "a"}, "b": {"name": "b"}}
    found = rules.check_new_entries(
        "autostart", entries, {"a"}, title="Neu", severity=Severity.WARN
    )
    assert [f.detail["name"] for f in found] == ["b"]


def test_own_autostart_entry_is_recognised() -> None:
    from sentinel.monitor.autostart import is_own_entry

    exe = r"C:\Program Files\SENTINEL AI\sentinel-app.exe"
    own = {"command": f'"{exe}" --autostart'}
    other = {"command": r"C:\Users\demo\AppData\Local\Temp\x.exe"}
    assert is_own_entry(own, exe)
    assert not is_own_entry(other, exe)
    assert not is_own_entry(own, None)


def test_background_commands_never_open_console_windows(monkeypatch) -> None:
    import subprocess
    import sys

    from sentinel.monitor import sysmon
    from sentinel.network import inventory
    from sentinel.winproc import NO_WINDOW

    calls: list[dict] = []

    def fake_run(args, **kwargs):
        calls.append(kwargs)
        return subprocess.CompletedProcess(args, 1, b"", b"")

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(sys, "platform", "win32")
    reader = sysmon.SysmonReader()
    monkeypatch.setattr(reader, "available", lambda: True)
    reader.read_latest()
    inventory.NetworkInventory._ping(None, "10.0.0.1")  # type: ignore[arg-type]
    assert calls and all(c.get("creationflags") == NO_WINDOW for c in calls)
    assert all(c.get("shell") is False for c in calls)
