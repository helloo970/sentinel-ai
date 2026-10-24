"""Hand for the brain: look up, record, forget (via the permissions engine)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import Field

from sentinel.brain.privacy import blocked_reason
from sentinel.brain.store import BrainRejected
from sentinel.permissions.models import (
    CURRENT_REQUEST,
    ActionDenied,
    ActionResult,
    ActionSpec,
    Risk,
    Source,
    StrictParams,
)

if TYPE_CHECKING:
    from sentinel.permissions import ActionRegistry
    from sentinel.services import Services


class LookupParams(StrictParams):
    query: str = Field(min_length=2, max_length=120, description="Topic or name")


class RememberParams(StrictParams):
    label: str = Field(min_length=2, max_length=80, description="Short name of the thought")
    summary: str = Field(default="", max_length=300, description="One sentence about it")
    category: str = Field(default="ich", pattern=r"^[a-z][a-z0-9_]{2,19}$")
    related: list[str] = Field(default_factory=list, max_length=5)


class ForgetParams(StrictParams):
    node_id: int = Field(ge=1)


def _lookup(p: LookupParams, svc: Services) -> ActionResult:
    rows = svc.brain.store.search(p.query, limit=8)
    svc.brain.relevant(p.query)
    data = [
        {
            "id": r["id"],
            "label": r["label"],
            "summary": r["summary"],
            "category": r["category"],
            "origin": r["origin"],
        }
        for r in rows
    ]
    # Knowledge from files/sensor data is foreign -> result is treated as untrusted
    untrusted = any(r["origin"] in ("file", "sensor") for r in rows)
    return ActionResult(f"{len(data)} thoughts found", data, untrusted=untrusted)


def _check_remember(p: RememberParams, svc: Services) -> None:
    reason = blocked_reason(f"{p.label} {p.summary}")
    if reason:
        raise ActionDenied(reason)


def _remember(p: RememberParams, svc: Services) -> ActionResult:
    store = svc.brain.store
    request = CURRENT_REQUEST.get()
    external = request is not None and request.source is Source.EXTERNAL
    try:
        node = store.add(
            p.label,
            category=p.category,
            summary=p.summary,
            # From Hermes: mark as AI knowledge, not as the user's own statement
            origin="ai" if external else "user",
            source="hermes" if external else "jarvis",
        )
    except BrainRejected as exc:
        raise ActionDenied(str(exc)) from exc
    for name in p.related:
        other = store.find(name)
        if other:
            store.link(int(node["id"]), int(other["id"]))
    svc.brain.changed()
    svc.brain.focus(int(node["id"]))
    return ActionResult(f"Saved to the brain: {node['label']}", {"id": node["id"]})


def _check_forget(p: ForgetParams, svc: Services) -> None:
    node = svc.brain.store.get(p.node_id)
    if node is None:
        raise ActionDenied("That thought does not exist")
    if not svc.brain.forgettable(node):
        raise ActionDenied("Belongs to SENTINEL itself and comes back automatically")


def _forget(p: ForgetParams, svc: Services) -> ActionResult:
    node = svc.brain.store.get(p.node_id)
    if node is None or not svc.brain.delete_from_ui(p.node_id):
        raise ActionDenied("That thought does not exist")
    return ActionResult(f"Forgotten: {node['label']}")


def _forget_preview(p: ForgetParams, svc: Services) -> str:
    node = svc.brain.store.get(p.node_id)
    return f"Forget in the brain: {node['label'] if node else p.node_id}"


def register(registry: ActionRegistry) -> None:
    specs = [
        ActionSpec(
            "brain_lookup",
            "Look up in the brain",
            "Searches knowledge in the brain (about the user, projects, devices, "
            "appointments, everything from SENTINEL).",
            Risk.READ,
            LookupParams,
            _lookup,
        ),
        ActionSpec(
            "brain_remember",
            "Remember in the brain",
            "Records a thought in the brain, only when the user says so directly. Categories: "
            "ich, projekte, geraete, schule, interessen, ziele, aufgaben, stil, wissen.",
            Risk.LOW,
            RememberParams,
            _remember,
            check=_check_remember,
            preview=lambda p, s: f"Remember: {p.label}",
        ),
        ActionSpec(
            "brain_forget",
            "Delete from the brain",
            "Deletes a thought (node_id from brain_lookup). Always with confirmation.",
            Risk.FINAL,
            ForgetParams,
            _forget,
            check=_check_forget,
            preview=_forget_preview,
        ),
    ]
    for spec in specs:
        registry.register(spec)
