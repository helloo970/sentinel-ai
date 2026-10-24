"""Data types of the permissions engine."""

from __future__ import annotations

from collections.abc import Callable
from contextvars import ContextVar
from dataclasses import dataclass, field
from enum import IntEnum, StrEnum
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict

if TYPE_CHECKING:
    from sentinel.services import Services


class Risk(StrEnum):
    READ = "read"  # read only
    LOW = "low"  # e.g. start a program, timer, system control
    REVERSIBLE = "reversible"  # undoable: quarantine, move files, port check
    HIGH = "high"  # not undoable: end a process
    FINAL = "final"  # final: delete, system settings - always confirmation


class Source(StrEnum):
    USER = "user"  # direct operation in the UI
    ASSISTANT = "assistant"  # suggestion from Jarvis in response to a direct command
    RULE = "rule"  # fixed detection rule
    AGENT = "agent"  # agent - may only suggest
    # External agent (Hermes): low risk runs automatically in autopilot, otherwise a suggestion
    EXTERNAL = "external"
    UNTRUSTED = "untrusted"  # from foreign content - never execute


class Autonomy(IntEnum):
    OBSERVER = 0
    ASSISTANT = 1
    AUTOPILOT = 2
    GUARDIAN = 3


class Outcome(StrEnum):
    EXECUTE = "execute"
    CONFIRM = "confirm"
    DENY = "deny"


class Severity(StrEnum):
    INFO = "info"
    WARN = "warn"
    CRITICAL = "critical"


class ActionDenied(Exception):  # noqa: N818 - domain-specific name
    """Parameters are valid, but the action is not permitted."""


class StrictParams(BaseModel):
    """Base for action parameters: unknown fields are rejected."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


class NoParams(StrictParams):
    pass


@dataclass
class ActionResult:
    message: str
    data: Any = None
    undo: dict[str, Any] | None = None
    untrusted: bool = False


@dataclass(frozen=True)
class Decision:
    outcome: Outcome
    reason: str
    autonomous: bool = False


@dataclass(frozen=True)
class ActionSpec:
    name: str
    title: str
    description: str
    risk: Risk
    params: type[StrictParams]
    execute: Callable[[Any, Services], ActionResult]
    undo: Callable[[dict[str, Any], Services], str] | None = None
    preview: Callable[[Any, Services], str] | None = None
    check: Callable[[Any, Services], None] | None = None
    untrusted_output: bool = False
    llm_exposed: bool = True
    guardian_auto: bool = False
    # Approval only with the owner PIN (never executed automatically)
    requires_pin: bool = False
    # Own undo time window (e.g. weekly tidy-up), otherwise the default
    undo_window_s: int | None = None


@dataclass(frozen=True)
class RequestContext:
    """Who triggered the action currently running (e.g. for routine steps)."""

    source: Source
    actor: str
    tainted: bool = False


CURRENT_REQUEST: ContextVar[RequestContext | None] = ContextVar("current_request", default=None)


@dataclass
class ActionOutcome:
    status: str  # executed | pending | denied | failed
    action: str
    message: str
    data: Any = None
    approval_id: str | None = None
    undo_id: str | None = None
    untrusted: bool = False
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "action": self.action,
            "message": self.message,
            "data": self.data,
            "approval_id": self.approval_id,
            "undo_id": self.undo_id,
            "untrusted": self.untrusted,
        }
