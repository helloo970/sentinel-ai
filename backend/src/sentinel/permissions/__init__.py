"""Permissions engine: allowlist, risk levels, autonomy, emergency stop, audit."""

from sentinel.permissions.engine import ActionRegistry, PermissionEngine, decide
from sentinel.permissions.models import (
    ActionDenied,
    ActionOutcome,
    ActionResult,
    ActionSpec,
    Autonomy,
    Decision,
    NoParams,
    Outcome,
    Risk,
    Severity,
    Source,
    StrictParams,
)

__all__ = [
    "ActionDenied",
    "ActionOutcome",
    "ActionRegistry",
    "ActionResult",
    "ActionSpec",
    "Autonomy",
    "Decision",
    "NoParams",
    "Outcome",
    "PermissionEngine",
    "Risk",
    "Severity",
    "Source",
    "StrictParams",
    "decide",
]
