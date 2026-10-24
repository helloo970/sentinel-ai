"""Storage of alerts and reaction to new findings."""

from __future__ import annotations

import json
import logging
import time
from typing import TYPE_CHECKING, Any

from sentinel.monitor.rules import Finding
from sentinel.permissions.models import Severity, Source

if TYPE_CHECKING:
    from sentinel.services import Services

log = logging.getLogger(__name__)


class AlertStore:
    def __init__(self, svc: Services) -> None:
        self.svc = svc
        self.db = svc.db

    def add(self, finding: Finding) -> int | None:
        """Stores a finding. Returns None if it is already known."""
        with self.db.lock:
            exists = self.db.query_one(
                "SELECT id FROM alerts WHERE fingerprint = ?", (finding.fingerprint,)
            )
            if exists:
                return None
            return self.db.execute(
                "INSERT INTO alerts(ts, rule, severity, title, detail, fingerprint) "
                "VALUES(?,?,?,?,?,?)",
                (
                    time.time(),
                    finding.rule,
                    finding.severity,
                    finding.title,
                    json.dumps(finding.detail, ensure_ascii=False, default=str),
                    finding.fingerprint,
                ),
            )

    def handle(self, finding: Finding) -> int | None:
        """Store a new finding, report it and, if applicable, request the fixed rule response."""
        alert_id = self.add(finding)
        if alert_id is None:
            return None
        payload = {
            "id": alert_id,
            "rule": finding.rule,
            "severity": finding.severity,
            "title": finding.title,
        }
        self.svc.bus.publish("alert", payload)
        if finding.notify and finding.severity in (Severity.WARN, Severity.CRITICAL):
            self.svc.notifier.notify(
                "Security warning" if finding.severity is Severity.WARN else "Critical alert",
                finding.title,
                severity=finding.severity,
                speak=finding.severity is Severity.CRITICAL,
            )
        if finding.response is not None:
            action, params = finding.response
            outcome = self.svc.engine.request(
                action,
                params,
                source=Source.RULE,
                actor=f"rule:{finding.rule}",
                reason=finding.title,
                severity=finding.severity,
            )
            self.db.execute(
                "UPDATE alerts SET response = ? WHERE id = ?",
                (
                    json.dumps(
                        {
                            "action": action,
                            "status": outcome.status,
                            "message": outcome.message,
                            "undo_id": outcome.undo_id,
                            "approval_id": outcome.approval_id,
                        },
                        ensure_ascii=False,
                    ),
                    alert_id,
                ),
            )
        return alert_id

    def list(self, status: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        if status:
            rows = self.db.query(
                "SELECT * FROM alerts WHERE status = ? ORDER BY ts DESC LIMIT ?", (status, limit)
            )
        else:
            rows = self.db.query("SELECT * FROM alerts ORDER BY ts DESC LIMIT ?", (limit,))
        for row in rows:
            row["detail"] = json.loads(row["detail"])
            row["response"] = json.loads(row["response"]) if row["response"] else None
        return rows

    def ack(self, alert_id: int) -> bool:
        return bool(self.db.execute("UPDATE alerts SET status = 'ack' WHERE id = ?", (alert_id,)))

    def ack_all(self) -> None:
        self.db.execute("UPDATE alerts SET status = 'ack' WHERE status = 'new'")

    def counts(self) -> dict[str, int]:
        rows = self.db.query(
            "SELECT severity, COUNT(*) AS n FROM alerts WHERE status = 'new' GROUP BY severity"
        )
        counts = {"info": 0, "warn": 0, "critical": 0}
        for row in rows:
            counts[row["severity"]] = row["n"]
        return counts
