"""PC security: file scan, USB, browser extensions, camera/microphone, monitor loop."""

from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path

import pytest

from sentinel.monitor import rules
from sentinel.pcsec import devices as pcdevices
from sentinel.pcsec import filescan
from sentinel.permissions import Severity, Source
from sentinel.services import Services

FEED = """################
# MalwareBazaar recent malware samples
################
{bad}
not-a-hash
ABCDEF
"""


@pytest.fixture
def no_defender(monkeypatch) -> None:
    monkeypatch.setattr(filescan, "defender_scan", lambda path, timeout=180: "clean")


def add_bad_hash(svc: Services, data: bytes) -> str:
    digest = hashlib.sha256(data).hexdigest()
    svc.db.execute("INSERT INTO malware_hashes(sha256, added) VALUES(?, 0)", (digest,))
    return digest


# ---------------------------------------------------------------------- File scan


def test_feed_parsing_only_accepts_sha256() -> None:
    bad = "a" * 64
    assert filescan.parse_feed(FEED.format(bad=bad)) == {bad}


def test_scan_detects_known_malware_and_records(svc: Services, managed: Path, no_defender) -> None:
    sample = managed / "invoice.exe"
    sample.write_bytes(b"pretend-malware")
    digest = add_bad_hash(svc, b"pretend-malware")
    result = svc.filescan.scan(sample)
    assert (
        result["sha256"] == digest and result["known_malware"] and result["verdict"] == "malicious"
    )
    finding = rules.check_file_scan(result)
    assert finding.severity is Severity.CRITICAL
    assert finding.response == ("quarantine_file", {"path": str(sample)})
    assert svc.filescan.recent()[0]["verdict"] == "malicious"


def test_clean_file_and_defender_threat(svc: Services, managed: Path, monkeypatch) -> None:
    sample = managed / "tool.exe"
    sample.write_bytes(b"harmless")
    monkeypatch.setattr(filescan, "defender_scan", lambda path, timeout=180: "clean")
    assert svc.filescan.scan(sample)["verdict"] == "clean"
    assert rules.check_file_scan(svc.filescan.scan(sample)) is None
    monkeypatch.setattr(filescan, "defender_scan", lambda path, timeout=180: "threat")
    result = svc.filescan.scan(sample)
    assert result["verdict"] == "malicious"
    assert "Windows Defender" in rules.check_file_scan(result).detail["reasons"][0]


def test_scan_file_action_only_in_allowed_folders(
    svc: Services, managed: Path, tmp_path: Path, no_defender
) -> None:
    inside = managed / "a.pdf"
    inside.write_bytes(b"pdf")
    outside = tmp_path / "b.pdf"
    outside.write_bytes(b"pdf")
    ok = svc.engine.request(
        "scan_file", {"path": str(inside)}, source=Source.ASSISTANT, actor="jarvis"
    )
    assert ok.status == "executed" and "clean" in ok.message
    denied = svc.engine.request(
        "scan_file", {"path": str(outside)}, source=Source.USER, actor="user"
    )
    assert denied.status == "denied"


def test_malicious_scan_quarantines_at_autopilot(svc: Services, managed: Path, no_defender) -> None:
    sample = managed / "dropper.exe"
    sample.write_bytes(b"really-bad")
    add_bad_hash(svc, b"really-bad")
    outcome = svc.engine.request(
        "scan_file", {"path": str(sample)}, source=Source.USER, actor="user"
    )
    assert outcome.status == "executed"
    alert = svc.alerts.list()[0]
    assert alert["rule"] == "malware_file" and alert["response"]["status"] == "executed"
    assert not sample.exists()  # in quarantine, undoable


def test_new_download_is_scanned_automatically(svc: Services, managed: Path, monkeypatch) -> None:
    scanned: list[Path] = []
    monkeypatch.setattr(
        svc.filescan,
        "scan",
        lambda path, defender=True: scanned.append(path) or {"path": str(path), "verdict": "clean"},
    )
    watcher = svc.monitor.folders
    sample = managed / "setup.msi"
    sample.write_bytes(b"x")
    watcher.scan_later = lambda path, delay=0: svc.filescan.scan(path)  # sofort statt Timer
    watcher.on_event(str(managed), "created", str(sample), "")
    watcher.on_event(str(managed), "created", str(managed / "foto.jpg"), "")
    assert scanned == [sample]


# ---------------------------------------------------------------------- USB


def test_usb_rules() -> None:
    devices = [
        {"id": "VID_1&PID_1\\a", "name": "USB stick", "keyboard": False},
        {"id": "VID_2&PID_2\\b", "name": "HID keyboard", "keyboard": True},
    ]
    found = rules.check_usb(devices, {"VID_1&PID_1\\a"})
    assert len(found) == 1 and found[0].severity is Severity.WARN and found[0].notify
    assert "keyboard" in found[0].title
    other = rules.check_usb([{"id": "x", "name": "Mouse"}], set())
    assert other[0].severity is Severity.INFO and other[0].notify is False


def test_registry_description_cleanup() -> None:
    assert pcdevices._desc("@input.inf,%hid%;HID keyboard") == "HID keyboard"
    assert pcdevices._desc("") == ""


# ---------------------------------------------------------------------- Browser


def make_extension(root: Path, ext_id: str, manifest: dict, messages: dict | None = None) -> None:
    version = root / "Default" / "Extensions" / ext_id / "1.0.0_0"
    version.mkdir(parents=True)
    (version / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    if messages:
        loc = version / "_locales" / "en"
        loc.mkdir(parents=True)
        (loc / "messages.json").write_text(json.dumps(messages), encoding="utf-8")


def test_chromium_extensions(tmp_path: Path) -> None:
    make_extension(
        tmp_path,
        "abc",
        {
            "name": "__MSG_appName__",
            "version": "1.2",
            "permissions": ["storage", "tabs"],
            "host_permissions": ["<all_urls>"],
        },
        {"appName": {"message": "Ad blocker"}},
    )
    make_extension(tmp_path, "def", {"name": "Notes", "version": "3", "permissions": ["storage"]})
    found = {e["id"]: e for e in pcdevices.chromium_extensions({"Chrome": str(tmp_path)})}
    assert found["abc"]["name"] == "Ad blocker"
    assert found["abc"]["broad"] == ["<all_urls>", "tabs"]
    assert found["def"]["broad"] == []


def test_firefox_extensions(tmp_path: Path) -> None:
    profile = tmp_path / "abc.default"
    profile.mkdir()
    (profile / "extensions.json").write_text(
        json.dumps(
            {
                "addons": [
                    {
                        "id": "x@y",
                        "type": "extension",
                        "location": "app-profile",
                        "version": "2",
                        "defaultLocale": {"name": "Tabs+"},
                        "userPermissions": {"permissions": ["tabs"], "origins": ["<all_urls>"]},
                    },
                    {"id": "sys@mozilla", "type": "extension", "location": "app-system-defaults"},
                ]
            }
        ),
        encoding="utf-8",
    )
    found = pcdevices.firefox_extensions(str(tmp_path / "*" / "extensions.json"))
    assert [e["name"] for e in found] == ["Tabs+"]
    assert found[0]["broad"] == ["<all_urls>", "tabs"]


def test_extension_rules() -> None:
    ext = {
        "browser": "Chrome",
        "profile": "Default",
        "id": "abc",
        "name": "X",
        "broad": ["<all_urls>"],
    }
    assert rules.check_extensions([ext], {"Chrome|Default|abc"}) == []
    found = rules.check_extensions([ext], set())
    assert found[0].severity is Severity.WARN and "all sites" in found[0].title


# ---------------------------------------------------------------------- Camera / microphone


def test_filetime_conversion_and_camera_rule() -> None:
    assert pcdevices._filetime(0) is None
    # January 1, 2026 as FILETIME (100 ns steps since 1601)
    ts = pcdevices._filetime(134116992000000000)
    assert ts is not None and 1767225600 - 86400 < ts < 1767225600 + 86400
    apps = [{"app": "C:\\Program Files\\Zoom\\zoom.exe", "in_use": True}]
    found = rules.check_capability_apps("webcam", apps, set())
    assert found[0].severity is Severity.WARN and "camera" in found[0].title
    mic = rules.check_capability_apps("microphone", apps, set())
    assert mic[0].severity is Severity.INFO
    assert rules.check_capability_apps("webcam", apps, {apps[0]["app"]}) == []


def test_pc_checks_build_baseline_first(svc: Services, monkeypatch) -> None:
    usb = [{"id": "a", "name": "Stick", "keyboard": False}]
    monkeypatch.setattr(pcdevices, "usb_devices", lambda: usb)
    monkeypatch.setattr(pcdevices, "browser_extensions", lambda: [])
    monkeypatch.setattr(pcdevices, "capability_usage", lambda c: [])
    assert svc.monitor.pc_checks() == []  # first run: only record the baseline
    usb.append({"id": "b", "name": "Keyboard", "keyboard": True})
    found = svc.monitor.pc_checks()
    assert [f.detail["id"] for f in found] == ["b"]


# ---------------------------------------------------------------------- Monitor-Schleife


async def test_monitor_loop_runs_background_checks(svc: Services, monkeypatch) -> None:
    """Regression: the background checks must belong to the monitor class."""
    calls: list[str] = []
    monkeypatch.setattr(svc.monitor, "scan_once", lambda: calls.append("scan") or [])
    monkeypatch.setattr(svc.network, "refresh", lambda resolve_names=True: calls.append("arp"))
    monkeypatch.setattr(svc.certs, "check", lambda: calls.append("certs"))
    monkeypatch.setattr(svc.filescan, "update_feed", lambda: calls.append("feed") or 0)
    svc.config.monitor_interval_s = 5

    async def stop(_seconds: float) -> None:
        raise asyncio.CancelledError

    monkeypatch.setattr(asyncio, "sleep", stop)
    with pytest.raises(asyncio.CancelledError):
        await svc.monitor._loop()
    assert calls == ["scan", "arp", "certs", "feed"]


def test_malware_feed_needs_key_and_parses_csv(svc: Services, monkeypatch) -> None:
    from sentinel.pcsec import filescan

    assert svc.filescan.update_feed() == 0
    assert "abuse.ch key missing" in (svc.filescan.feed_error or "")
    filescan._keyring().set_password(*filescan.ABUSECH_KEYRING, "a" * 40)
    sha = "ab" * 32
    csv = f'# MalwareBazaar\n"2026-10-24 08:00:00","{sha}","md5","sha1"\n'
    seen: list[str] = []
    monkeypatch.setattr(filescan, "_download", lambda url: seen.append(url) or csv)
    assert svc.filescan.update_feed() == 1
    assert svc.filescan.is_known_malware(sha) and svc.filescan.feed_error is None
    assert seen[0].startswith("https://mb-api.abuse.ch/v2/files/exports/")
