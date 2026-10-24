"""File scan: SHA-256 against known malware (MalwareBazaar) and Windows Defender.

No file is ever uploaded. The hash list is stored locally, Defender
scans locally (MpCmdRun without admin rights, fixed arguments, without remediation).
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from sentinel.winproc import NO_WINDOW

if TYPE_CHECKING:
    from sentinel.services import Services

log = logging.getLogger(__name__)

# MalwareBazaar (abuse.ch) has required a free auth key since 2025
FEED_URL = "https://mb-api.abuse.ch/v2/files/exports/{key}/recent.csv"
ABUSECH_KEYRING = ("SentinelAI-AbuseCH", "auth_key")
_SHA_IN_LINE = re.compile(r"[0-9a-f]{64}")
FEED_INTERVAL_S = 6 * 3600
KEEP_DAYS = 30
MAX_SCAN_BYTES = 500 * 1024 * 1024
SHA_RE = re.compile(r"^[0-9a-f]{64}$")
SCAN_EXTENSIONS = {
    ".exe",
    ".dll",
    ".msi",
    ".scr",
    ".bat",
    ".cmd",
    ".ps1",
    ".vbs",
    ".js",
    ".jse",
    ".hta",
    ".lnk",
    ".jar",
    ".zip",
    ".rar",
    ".7z",
    ".iso",
    ".img",
    ".docm",
    ".xlsm",
    ".pptm",
    ".pdf",
}


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_feed(text: str) -> set[str]:
    """SHA-256 values from the text list or the CSV export (comment lines start with #)."""
    hashes: set[str] = set()
    for line in text.splitlines():
        line = line.strip().lower()
        if not line or line.startswith("#"):
            continue
        if SHA_RE.match(line):
            hashes.add(line)
            continue
        found = _SHA_IN_LINE.search(line)
        if found:
            hashes.add(found.group(0))
    return hashes


def _keyring() -> Any:
    import keyring

    return keyring


def abusech_key() -> str | None:
    try:
        return _keyring().get_password(*ABUSECH_KEYRING)
    except Exception:
        return None


def _download(url: str) -> str:
    """Without httpx logging: the key is part of the URL and must never end up in the log."""
    import urllib.request

    if not url.startswith("https://mb-api.abuse.ch/"):
        raise ValueError("unexpected feed url")
    request = urllib.request.Request(url, headers={"User-Agent": "SENTINEL-AI/0.1"})  # noqa: S310
    with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310 - fixed https address
        return response.read(20_000_000).decode("utf-8", errors="ignore")


def defender_path() -> Path:
    base = os.environ.get("ProgramFiles", r"C:\Program Files")
    return Path(base) / "Windows Defender" / "MpCmdRun.exe"


def defender_scan(path: Path, timeout: int = 180) -> str:
    """'clean', 'threat' or 'unavailable'. Only scans, removes nothing (-DisableRemediation)."""
    exe = defender_path()
    if sys.platform != "win32" or not exe.is_file():
        return "unavailable"
    try:
        proc = subprocess.run(  # noqa: S603 - fixed path, checked file, no shell
            [str(exe), "-Scan", "-ScanType", "3", "-File", str(path), "-DisableRemediation"],
            capture_output=True,
            timeout=timeout,
            check=False,
            shell=False,
            creationflags=NO_WINDOW,
        )
    except (OSError, subprocess.TimeoutExpired):
        return "unavailable"
    if proc.returncode == 0:
        return "clean"
    if proc.returncode == 2:
        return "threat"
    return "unavailable"


class FileScanner:
    def __init__(self, svc: Services) -> None:
        self.svc = svc
        self.last_feed = 0.0
        self.feed_error: str | None = None
        self._lock = threading.Lock()  # only one Defender scan at a time

    # ------------------------------------------------------------------ Hash list

    def hash_count(self) -> int:
        row = self.svc.db.query_one("SELECT COUNT(*) AS n FROM malware_hashes")
        return int(row["n"]) if row else 0

    def update_feed(self) -> int:
        import urllib.error

        key = abusech_key()
        if not key:
            self.last_feed = time.time()
            self.feed_error = (
                "Free abuse.ch key missing - in a terminal run: sentinel abusech login"
            )
            return 0
        try:
            text = _download(FEED_URL.format(key=key))
        except (urllib.error.URLError, OSError, ValueError) as exc:
            # Don't retry right away - only again after the normal interval
            self.last_feed = time.time()
            status = getattr(exc, "code", None)
            self.feed_error = (
                "abuse.ch rejects the key - run sentinel abusech login"
                if status in (401, 403)
                else f"List not reachable ({status or type(exc).__name__})"
            )
            log.warning("malware feed unavailable: %s", status or type(exc).__name__)
            return 0
        hashes = parse_feed(text)
        now = time.time()
        self.svc.db.executemany(
            "INSERT OR IGNORE INTO malware_hashes(sha256, added) VALUES(?, ?)",
            [(h, now) for h in hashes],
        )
        self.svc.db.execute(
            "DELETE FROM malware_hashes WHERE added < ?", (now - KEEP_DAYS * 86400,)
        )
        self.last_feed = now
        self.feed_error = None
        return len(hashes)

    def feed_due(self) -> bool:
        return self.svc.config.malware_feed and time.time() - self.last_feed > FEED_INTERVAL_S

    def is_known_malware(self, sha256: str) -> bool:
        return (
            self.svc.db.query_one(
                "SELECT sha256 FROM malware_hashes WHERE sha256 = ?", (sha256.lower(),)
            )
            is not None
        )

    # ------------------------------------------------------------------ Scanning

    @staticmethod
    def should_scan(path: Path) -> bool:
        return path.suffix.lower() in SCAN_EXTENSIONS

    def scan(self, path: Path, *, defender: bool = True) -> dict[str, Any]:
        if not path.is_file():
            return {"path": str(path), "error": "File not found"}
        size = path.stat().st_size
        if size > MAX_SCAN_BYTES:
            return {"path": str(path), "error": "File too large to scan"}
        digest = sha256_of(path)
        result: dict[str, Any] = {
            "path": str(path),
            "sha256": digest,
            "size": size,
            "known_malware": self.is_known_malware(digest),
            "defender": "skipped",
        }
        if defender:
            with self._lock:
                result["defender"] = defender_scan(path)
        result["verdict"] = (
            "malicious" if result["known_malware"] or result["defender"] == "threat" else "clean"
        )
        self.svc.db.execute(
            "INSERT INTO file_scans(ts, path, sha256, verdict, defender) VALUES(?,?,?,?,?)",
            (time.time(), str(path), digest, result["verdict"], result["defender"]),
        )
        return result

    def recent(self, limit: int = 50) -> list[dict[str, Any]]:
        return self.svc.db.query("SELECT * FROM file_scans ORDER BY ts DESC LIMIT ?", (limit,))
