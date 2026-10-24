"""Repair of a corrupted database (`sentinel db check|repair`).

Procedure: back up all files -> rescue readable rows from two views (with and without the
WAL file) -> new database with all migrations -> copy rows over -> rebuild the full-text
index -> swap. The old files stay in the backup folder.
"""

from __future__ import annotations

import shutil
import sqlite3
import tempfile
import time
from pathlib import Path
from typing import Any

from sentinel.db import Database

FTS_TABLES = ("memories_fts", "brain_fts")
SUFFIXES = ("", "-wal", "-shm")


def check(path: Path) -> str:
    """'ok' or the error message from SQLite."""
    try:
        con = sqlite3.connect(path, timeout=5)
        try:
            result = con.execute("PRAGMA quick_check").fetchone()
        finally:
            con.close()
    except sqlite3.DatabaseError as exc:
        return str(exc)
    return str(result[0]) if result else "unknown"


def _read_tables(path: Path) -> dict[str, tuple[list[str], list[tuple[Any, ...]]]]:
    """All readable rows per table. Damaged areas are skipped."""
    result: dict[str, tuple[list[str], list[tuple[Any, ...]]]] = {}
    try:
        con = sqlite3.connect(path, timeout=5)  # copies only, never the original
    except sqlite3.DatabaseError:
        return result
    try:
        try:
            tables = [
                r[0]
                for r in con.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table' AND sql NOT LIKE "
                    "'CREATE VIRTUAL%' AND name NOT LIKE 'sqlite_%'"
                )
            ]
        except sqlite3.DatabaseError:
            return result
        for table in tables:
            if table.startswith(FTS_TABLES):
                continue
            try:
                columns = [r[1] for r in con.execute(f'PRAGMA table_info("{table}")')]
            except sqlite3.DatabaseError:
                continue
            rows: list[tuple[Any, ...]] = []
            try:
                for row in con.execute(f'SELECT * FROM "{table}"'):  # noqa: S608 - name comes from sqlite_master
                    rows.append(tuple(row))
            except sqlite3.DatabaseError:
                pass  # rest of the table is damaged - keep what was read so far
            result[table] = (columns, rows)
    finally:
        con.close()
    return result


def _copy(files_from: Path, to: Path, *, with_wal: bool) -> Path:
    to.mkdir(parents=True, exist_ok=True)
    target = to / files_from.name
    shutil.copy2(files_from, target)
    if with_wal:
        wal = files_from.with_name(files_from.name + "-wal")
        if wal.is_file():
            shutil.copy2(wal, target.with_name(target.name + "-wal"))
    return target


def repair(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise FileNotFoundError(path)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    backup_dir = path.parent / "backup" / f"db-{stamp}"
    backup_dir.mkdir(parents=True, exist_ok=True)
    for suffix in SUFFIXES:
        source = path.with_name(path.name + suffix)
        if source.is_file():
            shutil.copy2(source, backup_dir / source.name)

    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        # Two views: with WAL (newest state) and main file only (older, often intact)
        full = _read_tables(_copy(path, tmp_dir / "full", with_wal=True))
        main = _read_tables(_copy(path, tmp_dir / "main", with_wal=False))
        fresh_path = tmp_dir / "fresh.db"
        fresh = Database(fresh_path)
        report: dict[str, int] = {}
        con = fresh._con
        con.execute("PRAGMA foreign_keys = OFF")
        target_tables = {
            r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        for table in sorted(set(full) | set(main)):
            if table not in target_tables:
                continue
            a, b = full.get(table), main.get(table)
            columns, rows = max((x for x in (a, b) if x), key=lambda x: len(x[1]))
            target_cols = [r[1] for r in con.execute(f'PRAGMA table_info("{table}")')]
            keep = [i for i, c in enumerate(columns) if c in target_cols]
            names = ", ".join(f'"{columns[i]}"' for i in keep)
            marks = ", ".join("?" for _ in keep)
            saved = 0
            for row in rows:
                try:
                    con.execute(
                        f'INSERT OR IGNORE INTO "{table}" ({names}) VALUES ({marks})',  # noqa: S608
                        [row[i] for i in keep],
                    )
                    saved += 1
                except sqlite3.DatabaseError:
                    continue
            report[table] = saved
        for fts in FTS_TABLES:
            if fts in target_tables:
                con.execute(f"INSERT INTO {fts}({fts}) VALUES('rebuild')")
        con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        fresh.close()
        if check(fresh_path) != "ok":
            raise RuntimeError("New database is not healthy - nothing replaced")
        # First check that all files are free - otherwise touch nothing
        moves = [
            (path.with_name(path.name + suffix), backup_dir / f"replaced{suffix or '.db'}")
            for suffix in SUFFIXES
            if path.with_name(path.name + suffix).is_file()
        ]
        for old, _ in moves:
            with open(old, "r+b"):
                pass
        for old, target in moves:
            old.replace(target)
        shutil.copy2(fresh_path, path)
    return {"backup": str(backup_dir), "tables": report}


def restore_latest(path: Path, auto_dir: Path) -> Path:
    """Restore the newest automatic backup (the current file is backed up first)."""
    backups = sorted(auto_dir.glob("sentinel-*.db")) if auto_dir.is_dir() else []
    if not backups:
        raise FileNotFoundError(auto_dir)
    latest = backups[-1]
    if check(latest) != "ok":
        raise RuntimeError(f"Backup {latest.name} is itself corrupted")
    stamp = time.strftime("%Y%m%d-%H%M%S")
    keep = path.parent / "backup" / f"db-{stamp}"
    keep.mkdir(parents=True, exist_ok=True)
    for suffix in SUFFIXES:
        old = path.with_name(path.name + suffix)
        if old.is_file():
            old.replace(keep / f"replaced{suffix or '.db'}")
    shutil.copy2(latest, path)
    return latest
