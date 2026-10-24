"""Knowledge graph in SQLite: nodes (knowledge) with category, source and connections.

Rules: every node has a source and an origin (user, chat, seed, system, ai, file).
Off-limits content (privacy.py) is rejected. Knowledge never grants permissions.
Everything can be viewed and deleted.
"""

from __future__ import annotations

import re
import time
from typing import Any

from sentinel.brain.privacy import blocked_reason
from sentinel.db import Database

# Built-in categories (key → label); the AI may add more. Keys are data, only labels are shown.
BUILTIN_CATEGORIES: dict[str, str] = {
    "ich": "Me & People",
    "projekte": "Projects & Notes",
    "geraete": "Devices & Servers",
    "skills": "Hand",
    "schule": "School & Training",
    "interessen": "Interests",
    "sprachen": "Languages",
    "ziele": "Goals",
    "wissen": "Knowledge",
    "stimme": "Voice",
    "quellen": "Sources",
    "stil": "Style",
    "logbuch": "Logbook",
    "gedaechtnis": "Memory",
    "aufgaben": "Tasks & Appointments",
    "sicherheit": "Security",
    "einkaufen": "Shopping",
}
ORIGINS = {"user", "chat", "seed", "system", "sensor", "ai", "file"}
# Origins from external content (files, logs, network, web): only as marked data to the AI
UNTRUSTED_ORIGINS = {"file", "sensor"}
CATEGORY_KEY = re.compile(r"^[a-z][a-z0-9_]{2,19}$")
MAX_CATEGORIES = 30
MAX_LABEL = 80
MAX_SUMMARY = 300
MAX_NODES = 5000


class BrainRejected(ValueError):  # noqa: N818
    pass


def node_key(label: str) -> str:
    return re.sub(r"\s+", " ", label.strip().lower())


def _fts_query(query: str) -> str | None:
    terms = [t for t in re.findall(r"\w+", query.lower()) if len(t) >= 2]
    if not terms:
        return None
    return " OR ".join(f'"{t}"*' for t in terms[:12])


class BrainStore:
    def __init__(self, db: Database) -> None:
        self.db = db
        now = time.time()
        self.db.executemany(
            "INSERT INTO brain_categories(key, label, builtin, created) VALUES(?,?,1,?) "
            "ON CONFLICT(key) DO UPDATE SET label = excluded.label WHERE builtin = 1",
            [(key, label, now) for key, label in BUILTIN_CATEGORIES.items()],
        )

    # ---------------------------------------------------------------- Categories

    def categories(self) -> list[dict[str, Any]]:
        return self.db.query("SELECT key, label, builtin FROM brain_categories ORDER BY created")

    def ensure_category(self, key: str, label: str | None = None) -> str:
        key = key.strip().lower()
        if self.db.query_one("SELECT key FROM brain_categories WHERE key = ?", (key,)):
            return key
        label = (label or "").strip()[:40]
        count = self.db.query_one("SELECT COUNT(*) AS n FROM brain_categories")
        if (
            not CATEGORY_KEY.match(key)
            or not label
            or blocked_reason(label)
            or (count and count["n"] >= MAX_CATEGORIES)
        ):
            return "wissen"
        self.db.execute(
            "INSERT INTO brain_categories(key, label, builtin, created) VALUES(?,?,0,?)",
            (key, label, time.time()),
        )
        return key

    # ---------------------------------------------------------------- Nodes

    def add(
        self,
        label: str,
        *,
        category: str = "wissen",
        summary: str = "",
        origin: str,
        source: str,
        category_label: str | None = None,
    ) -> dict[str, Any]:
        label = re.sub(r"\s+", " ", label).strip()[:MAX_LABEL]
        summary = re.sub(r"\s+", " ", summary).strip()[:MAX_SUMMARY]
        if len(label) < 2:
            raise BrainRejected("Empty node")
        if origin not in ORIGINS or not source:
            raise BrainRejected("Origin or source missing")
        reason = blocked_reason(f"{label} {summary}")
        if reason:
            raise BrainRejected(reason)
        category = self.ensure_category(category, category_label)
        key = node_key(label)
        now = time.time()
        existing = self.db.query_one("SELECT * FROM brain_nodes WHERE key = ?", (key,))
        if existing:
            machine = {"system", "sensor"}
            if origin in machine and existing["origin"] in machine:
                # Data from SENTINEL: always the current state
                if summary != existing["summary"] or origin != existing["origin"]:
                    self.db.execute(
                        "UPDATE brain_nodes SET summary = ?, origin = ?, updated = ? WHERE id = ?",
                        (summary, origin, now, existing["id"]),
                    )
                return self.get(existing["id"]) or {}
            # Known node: only extend the summary, never overwrite the user's knowledge
            if summary and summary not in existing["summary"] and existing["origin"] != "user":
                merged = f"{existing['summary']} {summary}".strip()[-MAX_SUMMARY:]
                self.db.execute(
                    "UPDATE brain_nodes SET summary = ?, updated = ? WHERE id = ?",
                    (merged, now, existing["id"]),
                )
            elif origin == "user" and summary:
                self.db.execute(
                    "UPDATE brain_nodes SET summary = ?, origin = 'user', source = ?, "
                    "updated = ? WHERE id = ?",
                    (summary, source, now, existing["id"]),
                )
            return self.get(existing["id"]) or {}
        count = self.db.query_one("SELECT COUNT(*) AS n FROM brain_nodes")
        if count and count["n"] >= MAX_NODES:
            raise BrainRejected("The brain is full")
        node_id = self.db.execute(
            "INSERT INTO brain_nodes(key, label, category, summary, origin, source, created, "
            "updated) VALUES(?,?,?,?,?,?,?,?)",
            (key, label, category, summary, origin, source, now, now),
        )
        return self.get(node_id) or {}

    def get(self, node_id: int) -> dict[str, Any] | None:
        return self.db.query_one("SELECT * FROM brain_nodes WHERE id = ?", (node_id,))

    def find(self, label: str) -> dict[str, Any] | None:
        return self.db.query_one("SELECT * FROM brain_nodes WHERE key = ?", (node_key(label),))

    def link(self, a: int, b: int, relation: str = "") -> bool:
        if a == b or not self.get(a) or not self.get(b):
            return False
        low, high = sorted((a, b))
        self.db.execute(
            "INSERT OR IGNORE INTO brain_edges(a, b, relation, created) VALUES(?,?,?,?)",
            (low, high, relation[:40], time.time()),
        )
        return True

    def neighbors(self, node_id: int) -> list[dict[str, Any]]:
        return self.db.query(
            "SELECT n.* FROM brain_edges e JOIN brain_nodes n "
            "ON n.id = CASE WHEN e.a = ? THEN e.b ELSE e.a END "
            "WHERE e.a = ? OR e.b = ? ORDER BY n.label",
            (node_id, node_id, node_id),
        )

    def delete(self, node_id: int) -> bool:
        return bool(self.db.execute("DELETE FROM brain_nodes WHERE id = ?", (node_id,)))

    def delete_where_source_prefix(self, prefix: str, keep_ids: set[int]) -> int:
        """Removes machine nodes with this source prefix that were not kept in the last sync."""
        removed = 0
        for row in self.db.query(
            "SELECT id, source FROM brain_nodes WHERE origin IN ('system', 'sensor') "
            "AND source LIKE ?",
            (prefix + "%",),
        ):
            if int(row["id"]) not in keep_ids:
                removed += self.delete(row["id"])
        return removed

    def in_category(self, category: str, limit: int = 20) -> list[dict[str, Any]]:
        return self.db.query(
            "SELECT * FROM brain_nodes WHERE category = ? ORDER BY updated DESC LIMIT ?",
            (category, limit),
        )

    def touch(self, ids: list[int]) -> None:
        self.db.executemany(
            "UPDATE brain_nodes SET hits = hits + 1 WHERE id = ?", [(i,) for i in ids]
        )

    # ---------------------------------------------------------------- Search

    def search(self, query: str, limit: int = 6) -> list[dict[str, Any]]:
        fts = _fts_query(query)
        if not fts:
            return []
        rows = self.db.query(
            "SELECT n.*, bm25(brain_fts, 4.0, 1.0) AS rank FROM brain_fts "
            "JOIN brain_nodes n ON n.id = brain_fts.rowid "
            "WHERE brain_fts MATCH ? ORDER BY rank LIMIT ?",
            (fts, limit),
        )
        return rows

    def lookup(self, topic: str) -> dict[str, Any] | None:
        """Best match for "What do you know about …": exact name before full text."""
        return self.find(topic) or next(iter(self.search(topic, limit=1)), None)

    # ---------------------------------------------------------------- View

    def graph(self) -> dict[str, Any]:
        nodes = self.db.query(
            "SELECT id, label, category, summary, origin, source, hits, updated FROM brain_nodes"
        )
        edges = self.db.query("SELECT a, b, relation FROM brain_edges")
        return {"categories": self.categories(), "nodes": nodes, "edges": edges}

    def stats(self) -> dict[str, int]:
        nodes = self.db.query_one("SELECT COUNT(*) AS n FROM brain_nodes")
        edges = self.db.query_one("SELECT COUNT(*) AS n FROM brain_edges")
        return {"nodes": nodes["n"] if nodes else 0, "edges": edges["n"] if edges else 0}

    def export(self) -> dict[str, Any]:
        return {
            "version": 1,
            "categories": self.db.query("SELECT * FROM brain_categories"),
            "nodes": self.db.query("SELECT * FROM brain_nodes"),
            "edges": self.db.query("SELECT * FROM brain_edges"),
        }

    def import_(self, data: dict[str, Any]) -> int:
        """Restores a backup (replaces the current brain)."""
        if data.get("version") != 1:
            raise BrainRejected("Unknown backup format")
        with self.db.lock:
            self.db.execute("DELETE FROM brain_edges")
            self.db.execute("DELETE FROM brain_nodes")
            for cat in data.get("categories", []):
                self.db.execute(
                    "INSERT OR IGNORE INTO brain_categories(key, label, builtin, created) "
                    "VALUES(?,?,?,?)",
                    (cat["key"], cat["label"], int(cat.get("builtin", 0)), cat["created"]),
                )
            for n in data.get("nodes", []):
                self.db.execute(
                    "INSERT INTO brain_nodes(id, key, label, category, summary, origin, source, "
                    "hits, created, updated) VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (
                        n["id"],
                        n["key"],
                        n["label"],
                        n["category"],
                        n["summary"],
                        n["origin"],
                        n["source"],
                        n.get("hits", 0),
                        n["created"],
                        n["updated"],
                    ),
                )
            for e in data.get("edges", []):
                self.db.execute(
                    "INSERT OR IGNORE INTO brain_edges(a, b, relation, created) VALUES(?,?,?,?)",
                    (e["a"], e["b"], e.get("relation", ""), e["created"]),
                )
        return len(data.get("nodes", []))
