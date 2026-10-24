"""Append-only audit log with a hash chain (tampering becomes detectable)."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any

from sentinel.db import Database
from sentinel.secrets_filter import redact

GENESIS = "0" * 64


def _canonical(record: dict[str, Any]) -> str:
    return json.dumps(record, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


class AuditLog:
    def __init__(self, db: Database) -> None:
        self.db = db

    def append(
        self,
        *,
        actor: str,
        source: str,
        action: str,
        params: dict[str, Any] | None = None,
        decision: str,
        result: str = "",
        detail: str = "",
    ) -> int:
        record = {
            "ts": datetime.now(UTC).isoformat(timespec="milliseconds"),
            "actor": actor,
            "source": source,
            "action": action,
            "params": redact(
                json.dumps(params or {}, sort_keys=True, ensure_ascii=False, default=str)
            ),
            "decision": decision,
            "result": redact(result)[:2000],
            "detail": redact(detail)[:2000],
        }
        with self.db.lock:
            last = self.db.query_one("SELECT hash FROM audit ORDER BY id DESC LIMIT 1")
            prev_hash = last["hash"] if last else GENESIS
            digest = hashlib.sha256((prev_hash + _canonical(record)).encode()).hexdigest()
            return self.db.execute(
                "INSERT INTO audit(ts, actor, source, action, params, decision, result, detail, "
                "prev_hash, hash) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (
                    record["ts"],
                    record["actor"],
                    record["source"],
                    record["action"],
                    record["params"],
                    record["decision"],
                    record["result"],
                    record["detail"],
                    prev_hash,
                    digest,
                ),
            )

    def list(self, limit: int = 100, offset: int = 0) -> list[dict[str, Any]]:
        return self.db.query(
            "SELECT * FROM audit ORDER BY id DESC LIMIT ? OFFSET ?", (min(limit, 500), offset)
        )

    def verify(self) -> bool:
        """Verifies the entire hash chain."""
        prev = GENESIS
        for row in self.db.query("SELECT * FROM audit ORDER BY id ASC"):
            record = {
                k: row[k]
                for k in (
                    "ts",
                    "actor",
                    "source",
                    "action",
                    "params",
                    "decision",
                    "result",
                    "detail",
                )
            }
            expected = hashlib.sha256((prev + _canonical(record)).encode()).hexdigest()
            if row["prev_hash"] != prev or row["hash"] != expected:
                return False
            prev = row["hash"]
        return True
