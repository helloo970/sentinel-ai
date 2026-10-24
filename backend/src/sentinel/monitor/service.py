"""Periodic monitoring: processes, ports, autostart, Sysmon, folders, server."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import threading
import time
from collections import deque
from dataclasses import asdict
from pathlib import Path
from typing import TYPE_CHECKING, Any

from sentinel.monitor import autostart, processes, rules
from sentinel.monitor.sysmon import SysmonReader
from sentinel.pcsec import devices as pcdevices
from sentinel.permissions.models import Severity

if TYPE_CHECKING:
    from sentinel.services import Services

log = logging.getLogger(__name__)

BURST_WINDOW_S = 10
BURST_THRESHOLD = 100
ARP_INTERVAL_S = 300
SCAN_DELAY_S = 5.0


class Baselines:
    """Known entries (autostart, ports). The first run silently fills the baseline."""

    def __init__(self, svc: Services) -> None:
        self.db = svc.db

    def known(self, kind: str) -> set[str]:
        return {
            r["key"] for r in self.db.query("SELECT key FROM baselines WHERE kind = ?", (kind,))
        }

    def add(self, kind: str, keys: list[str]) -> None:
        now = time.time()
        self.db.executemany(
            "INSERT OR IGNORE INTO baselines(kind, key, first_seen) VALUES(?,?,?)",
            [(kind, k, now) for k in keys],
        )


class FolderWatcher:
    """Watches folders with watchdog: risky new files and mass changes."""

    def __init__(self, svc: Services, folders: list[Path]) -> None:
        self.svc = svc
        self.folders = [f for f in folders if f.is_dir()]
        self._observer: Any = None
        self._events: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def start(self) -> None:
        if not self.folders:
            return
        from watchdog.events import FileSystemEvent, FileSystemEventHandler
        from watchdog.observers import Observer

        watcher = self

        class Handler(FileSystemEventHandler):
            def __init__(self, folder: Path) -> None:
                self.folder = folder

            def on_any_event(self, event: FileSystemEvent) -> None:
                if event.is_directory:
                    return
                watcher.on_event(
                    str(self.folder),
                    event.event_type,
                    str(event.src_path),
                    str(getattr(event, "dest_path", "") or ""),
                )

        self._observer = Observer()
        for folder in self.folders:
            self._observer.schedule(Handler(folder), str(folder), recursive=False)
        self._observer.daemon = True
        self._observer.start()

    def stop(self) -> None:
        if self._observer is not None:
            self._observer.stop()
            self._observer.join(timeout=5)

    def on_event(self, folder: str, event_type: str, src: str, dest: str) -> None:
        now = time.time()
        with self._lock:
            window = self._events.setdefault(folder, deque())
            window.append(now)
            while window and window[0] < now - BURST_WINDOW_S:
                window.popleft()
            count = len(window)
        if count >= BURST_THRESHOLD:
            bucket = int(now // 300)
            self.svc.alerts.handle(rules.check_burst(folder, count, BURST_WINDOW_S, bucket))
        path = None
        if event_type == "created":
            path = src
        elif event_type == "moved" and rules.is_download_finish(src):
            path = dest
        if path and not path.startswith(str(self.svc.config.quarantine_dir)):
            finding = rules.check_new_file(path)
            if finding:
                self.svc.alerts.handle(finding)
            if self.svc.filescan is not None and self.svc.filescan.should_scan(Path(path)):
                self.scan_later(Path(path))

    def scan_later(self, path: Path, delay: float = SCAN_DELAY_S) -> None:
        """Scans new files once they are fully written (size stable)."""

        def run() -> None:
            try:
                size = path.stat().st_size
                time.sleep(1.0)
                if path.stat().st_size != size:
                    self.scan_later(path, delay)
                    return
                result = self.svc.filescan.scan(path)
            except OSError:
                return
            finding = rules.check_file_scan(result)
            if finding:
                self.svc.alerts.handle(finding)

        timer = threading.Timer(delay, run)
        timer.daemon = True
        timer.start()


class MonitorService:
    def __init__(self, svc: Services) -> None:
        self.svc = svc
        self.baselines = Baselines(svc)
        self.sysmon = SysmonReader()
        self.folders = FolderWatcher(svc, svc.config.watched_folders)
        self._task: asyncio.Task[None] | None = None
        self.last_scan: float | None = None
        self.last_error: str | None = None
        self._last_arp = 0.0

    def status(self) -> dict[str, Any]:
        return {
            "last_scan": self.last_scan,
            "last_error": self.last_error,
            "sysmon": self.sysmon.status,
            "watched_folders": [str(f) for f in self.folders.folders],
            "interval_s": self.svc.config.monitor_interval_s,
        }

    async def _background_checks(self) -> None:
        """Passive: read the ARP table (no scan), check certificates, load the hash list."""
        now = time.time()
        if now - self._last_arp >= ARP_INTERVAL_S:
            self._last_arp = now
            try:
                await asyncio.to_thread(self.svc.network.refresh, resolve_names=False)
            except Exception as exc:
                log.warning("arp refresh failed: %s", exc)
        if self.svc.certs.due():
            try:
                await asyncio.to_thread(self.svc.certs.check)
            except Exception as exc:
                log.warning("cert check failed: %s", exc)
        if self.svc.filescan.feed_due():
            try:
                await asyncio.to_thread(self.svc.filescan.update_feed)
            except Exception as exc:
                log.warning("malware feed failed: %s", exc)

    def _known_or_baseline(self, kind: str, keys: list[str]) -> set[str] | None:
        """Known entries - None on the first run (then silently remember them as the baseline)."""
        known = self.baselines.known(kind)
        self.baselines.add(kind, keys)
        return known or None

    def pc_checks(self) -> list[rules.Finding]:
        """USB devices, browser extensions, camera and microphone programs."""
        findings: list[rules.Finding] = []
        usb = pcdevices.usb_devices()
        known = self._known_or_baseline("usb", [d["id"] for d in usb])
        if known is not None:
            findings += rules.check_usb(usb, known)

        extensions = pcdevices.browser_extensions()
        keys = [f"{e['browser']}|{e['profile']}|{e['id']}" for e in extensions]
        known = self._known_or_baseline("extension", keys)
        if known is not None:
            findings += rules.check_extensions(extensions, known)

        for capability in ("webcam", "microphone"):
            # Jarvis' own camera (hologram, gestures) is not an alert
            own = os.path.normcase(self.svc.config.app_exe or "")
            apps = [
                a
                for a in pcdevices.capability_usage(capability)
                if not own or os.path.normcase(a["app"]) != own
            ]
            known = self._known_or_baseline(f"{capability}_app", [a["app"] for a in apps])
            if known is not None:
                findings += rules.check_capability_apps(capability, apps, known)
        return findings

    def _diff(
        self, kind: str, entries: dict[str, dict[str, Any]], title: str, severity: Severity
    ) -> list[rules.Finding]:
        known = self.baselines.known(kind)
        if not known:
            self.baselines.add(kind, list(entries))
            return []
        findings = rules.check_new_entries(kind, entries, known, title=title, severity=severity)
        self.baselines.add(kind, list(entries))
        return findings

    def scan_once(self) -> list[rules.Finding]:
        """One pass of all checks (blocking, runs in a thread)."""
        findings: list[rules.Finding] = []
        procs = [asdict(p) for p in processes.list_processes()]
        listening = processes.list_listening_ports()
        findings += rules.check_processes(procs, listening)

        auto = {
            autostart.entry_key(e): e
            for e in autostart.list_autostart()
            if not autostart.is_own_entry(e, self.svc.config.app_exe)
        }
        findings += self._diff("autostart", auto, "New autostart entry", Severity.WARN)

        public = {f"{p['process']}|{p['port']}": p for p in listening if p["public"]}
        findings += self._diff("listener", public, "New open port", Severity.INFO)

        findings += rules.check_sysmon(self.sysmon.read_new())
        findings += self.pc_checks()

        for finding in findings:
            self.svc.alerts.handle(finding)
        self.last_scan = time.time()
        return findings

    async def _loop(self) -> None:
        while True:
            if not self.svc.engine.halted:
                try:
                    await asyncio.to_thread(self.scan_once)
                    self.last_error = None
                except Exception as exc:
                    self.last_error = str(exc)
                    log.exception("monitor scan failed")
                try:
                    await self.svc.server.poll_and_check(self.svc)
                except Exception as exc:
                    log.warning("server check failed: %s", exc)
                await self._background_checks()
            await asyncio.sleep(self.svc.config.monitor_interval_s)

    def start(self) -> None:
        self.folders.start()
        self._task = asyncio.get_running_loop().create_task(self._loop())

    async def stop(self) -> None:
        self.folders.stop()
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
