"""Permissions engine: the only place where actions are executed.

Flow: allowlist check -> parameter check -> emergency stop -> decision
(risk, source, autonomy, rate limit) -> execute, request approval or deny.
Everything ends up in the audit log.
"""

from __future__ import annotations

import json
import secrets
import threading
import time
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from pydantic import ValidationError

from sentinel.audit import AuditLog
from sentinel.db import Database, SettingsStore
from sentinel.permissions.models import (
    CURRENT_REQUEST,
    ActionDenied,
    ActionOutcome,
    ActionSpec,
    Autonomy,
    Decision,
    Outcome,
    RequestContext,
    Risk,
    Severity,
    Source,
)

if TYPE_CHECKING:
    from sentinel.services import Services

EventSink = Callable[[str, dict[str, Any]], None]

AUTONOMOUS_SOURCES = {Source.ASSISTANT, Source.RULE}


def decide(
    spec: ActionSpec,
    source: Source,
    autonomy: Autonomy,
    *,
    tainted: bool = False,
    severity: Severity | None = None,
) -> Decision:
    """Pure decision function without side effects (easy to test)."""
    if source is Source.UNTRUSTED:
        return Decision(Outcome.DENY, "Actions from untrusted data are forbidden")
    if spec.risk is Risk.READ:
        return Decision(Outcome.EXECUTE, "Read access")
    if spec.risk is Risk.FINAL:
        if source is Source.RULE:
            return Decision(Outcome.DENY, "Rules may not trigger final actions")
        return Decision(Outcome.CONFIRM, "Final action - always with confirmation")
    if spec.requires_pin:
        return Decision(Outcome.CONFIRM, "Approval only with PIN")
    if source is Source.AGENT:
        return Decision(Outcome.CONFIRM, "Agents may only suggest")
    if source is Source.EXTERNAL:
        if spec.risk is Risk.LOW and autonomy >= Autonomy.AUTOPILOT and not tainted:
            return Decision(Outcome.EXECUTE, "External agent: low risk", autonomous=True)
        return Decision(Outcome.CONFIRM, "External agent - confirmation needed")
    if source is Source.USER:
        if spec.risk is Risk.HIGH:
            return Decision(Outcome.CONFIRM, "High level - confirmation with preview")
        return Decision(Outcome.EXECUTE, "Direct operation by the user")

    # From here on: ASSISTANT or RULE
    if autonomy is Autonomy.OBSERVER:
        if source is Source.RULE:
            return Decision(Outcome.DENY, "Observer mode: rules only report")
        return Decision(Outcome.CONFIRM, "Observer mode: only with confirmation")
    if tainted:
        return Decision(Outcome.CONFIRM, "Context contains untrusted data")

    if spec.risk is Risk.LOW:
        return Decision(Outcome.EXECUTE, "Low risk", autonomous=True)
    if spec.risk is Risk.REVERSIBLE:
        if autonomy >= Autonomy.AUTOPILOT:
            return Decision(Outcome.EXECUTE, "Autopilot: undoable", autonomous=True)
        return Decision(Outcome.CONFIRM, "Assistant mode: confirmation needed")
    # Risk.HIGH
    if (
        autonomy is Autonomy.GUARDIAN
        and source is Source.RULE
        and severity is Severity.CRITICAL
        and spec.guardian_auto
    ):
        return Decision(Outcome.EXECUTE, "Guardian: critical rule", autonomous=True)
    return Decision(Outcome.CONFIRM, "High level - confirmation needed")


class ActionRegistry:
    """Allowlist of all known actions. Anything unknown is rejected."""

    def __init__(self) -> None:
        self._specs: dict[str, ActionSpec] = {}

    def register(self, spec: ActionSpec) -> None:
        if spec.name in self._specs:
            raise ValueError(f"duplicate action {spec.name}")
        self._specs[spec.name] = spec

    def unregister_prefix(self, prefix: str) -> int:
        """Removes all actions with this prefix (for reloading plugins)."""
        names = [n for n in self._specs if n.startswith(prefix)]
        for name in names:
            del self._specs[name]
        return len(names)

    def get(self, name: str) -> ActionSpec | None:
        return self._specs.get(name)

    def all(self) -> list[ActionSpec]:
        return list(self._specs.values())


class PermissionEngine:
    def __init__(
        self,
        *,
        registry: ActionRegistry,
        db: Database,
        audit: AuditLog,
        store: SettingsStore,
        services: Services,
        default_autonomy: int = 2,
        max_autonomous_per_hour: int = 10,
        undo_window_s: int = 60,
        approval_ttl_s: int = 600,
        on_event: EventSink | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.registry = registry
        self.db = db
        self.audit = audit
        self.store = store
        self.services = services
        self.default_autonomy = default_autonomy
        self.max_autonomous_per_hour = max_autonomous_per_hour
        self.undo_window_s = undo_window_s
        self.approval_ttl_s = approval_ttl_s
        self.on_event = on_event or (lambda kind, data: None)
        self.clock = clock
        self._lock = threading.RLock()

    # ---------------------------------------------------------------- State

    @property
    def autonomy(self) -> Autonomy:
        raw = self.store.get("autonomy", str(self.default_autonomy))
        try:
            return Autonomy(int(raw or self.default_autonomy))
        except ValueError:
            return Autonomy.ASSISTANT

    def set_autonomy(self, level: int, *, actor: str, reason: str = "") -> Autonomy:
        new = Autonomy(level)
        old = self.autonomy
        self.store.set("autonomy", str(int(new)))
        self.audit.append(
            actor=actor,
            source=Source.USER if actor == "user" else "system",
            action="set_autonomy",
            params={"from": int(old), "to": int(new)},
            decision="execute",
            detail=reason,
        )
        self.on_event("autonomy", {"level": int(new), "previous": int(old), "reason": reason})
        return new

    @property
    def halted(self) -> bool:
        return self.store.get("halted", "0") == "1"

    def halt(self, *, actor: str, via: str = "ui") -> None:
        self.store.set("halted", "1")
        self.audit.append(
            actor=actor,
            source="user",
            action="emergency_stop",
            params={"via": via},
            decision="execute",
        )
        self.on_event("halt", {"halted": True, "via": via})

    def resume(self, *, actor: str) -> None:
        self.store.set("halted", "0")
        self.audit.append(
            actor=actor, source="user", action="emergency_release", decision="execute"
        )
        self.on_event("halt", {"halted": False})

    def autonomous_count(self) -> int:
        since = self.clock() - 3600
        row = self.db.query_one(
            "SELECT COUNT(*) AS n FROM autonomous_actions WHERE ts > ?", (since,)
        )
        return int(row["n"]) if row else 0

    # ---------------------------------------------------------------- Request

    def request(
        self,
        name: str,
        raw_params: dict[str, Any] | None,
        *,
        source: Source,
        actor: str,
        reason: str = "",
        tainted: bool = False,
        severity: Severity | None = None,
    ) -> ActionOutcome:
        raw_params = raw_params or {}
        spec = self.registry.get(name)
        if spec is None:
            return self._deny(
                name, raw_params, source, actor, "Unknown action (not on the allowlist)"
            )

        try:
            params = spec.params.model_validate(raw_params)
        except ValidationError as exc:
            msg = "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors())
            return self._deny(name, raw_params, source, actor, f"Invalid parameters: {msg}")

        if self.halted and spec.risk is not Risk.READ:
            return self._deny(
                name, raw_params, source, actor, "Emergency stop active - all actions blocked"
            )
        if self.halted and source in {Source.RULE, Source.AGENT, Source.EXTERNAL}:
            return self._deny(name, raw_params, source, actor, "Emergency stop active")

        try:
            if spec.check:
                spec.check(params, self.services)
        except ActionDenied as exc:
            return self._deny(name, raw_params, source, actor, str(exc))

        with self._lock:
            decision = decide(spec, source, self.autonomy, tainted=tainted, severity=severity)
            if decision.outcome is Outcome.EXECUTE and decision.autonomous:
                decision = self._apply_rate_limit(decision)
                if decision.outcome is Outcome.EXECUTE:
                    self.db.execute(
                        "INSERT INTO autonomous_actions(ts, action) VALUES(?, ?)",
                        (self.clock(), name),
                    )

        if decision.outcome is Outcome.DENY:
            return self._deny(name, raw_params, source, actor, decision.reason)
        if decision.outcome is Outcome.CONFIRM:
            return self._create_approval(spec, params, source, actor, reason, decision, tainted)
        return self._execute(
            spec,
            params,
            source,
            actor,
            decision.reason,
            autonomous=decision.autonomous,
            tainted=tainted,
        )

    def _apply_rate_limit(self, decision: Decision) -> Decision:
        if self.autonomous_count() < self.max_autonomous_per_hour:
            return decision
        if self.autonomy > Autonomy.ASSISTANT:
            self.set_autonomy(
                Autonomy.ASSISTANT,
                actor="system",
                reason=f"More than {self.max_autonomous_per_hour} autonomous actions per hour",
            )
            self.on_event(
                "warning",
                {
                    "title": "Autonomy lowered",
                    "message": "Too many autonomous actions in one hour - level 1 active.",
                },
            )
        return Decision(Outcome.CONFIRM, "Rate limit for autonomous actions reached")

    # ---------------------------------------------------------------- Execution

    def _execute(
        self,
        spec: ActionSpec,
        params: Any,
        source: Source,
        actor: str,
        reason: str,
        *,
        autonomous: bool,
        approval_id: str | None = None,
        tainted: bool = False,
    ) -> ActionOutcome:
        param_dict = params.model_dump(mode="json")
        context = RequestContext(
            source=Source.USER if approval_id is not None else source, actor=actor, tainted=tainted
        )
        token = CURRENT_REQUEST.set(context)
        try:
            result = spec.execute(params, self.services)
        except ActionDenied as exc:
            return self._deny(spec.name, param_dict, source, actor, str(exc))
        except Exception as exc:
            self.audit.append(
                actor=actor,
                source=source,
                action=spec.name,
                params=param_dict,
                decision="execute",
                result="failed",
                detail=f"{type(exc).__name__}: {exc}",
            )
            return ActionOutcome("failed", spec.name, f"Error: {exc}")
        finally:
            CURRENT_REQUEST.reset(token)

        undo_id = None
        if result.undo is not None and spec.undo is not None:
            undo_id = self._store_undo(spec, result)

        self.audit.append(
            actor=actor,
            source=source,
            action=spec.name,
            params=param_dict,
            decision="execute" if approval_id is None else "approved",
            result="ok",
            detail=f"{reason}; {result.message}",
        )
        if spec.risk is not Risk.READ:
            self.on_event(
                "action",
                {
                    "action": spec.name,
                    "title": spec.title,
                    "message": result.message,
                    "autonomous": autonomous,
                    "undo_id": undo_id,
                    "undo_expires": self.clock() + self._undo_window(spec) if undo_id else None,
                },
            )
        return ActionOutcome(
            "executed",
            spec.name,
            result.message,
            data=result.data,
            undo_id=undo_id,
            untrusted=result.untrusted or spec.untrusted_output,
        )

    def _deny(
        self, name: str, params: dict[str, Any], source: Source, actor: str, reason: str
    ) -> ActionOutcome:
        self.audit.append(
            actor=actor, source=source, action=name, params=params, decision="deny", detail=reason
        )
        return ActionOutcome("denied", name, reason)

    # ---------------------------------------------------------------- Approvals

    def _create_approval(
        self,
        spec: ActionSpec,
        params: Any,
        source: Source,
        actor: str,
        reason: str,
        decision: Decision,
        tainted: bool,
    ) -> ActionOutcome:
        approval_id = secrets.token_hex(8)
        now = self.clock()
        try:
            preview = spec.preview(params, self.services) if spec.preview else spec.title
        except Exception as exc:
            preview = f"{spec.title} (preview not available: {exc})"
        param_dict = params.model_dump(mode="json")
        self.db.execute(
            "INSERT INTO approvals(id, created, expires, action, params, source, actor, risk, "
            "reason, preview, tainted) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (
                approval_id,
                now,
                now + self.approval_ttl_s,
                spec.name,
                json.dumps(param_dict),
                source,
                actor,
                spec.risk,
                reason or decision.reason,
                preview,
                int(tainted),
            ),
        )
        self.audit.append(
            actor=actor,
            source=source,
            action=spec.name,
            params=param_dict,
            decision="confirm",
            detail=decision.reason,
        )
        self.on_event(
            "approval",
            {
                "id": approval_id,
                "action": spec.name,
                "title": spec.title,
                "preview": preview,
                "risk": spec.risk,
                "reason": reason or decision.reason,
                "tainted": tainted,
            },
        )
        return ActionOutcome(
            "pending", spec.name, f"Waiting for confirmation: {preview}", approval_id=approval_id
        )

    def list_approvals(self, status: str = "pending") -> list[dict[str, Any]]:
        self._expire_approvals()
        rows = self.db.query(
            "SELECT * FROM approvals WHERE status = ? ORDER BY created DESC LIMIT 100", (status,)
        )
        for row in rows:
            row["params"] = json.loads(row["params"])
            row["tainted"] = bool(row["tainted"])
            spec = self.registry.get(row["action"])
            row["requires_pin"] = bool(spec and spec.requires_pin)
        return rows

    def _expire_approvals(self) -> None:
        self.db.execute(
            "UPDATE approvals SET status = 'expired' WHERE status = 'pending' AND expires < ?",
            (self.clock(),),
        )

    def approve(
        self,
        approval_id: str,
        *,
        actor: str = "user",
        pin: str | None = None,
        pin_verified: bool = False,
    ) -> ActionOutcome:
        """Only reachable via the UI API and the paired phone - never via Jarvis/agents.

        `pin_verified` is only set by the phone module, after it has verified the
        signature of the paired phone that is bound to the PIN.
        """
        self._expire_approvals()
        with self._lock:
            row = self.db.query_one(
                "SELECT * FROM approvals WHERE id = ? AND status = 'pending'", (approval_id,)
            )
            if row is None:
                return ActionOutcome("denied", "approve", "Approval not found or expired")
            if self.halted:
                return ActionOutcome(
                    "denied", row["action"], "Emergency stop active - approval blocked"
                )
            spec = self.registry.get(row["action"])
            if spec is None:
                return ActionOutcome("denied", row["action"], "Action is no longer registered")
            if spec.requires_pin and not pin_verified:
                pins = getattr(self.services, "pin", None)
                if pins is None or not pins.verify(pin, actor=actor):
                    # Approval stays open so another attempt is possible
                    return ActionOutcome("denied", spec.name, "PIN wrong, missing or locked")
            self.db.execute(
                "UPDATE approvals SET status = 'approved', decided = ? WHERE id = ?",
                (self.clock(), approval_id),
            )
        try:
            params = spec.params.model_validate(json.loads(row["params"]))
            if spec.check:
                spec.check(params, self.services)
        except (ValidationError, ActionDenied) as exc:
            return self._deny(
                spec.name, json.loads(row["params"]), Source(row["source"]), actor, str(exc)
            )
        return self._execute(
            spec,
            params,
            Source(row["source"]),
            actor,
            "Confirmed by the user",
            autonomous=False,
            approval_id=approval_id,
        )

    def reject(self, approval_id: str, *, actor: str = "user") -> bool:
        row = self.db.query_one(
            "SELECT * FROM approvals WHERE id = ? AND status = 'pending'", (approval_id,)
        )
        if row is None:
            return False
        self.db.execute(
            "UPDATE approvals SET status = 'rejected', decided = ? WHERE id = ?",
            (self.clock(), approval_id),
        )
        self.audit.append(
            actor=actor,
            source="user",
            action=row["action"],
            params=json.loads(row["params"]),
            decision="rejected",
        )
        return True

    # ---------------------------------------------------------------- Undo

    def _undo_window(self, spec: ActionSpec) -> int:
        return spec.undo_window_s or self.undo_window_s

    def _store_undo(self, spec: ActionSpec, result: Any) -> str:
        undo_id = secrets.token_hex(8)
        now = self.clock()
        self.db.execute(
            "INSERT INTO undo(id, created, expires, action, summary, data) VALUES(?,?,?,?,?,?)",
            (
                undo_id,
                now,
                now + self._undo_window(spec),
                spec.name,
                result.message,
                json.dumps(result.undo, default=str),
            ),
        )
        return undo_id

    def list_undo(self) -> list[dict[str, Any]]:
        return self.db.query(
            "SELECT id, created, expires, action, summary FROM undo "
            "WHERE status = 'open' AND expires > ? ORDER BY created DESC",
            (self.clock(),),
        )

    def undo(self, undo_id: str, *, actor: str = "user") -> ActionOutcome:
        """Undoes an action within the time window (also allowed during an emergency stop)."""
        with self._lock:
            row = self.db.query_one(
                "SELECT * FROM undo WHERE id = ? AND status = 'open'", (undo_id,)
            )
            if row is None:
                return ActionOutcome("denied", "undo", "Nothing found to undo")
            if row["expires"] < self.clock():
                self.db.execute("UPDATE undo SET status = 'expired' WHERE id = ?", (undo_id,))
                return ActionOutcome("denied", row["action"], "Undo time window expired")
            self.db.execute("UPDATE undo SET status = 'done' WHERE id = ?", (undo_id,))
        spec = self.registry.get(row["action"])
        if spec is None or spec.undo is None:
            return ActionOutcome("denied", row["action"], "Undo not supported")
        try:
            message = spec.undo(json.loads(row["data"]), self.services)
        except Exception as exc:
            self.audit.append(
                actor=actor,
                source="user",
                action=f"undo:{spec.name}",
                decision="execute",
                result="failed",
                detail=str(exc),
            )
            return ActionOutcome("failed", spec.name, f"Undo failed: {exc}")
        self.audit.append(
            actor=actor,
            source="user",
            action=f"undo:{spec.name}",
            decision="execute",
            result="ok",
            detail=message,
        )
        self.on_event("undo", {"action": spec.name, "message": message})
        return ActionOutcome("executed", f"undo:{spec.name}", message)
