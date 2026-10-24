"""Repair of a corrupted database: backup, rescue, new index."""

from __future__ import annotations

from pathlib import Path

from sentinel import dbrepair
from sentinel.db import Database


def make_db(path: Path) -> None:
    db = Database(path)
    db.execute(
        "INSERT INTO memories(text, category, source, created, updated) VALUES(?,?,?,?,?)",
        ("Mag Tee", "preference", "ui", 1.0, 1.0),
    )
    db.execute("INSERT INTO settings(key, value) VALUES('autonomy', '2')")
    db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    db.close()


def test_healthy_db_is_ok(tmp_path: Path) -> None:
    path = tmp_path / "sentinel.db"
    make_db(path)
    assert dbrepair.check(path) == "ok"


def test_repair_keeps_rows_and_backup(tmp_path: Path) -> None:
    path = tmp_path / "sentinel.db"
    make_db(path)
    # Page 1 (schema) stays intact, a later page is destroyed
    raw = bytearray(path.read_bytes())
    page = 4096
    if len(raw) > page * 3:
        raw[page * 2 + 100 : page * 2 + 400] = b"\xff" * 300
    path.write_bytes(bytes(raw))
    report = dbrepair.repair(path)
    assert Path(report["backup"]).is_dir()
    assert dbrepair.check(path) == "ok"
    db = Database(path)
    assert db.query_one("SELECT value FROM settings WHERE key = 'autonomy'")["value"] == "2"
    # Full-text index works again
    db.query("SELECT rowid FROM memories_fts WHERE memories_fts MATCH 'tee'")
    db.close()


def test_repair_survives_unreadable_wal(tmp_path: Path) -> None:
    path = tmp_path / "sentinel.db"
    make_db(path)
    path.with_name("sentinel.db-wal").write_bytes(b"kaputt" * 1000)
    report = dbrepair.repair(path)
    assert report["tables"]["memories"] == 1
    assert dbrepair.check(path) == "ok"


def test_instance_lock_allows_only_one(tmp_path: Path) -> None:
    from sentinel.db import InstanceLock

    first = InstanceLock(tmp_path)
    assert first.acquire()
    second = InstanceLock(tmp_path)
    assert not second.acquire()
    assert second.held_by_other()
    first.release()
    assert not second.held_by_other()
    assert second.acquire()
    second.release()


def test_daily_backup_and_restore(tmp_path: Path) -> None:
    from sentinel.db import backup_database

    path = tmp_path / "sentinel.db"
    make_db(path)
    db = Database(path)
    auto = tmp_path / "backup" / "auto"
    for _ in range(9):
        assert backup_database(db, auto, keep=7)
        import time as _t

        _t.sleep(1.01)
    assert len(list(auto.glob("sentinel-*.db"))) == 7
    db.execute("DELETE FROM settings")
    db.close()
    restored = dbrepair.restore_latest(path, auto)
    assert restored.parent == auto
    db = Database(path)
    assert db.query_one("SELECT value FROM settings WHERE key = 'autonomy'")["value"] == "2"
    db.close()


def test_no_backup_of_damaged_db(tmp_path: Path) -> None:
    from sentinel.db import backup_database

    class Broken:
        import threading

        lock = threading.RLock()

        class _con:  # noqa: N801
            @staticmethod
            def execute(sql: str):
                class R:
                    @staticmethod
                    def fetchone():
                        return ("*** in database main ***",)

                return R()

    assert backup_database(Broken(), tmp_path / "auto") is None  # type: ignore[arg-type]
