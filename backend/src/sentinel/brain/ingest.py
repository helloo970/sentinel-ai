"""Reading folders: short text excerpts go into the learning queue.

File contents are untrusted data. They are only read (never executed), filtered for
secrets and off-limits content and later go to the AI as marked data.
"""

from __future__ import annotations

import logging
import os
import re
import time
from pathlib import Path
from typing import TYPE_CHECKING

from sentinel.brain.privacy import scrub
from sentinel.secrets_filter import contains_secret, redact

if TYPE_CHECKING:
    from sentinel.db import Database

log = logging.getLogger(__name__)

TEXT_SUFFIXES = {".md", ".txt"}
MAX_DEPTH = 3
MAX_FILES = 400
MAX_BYTES = 200_000
EXCERPT_CHARS = 3000
SKIP_DIRS = {
    "node_modules",
    ".git",
    ".venv",
    "venv",
    "__pycache__",
    "target",
    "dist",
    "build",
    "AppData",
    "$RECYCLE.BIN",
    ".cache",
}
# File names that sound like credentials or report cards are never opened (English + German)
_SKIP_NAMES = re.compile(
    r"(^\.env)|passw|kennwort|secret|token|credential|zugang|schlüssel|schluessel|"
    r"private|id_rsa|\.pem$|\.key$|zeugnis|noten|bewerbung|lebenslauf|ausweis|steuer|"
    r"konto|bank|rechnung|vertrag|grades|transcript|report.?card|resume|cv\b|passport|"
    r"tax.?return|invoice|contract|login",
    re.IGNORECASE,
)


def iter_files(folders: list[Path]) -> list[Path]:
    found: list[Path] = []
    for root in folders:
        if not root.is_dir():
            continue
        root_depth = len(root.parts)
        for current, dirs, files in os.walk(root):
            here = Path(current)
            depth = len(here.parts) - root_depth
            dirs[:] = [
                d
                for d in dirs
                if d not in SKIP_DIRS and not d.startswith(".") and depth < MAX_DEPTH
            ]
            for name in files:
                path = here / name
                if path.suffix.lower() not in TEXT_SUFFIXES or _SKIP_NAMES.search(name):
                    continue
                if path.is_symlink():
                    continue
                found.append(path)
                if len(found) >= MAX_FILES:
                    return found
    return found


def excerpt(path: Path) -> str | None:
    try:
        if path.stat().st_size > MAX_BYTES:
            return None
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None
    text = re.sub(r"\s+", " ", text).strip()[:EXCERPT_CHARS]
    if len(text) < 40:
        return None
    if contains_secret(text):
        text = redact(text)
    return scrub(text)


def scan(db: Database, folders: list[Path]) -> int:
    """Queues new or changed files. Returns the count."""
    queued = 0
    now = time.time()
    for path in iter_files(folders):
        try:
            mtime = path.stat().st_mtime
        except OSError:
            continue
        key = str(path)
        known = db.query_one("SELECT mtime FROM brain_files WHERE path = ?", (key,))
        if known and known["mtime"] >= mtime:
            continue
        db.execute("INSERT OR REPLACE INTO brain_files(path, mtime) VALUES(?,?)", (key, mtime))
        text = excerpt(path)
        if not text:
            continue
        db.execute(
            "INSERT INTO brain_queue(kind, ref, text, created, done) VALUES('file',?,?,?,0) "
            "ON CONFLICT(ref) DO UPDATE SET text = excluded.text, done = 0",
            (f"file:{key}", f"File {path.name}:\n{text}", now),
        )
        queued += 1
    if queued:
        log.info("brain: queued %d files for learning", queued)
    return queued
