"""File actions in approved folders: tidy up, quarantine, recycle bin.

Every path is resolved and must lie inside an approved folder.
There is no permanent deletion, only the recycle bin.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import time
from pathlib import Path
from typing import Any

from sentinel.permissions.models import ActionDenied

CATEGORIES: dict[str, set[str]] = {
    "Images": {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".svg", ".heic", ".tiff"},
    "Documents": {
        ".pdf",
        ".doc",
        ".docx",
        ".odt",
        ".txt",
        ".rtf",
        ".md",
        ".xls",
        ".xlsx",
        ".ods",
        ".csv",
        ".ppt",
        ".pptx",
        ".odp",
        ".epub",
    },
    "Archives": {".zip", ".rar", ".7z", ".tar", ".gz", ".bz2", ".xz"},
    "Installers": {".exe", ".msi", ".msix", ".appx"},
    "Audio": {".mp3", ".wav", ".flac", ".ogg", ".m4a", ".aac"},
    "Video": {".mp4", ".mkv", ".avi", ".mov", ".webm", ".wmv"},
    "Code": {
        ".py",
        ".js",
        ".ts",
        ".json",
        ".html",
        ".css",
        ".c",
        ".cpp",
        ".h",
        ".java",
        ".rs",
        ".go",
        ".sh",
        ".ps1",
        ".ipynb",
        ".yaml",
        ".yml",
        ".toml",
    },
}
SKIP_SUFFIXES = {".crdownload", ".part", ".tmp", ".partial", ".download", ".ini"}
MAX_FILES = 500
RECENT_S = 60


def resolve_inside(path_str: str, roots: list[Path], *, must_exist: bool = True) -> Path:
    """Resolves the path and checks that it lies inside an approved folder."""
    if not path_str or "\x00" in path_str:
        raise ActionDenied("Invalid path")
    path = Path(path_str)
    if not path.is_absolute():
        raise ActionDenied("Path must be absolute")
    try:
        resolved = path.resolve(strict=must_exist)
    except (OSError, RuntimeError) as exc:
        raise ActionDenied(f"Path not found: {path}") from exc
    for root in roots:
        try:
            root_resolved = root.resolve(strict=True)
        except OSError:
            continue
        if resolved == root_resolved or resolved.is_relative_to(root_resolved):
            return resolved
    raise ActionDenied("Path is outside the approved folders")


def category_for(path: Path) -> str:
    suffix = path.suffix.lower()
    for name, suffixes in CATEGORIES.items():
        if suffix in suffixes:
            return name
    return "Other"


def _free_target(target: Path) -> Path:
    if not target.exists():
        return target
    for index in range(1, 1000):
        candidate = target.with_name(f"{target.stem} ({index}){target.suffix}")
        if not candidate.exists():
            return candidate
    raise ActionDenied("No free file name found")


def plan_organize(folder: Path, now: float | None = None) -> list[tuple[Path, Path]]:
    now = now or time.time()
    moves: list[tuple[Path, Path]] = []
    reserved: set[Path] = set()
    for item in sorted(folder.iterdir()):
        if not item.is_file() or item.is_symlink():
            continue
        if item.suffix.lower() in SKIP_SUFFIXES or item.name.startswith("."):
            continue
        if now - item.stat().st_mtime < RECENT_S:
            continue  # may still be downloading
        target = folder / category_for(item) / item.name
        candidate = target
        index = 1
        while candidate.exists() or candidate in reserved:
            candidate = target.with_name(f"{target.stem} ({index}){target.suffix}")
            index += 1
        reserved.add(candidate)
        moves.append((item, candidate))
        if len(moves) >= MAX_FILES:
            break
    return moves


def apply_moves(moves: list[tuple[Path, Path]]) -> list[dict[str, str]]:
    done = []
    for src, dst in moves:
        dst.parent.mkdir(exist_ok=True)
        if dst.exists():
            continue
        shutil.move(str(src), str(dst))
        done.append({"from": str(src), "to": str(dst)})
    return done


def revert_moves(moves: list[dict[str, str]]) -> int:
    reverted = 0
    for move in reversed(moves):
        src, dst = Path(move["from"]), Path(move["to"])
        if dst.exists() and not src.exists():
            shutil.move(str(dst), str(src))
            reverted += 1
            parent = dst.parent
            if parent.is_dir() and not any(parent.iterdir()):
                parent.rmdir()
    return reverted


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def quarantine(path: Path, quarantine_dir: Path) -> dict[str, Any]:
    """Moves the file into quarantine (renamed, not executable)."""
    if not path.is_file():
        raise ActionDenied("Only files can be quarantined")
    quarantine_dir.mkdir(parents=True, exist_ok=True)
    digest = sha256_file(path)
    stored = _free_target(quarantine_dir / f"{int(time.time())}_{digest[:12]}.quarantined")
    shutil.move(str(path), str(stored))
    meta = {"original_path": str(path), "stored_path": str(stored), "sha256": digest}
    stored.with_suffix(".json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return meta


def restore(meta: dict[str, Any]) -> Path:
    stored, original = Path(meta["stored_path"]), Path(meta["original_path"])
    if not stored.is_file():
        raise ActionDenied("File is no longer in quarantine")
    target = _free_target(original)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(stored), str(target))
    meta_file = stored.with_suffix(".json")
    if meta_file.exists():
        meta_file.unlink()
    return target


def to_recycle_bin(path: Path) -> None:
    from send2trash import send2trash

    send2trash(os.fspath(path))
