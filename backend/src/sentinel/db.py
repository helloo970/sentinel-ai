"""SQLite access with simple, versioned migrations."""

from __future__ import annotations

import sqlite3
import sys
import threading
import time
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

MIGRATIONS: list[str] = [
    # 1: Core
    """
    CREATE TABLE settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);

    CREATE TABLE audit(
        id INTEGER PRIMARY KEY,
        ts TEXT NOT NULL,
        actor TEXT NOT NULL,
        source TEXT NOT NULL,
        action TEXT NOT NULL,
        params TEXT NOT NULL,
        decision TEXT NOT NULL,
        result TEXT NOT NULL,
        detail TEXT NOT NULL,
        prev_hash TEXT NOT NULL,
        hash TEXT NOT NULL
    );
    -- The audit log is append-only
    CREATE TRIGGER audit_no_update BEFORE UPDATE ON audit
        BEGIN SELECT RAISE(ABORT, 'audit log is append-only'); END;
    CREATE TRIGGER audit_no_delete BEFORE DELETE ON audit
        BEGIN SELECT RAISE(ABORT, 'audit log is append-only'); END;

    CREATE TABLE approvals(
        id TEXT PRIMARY KEY,
        created REAL NOT NULL,
        expires REAL NOT NULL,
        action TEXT NOT NULL,
        params TEXT NOT NULL,
        source TEXT NOT NULL,
        actor TEXT NOT NULL,
        risk TEXT NOT NULL,
        reason TEXT NOT NULL,
        preview TEXT NOT NULL,
        tainted INTEGER NOT NULL DEFAULT 0,
        status TEXT NOT NULL DEFAULT 'pending',
        decided REAL
    );

    CREATE TABLE undo(
        id TEXT PRIMARY KEY,
        created REAL NOT NULL,
        expires REAL NOT NULL,
        action TEXT NOT NULL,
        summary TEXT NOT NULL,
        data TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'open'
    );

    CREATE TABLE autonomous_actions(id INTEGER PRIMARY KEY, ts REAL NOT NULL, action TEXT NOT NULL);
    """,
    # 2: Chat, memory, costs
    """
    CREATE TABLE chat_messages(
        id INTEGER PRIMARY KEY,
        conversation TEXT NOT NULL,
        role TEXT NOT NULL,
        content TEXT NOT NULL,
        tainted INTEGER NOT NULL DEFAULT 0,
        ts REAL NOT NULL
    );
    CREATE INDEX chat_messages_conv ON chat_messages(conversation, id);

    CREATE TABLE memories(
        id INTEGER PRIMARY KEY,
        text TEXT NOT NULL,
        category TEXT NOT NULL,
        source TEXT NOT NULL,
        created REAL NOT NULL,
        updated REAL NOT NULL
    );
    CREATE VIRTUAL TABLE memories_fts USING fts5(text, content='memories', content_rowid='id',
        tokenize='unicode61 remove_diacritics 2');
    CREATE TRIGGER memories_ai AFTER INSERT ON memories BEGIN
        INSERT INTO memories_fts(rowid, text) VALUES (new.id, new.text);
    END;
    CREATE TRIGGER memories_ad AFTER DELETE ON memories BEGIN
        INSERT INTO memories_fts(memories_fts, rowid, text) VALUES('delete', old.id, old.text);
    END;
    CREATE TRIGGER memories_au AFTER UPDATE ON memories BEGIN
        INSERT INTO memories_fts(memories_fts, rowid, text) VALUES('delete', old.id, old.text);
        INSERT INTO memories_fts(rowid, text) VALUES (new.id, new.text);
    END;
    CREATE TABLE memory_vectors(
        memory_id INTEGER PRIMARY KEY, kind TEXT NOT NULL, vec BLOB NOT NULL
    );

    CREATE TABLE usage(
        id INTEGER PRIMARY KEY,
        ts REAL NOT NULL,
        model TEXT NOT NULL,
        purpose TEXT NOT NULL,
        input_tokens INTEGER NOT NULL,
        output_tokens INTEGER NOT NULL,
        cost_eur REAL NOT NULL
    );
    """,
    # 3: Security, network, automation, agents
    """
    CREATE TABLE alerts(
        id INTEGER PRIMARY KEY,
        ts REAL NOT NULL,
        rule TEXT NOT NULL,
        severity TEXT NOT NULL,
        title TEXT NOT NULL,
        detail TEXT NOT NULL,
        fingerprint TEXT NOT NULL UNIQUE,
        status TEXT NOT NULL DEFAULT 'new',
        response TEXT
    );
    CREATE TABLE baselines(kind TEXT NOT NULL, key TEXT NOT NULL, first_seen REAL NOT NULL,
        PRIMARY KEY(kind, key));
    CREATE TABLE devices(
        mac TEXT PRIMARY KEY,
        ip TEXT NOT NULL,
        hostname TEXT,
        label TEXT,
        trusted INTEGER NOT NULL DEFAULT 0,
        first_seen REAL NOT NULL,
        last_seen REAL NOT NULL
    );
    CREATE TABLE reminders(
        id INTEGER PRIMARY KEY,
        text TEXT NOT NULL,
        due REAL NOT NULL,
        created REAL NOT NULL,
        done INTEGER NOT NULL DEFAULT 0
    );
    CREATE TABLE quarantine(
        id INTEGER PRIMARY KEY,
        original_path TEXT NOT NULL,
        stored_path TEXT NOT NULL,
        sha256 TEXT NOT NULL,
        ts REAL NOT NULL,
        restored INTEGER NOT NULL DEFAULT 0
    );
    CREATE TABLE agent_reports(
        id INTEGER PRIMARY KEY,
        agent TEXT NOT NULL,
        ts REAL NOT NULL,
        title TEXT NOT NULL,
        body TEXT NOT NULL
    );
    """,
    # 4: Order suggestions
    """
    CREATE TABLE orders(
        id INTEGER PRIMARY KEY,
        ts REAL NOT NULL,
        query TEXT NOT NULL,
        data TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'proposed'
    );
    """,
    # 5: Server history and device profiles
    """
    CREATE TABLE server_metrics(
        id INTEGER PRIMARY KEY,
        ts REAL NOT NULL,
        load1 REAL NOT NULL,
        cpu_count INTEGER NOT NULL,
        memory_percent REAL NOT NULL,
        disk_percent REAL NOT NULL,
        auth_failures INTEGER NOT NULL
    );
    CREATE INDEX server_metrics_ts ON server_metrics(ts);
    ALTER TABLE devices ADD COLUMN ports TEXT;
    ALTER TABLE devices ADD COLUMN ports_checked REAL;
    """,
    # 6: File scanning
    """
    CREATE TABLE malware_hashes(sha256 TEXT PRIMARY KEY, added REAL NOT NULL);
    CREATE TABLE file_scans(
        id INTEGER PRIMARY KEY,
        ts REAL NOT NULL,
        path TEXT NOT NULL,
        sha256 TEXT NOT NULL,
        verdict TEXT NOT NULL,
        defender TEXT NOT NULL
    );
    """,
    # 7: Price alerts and shopping list
    """
    CREATE TABLE price_watches(
        id INTEGER PRIMARY KEY,
        url TEXT NOT NULL UNIQUE,
        shop TEXT NOT NULL,
        title TEXT NOT NULL DEFAULT '',
        target REAL,
        currency TEXT NOT NULL DEFAULT 'EUR',
        last_price REAL,
        last_check REAL NOT NULL DEFAULT 0,
        last_error TEXT,
        created REAL NOT NULL,
        active INTEGER NOT NULL DEFAULT 1
    );
    CREATE TABLE price_history(
        watch_id INTEGER NOT NULL REFERENCES price_watches(id) ON DELETE CASCADE,
        ts REAL NOT NULL,
        price REAL NOT NULL
    );
    CREATE INDEX price_history_watch ON price_history(watch_id, ts);
    CREATE TABLE shopping_items(
        id INTEGER PRIMARY KEY,
        text TEXT NOT NULL,
        created REAL NOT NULL,
        done INTEGER NOT NULL DEFAULT 0
    );
    """,
    # 8: Brain (knowledge graph, separate from memory)
    """
    CREATE TABLE brain_categories(
        key TEXT PRIMARY KEY,
        label TEXT NOT NULL,
        builtin INTEGER NOT NULL DEFAULT 0,
        created REAL NOT NULL
    );
    CREATE TABLE brain_nodes(
        id INTEGER PRIMARY KEY,
        key TEXT NOT NULL UNIQUE,
        label TEXT NOT NULL,
        category TEXT NOT NULL REFERENCES brain_categories(key),
        summary TEXT NOT NULL DEFAULT '',
        origin TEXT NOT NULL,
        source TEXT NOT NULL,
        hits INTEGER NOT NULL DEFAULT 0,
        created REAL NOT NULL,
        updated REAL NOT NULL
    );
    CREATE TABLE brain_edges(
        a INTEGER NOT NULL REFERENCES brain_nodes(id) ON DELETE CASCADE,
        b INTEGER NOT NULL REFERENCES brain_nodes(id) ON DELETE CASCADE,
        relation TEXT NOT NULL DEFAULT '',
        created REAL NOT NULL,
        PRIMARY KEY(a, b)
    );
    CREATE VIRTUAL TABLE brain_fts USING fts5(label, summary, content='brain_nodes',
        content_rowid='id', tokenize='unicode61 remove_diacritics 2');
    CREATE TRIGGER brain_ai AFTER INSERT ON brain_nodes BEGIN
        INSERT INTO brain_fts(rowid, label, summary) VALUES (new.id, new.label, new.summary);
    END;
    CREATE TRIGGER brain_ad AFTER DELETE ON brain_nodes BEGIN
        INSERT INTO brain_fts(brain_fts, rowid, label, summary)
            VALUES('delete', old.id, old.label, old.summary);
    END;
    CREATE TRIGGER brain_au AFTER UPDATE ON brain_nodes BEGIN
        INSERT INTO brain_fts(brain_fts, rowid, label, summary)
            VALUES('delete', old.id, old.label, old.summary);
        INSERT INTO brain_fts(rowid, label, summary) VALUES (new.id, new.label, new.summary);
    END;
    CREATE TABLE brain_queue(
        id INTEGER PRIMARY KEY,
        kind TEXT NOT NULL,
        ref TEXT NOT NULL UNIQUE,
        text TEXT NOT NULL,
        created REAL NOT NULL,
        done INTEGER NOT NULL DEFAULT 0
    );
    CREATE TABLE brain_files(path TEXT PRIMARY KEY, mtime REAL NOT NULL);
    """,
]


class Database:
    """One connection per process, protected by a lock."""

    def __init__(self, path: Path | str) -> None:
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._con = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        self._con.row_factory = sqlite3.Row
        self._con.execute("PRAGMA foreign_keys = ON")
        if self.path != ":memory:":
            self._con.execute("PRAGMA journal_mode = WAL")
        self._migrate()

    def _migrate(self) -> None:
        with self._lock:
            version = self._con.execute("PRAGMA user_version").fetchone()[0]
            for index, script in enumerate(MIGRATIONS[version:], start=version + 1):
                self._con.executescript(
                    f"BEGIN;\n{script}\nPRAGMA user_version = {index};\nCOMMIT;"
                )

    @property
    def lock(self) -> threading.RLock:
        return self._lock

    def execute(self, sql: str, args: Sequence[Any] = ()) -> int:
        with self._lock:
            cur = self._con.execute(sql, args)
            return cur.lastrowid if cur.lastrowid is not None else cur.rowcount

    def executemany(self, sql: str, rows: Iterable[Sequence[Any]]) -> None:
        with self._lock:
            self._con.executemany(sql, rows)

    def query(self, sql: str, args: Sequence[Any] = ()) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(row) for row in self._con.execute(sql, args).fetchall()]

    def query_one(self, sql: str, args: Sequence[Any] = ()) -> dict[str, Any] | None:
        rows = self.query(sql, args)
        return rows[0] if rows else None

    def close(self) -> None:
        with self._lock:
            self._con.close()


class InstanceLock:
    """Only one program writes to the database at a time (protects against corruption)."""

    def __init__(self, data_dir: Path) -> None:
        self.path = Path(data_dir) / "sentinel.lock"
        self._fh: Any = None

    def acquire(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fh = open(self.path, "a+b")  # noqa: SIM115 - stays open while the program runs
        try:
            if sys.platform == "win32":
                import msvcrt

                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            fh.close()
            return False
        self._fh = fh
        return True

    def release(self) -> None:
        if self._fh is None:
            return
        try:
            if sys.platform == "win32":
                import msvcrt

                self._fh.seek(0)
                msvcrt.locking(self._fh.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
        finally:
            self._fh.close()
            self._fh = None

    def held_by_other(self) -> bool:
        """True if another program (e.g. the app) is currently using the database."""
        if self._fh is not None:
            return False
        if not self.acquire():
            return True
        self.release()
        return False


def backup_database(db: Database, folder: Path, keep: int = 7) -> Path | None:
    """Daily backup using the SQLite backup API. Only if the database is intact."""
    with db.lock:
        result = db._con.execute("PRAGMA quick_check").fetchone()
        if not result or result[0] != "ok":
            return None
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / f"sentinel-{time.strftime('%Y%m%d-%H%M%S')}.db"
        dst = sqlite3.connect(target)
        try:
            db._con.backup(dst)
        finally:
            dst.close()
    for old in sorted(folder.glob("sentinel-*.db"))[:-keep]:
        old.unlink(missing_ok=True)
    return target


class SettingsStore:
    """Runtime settings (autonomy, emergency stop, ...) in the settings table."""

    def __init__(self, db: Database) -> None:
        self.db = db

    def get(self, key: str, default: str | None = None) -> str | None:
        row = self.db.query_one("SELECT value FROM settings WHERE key = ?", (key,))
        return row["value"] if row else default

    def set(self, key: str, value: str) -> None:
        self.db.execute(
            "INSERT INTO settings(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
