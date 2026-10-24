"""Memory: SQLite + FTS5, extended with local vectors (semantic search).

Rules: no secrets, every memory has a source, everything can be viewed, edited and
deleted. Memories never grant permissions.
"""

from __future__ import annotations

import re
import time
from typing import Any

from sentinel.db import Database
from sentinel.memory.embeddings import Embedder, cosine, create_embedder
from sentinel.secrets_filter import mentions_secret_topic

CATEGORIES = {"person", "preference", "work", "place", "task", "note"}
MAX_LEN = 500


class MemoryRejected(ValueError):  # noqa: N818
    pass


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())


def _fts_query(query: str) -> str | None:
    terms = [t for t in re.findall(r"\w+", query.lower()) if len(t) >= 2]
    if not terms:
        return None
    # Every term as a quoted phrase → no FTS syntax injection
    return " OR ".join(f'"{t}"*' for t in terms[:12])


class MemoryStore:
    def __init__(self, db: Database, embedder: Embedder | None = None) -> None:
        self.db = db
        self.embedder = embedder or create_embedder()

    def _check(self, text: str) -> str:
        text = text.strip()
        if not text:
            raise MemoryRejected("Empty memory")
        if len(text) > MAX_LEN:
            raise MemoryRejected(f"Too long (max. {MAX_LEN} characters)")
        if mentions_secret_topic(text):
            raise MemoryRejected("Looks like a password, key or token – never stored")
        return text

    def add(self, text: str, *, category: str = "note", source: str) -> dict[str, Any]:
        text = self._check(text)
        if category not in CATEGORIES:
            category = "note"
        if not source:
            raise MemoryRejected("Source missing")
        norm = _normalize(text)
        for row in self.db.query("SELECT id, text FROM memories"):
            if _normalize(row["text"]) == norm:
                return self.get(row["id"]) or {}
        now = time.time()
        memory_id = self.db.execute(
            "INSERT INTO memories(text, category, source, created, updated) VALUES(?,?,?,?,?)",
            (text, category, source, now, now),
        )
        self._store_vector(memory_id, text)
        return self.get(memory_id) or {}

    def _store_vector(self, memory_id: int, text: str) -> None:
        vec = self.embedder.embed(text)
        self.db.execute(
            "INSERT OR REPLACE INTO memory_vectors(memory_id, kind, vec) VALUES(?,?,?)",
            (memory_id, self.embedder.kind, vec.tobytes()),
        )

    def get(self, memory_id: int) -> dict[str, Any] | None:
        return self.db.query_one("SELECT * FROM memories WHERE id = ?", (memory_id,))

    def list(self, limit: int = 200) -> list[dict[str, Any]]:
        return self.db.query("SELECT * FROM memories ORDER BY updated DESC LIMIT ?", (limit,))

    def update(self, memory_id: int, text: str, category: str | None = None) -> dict[str, Any]:
        text = self._check(text)
        current = self.get(memory_id)
        if current is None:
            raise KeyError(memory_id)
        self.db.execute(
            "UPDATE memories SET text = ?, category = ?, updated = ? WHERE id = ?",
            (
                text,
                category if category in CATEGORIES else current["category"],
                time.time(),
                memory_id,
            ),
        )
        self._store_vector(memory_id, text)
        return self.get(memory_id) or {}

    def delete(self, memory_id: int) -> bool:
        self.db.execute("DELETE FROM memory_vectors WHERE memory_id = ?", (memory_id,))
        return bool(self.db.execute("DELETE FROM memories WHERE id = ?", (memory_id,)))

    def delete_all(self) -> None:
        self.db.execute("DELETE FROM memory_vectors")
        self.db.execute("DELETE FROM memories")

    def search(self, query: str, limit: int = 5) -> list[dict[str, Any]]:
        """Hybrid search: FTS5 (BM25) plus vector similarity."""
        scores: dict[int, float] = {}
        fts = _fts_query(query)
        if fts:
            rows = self.db.query(
                "SELECT rowid AS id, bm25(memories_fts) AS rank FROM memories_fts "
                "WHERE memories_fts MATCH ? ORDER BY rank LIMIT 20",
                (fts,),
            )
            for pos, row in enumerate(rows):
                scores[row["id"]] = scores.get(row["id"], 0.0) + 1.0 / (pos + 2)

        qvec = self.embedder.embed(query)
        sims = []
        for row in self.db.query(
            "SELECT memory_id, vec FROM memory_vectors WHERE kind = ?", (self.embedder.kind,)
        ):
            sim = cosine(qvec, self.embedder.from_bytes(row["vec"]))
            if sim >= self.embedder.min_similarity:
                sims.append((sim, row["memory_id"]))
        sims.sort(reverse=True)
        for pos, (_sim, memory_id) in enumerate(sims[:20]):
            scores[memory_id] = scores.get(memory_id, 0.0) + 1.0 / (pos + 2)

        ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)[:limit]
        result = []
        for memory_id, score in ranked:
            row = self.get(memory_id)
            if row:
                row["score"] = round(score, 3)
                result.append(row)
        return result

    def reindex(self) -> int:
        rows = self.db.query("SELECT id, text FROM memories")
        for row in rows:
            self._store_vector(row["id"], row["text"])
        return len(rows)
