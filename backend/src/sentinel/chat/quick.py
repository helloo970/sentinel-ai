"""Quick commands without a language model: fixed patterns for common commands (also offline).

Only the user's own messages or voice commands are matched. The action runs through the
permission engine like any other. External data never ends up here.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

_PREFIX = re.compile(r"^(?:(?:hey|ok(?:ay)?)\s+)?(?:jarvis|sentinel)[\s,:]*", re.I)
_PLEASE = re.compile(r"^please[\s,]+|[\s,]+please$", re.I)
_TRAILING = re.compile(r"[\s.!?]+$")

# Spoken mode names → mode keys ("fokus" is the stored key of the focus mode)
_MODE_WORDS = {"gaming": "gaming", "gamer": "gaming", "game": "gaming", "focus": "fokus"}
_MODE_NAME = r"(?:gaming|gamer|game|focus)"
_MODE_ON = re.compile(
    r"^(?:(?:start|enable|activate|turn\s+on|switch\s+on)\s+(?:the\s+)?)?"
    r"(gaming|gamer|game|focus)[\s-]?mode"
    r"(?:\s+(?:for\s+)?(\d{1,3})\s*min(?:ute)?s?)?(?:\s+(?:on|now|start))?$",
    re.I,
)
_MODE_OFF = re.compile(
    rf"^(?:{_MODE_NAME}[\s-]?)?mode\s+(?:off|stop|end)$"
    rf"|^(?:stop|end|exit|quit|disable|turn\s+off|switch\s+off)\s+(?:the\s+)?"
    rf"(?:{_MODE_NAME}[\s-]?)?mode$"
    rf"|^(?:turn|switch)\s+(?:the\s+)?(?:{_MODE_NAME}[\s-]?)?mode\s+off$",
    re.I,
)
_LIST = r"(?:the|my|our)\s+(?:shopping\s+|grocery\s+)?list"
_SHOP_ADD = re.compile(rf"^(?:add|put|write)\s+(.{{1,120}}?)\s+(?:to|on)\s+{_LIST}$", re.I)
_SHOP_REMOVE = re.compile(
    rf"^(?:remove|delete|take|cross|strike)\s+(.{{1,120}}?)\s+(?:off|from)\s+{_LIST}$", re.I
)
_SHOP_LIST = re.compile(
    rf"^(?:what(?:['’]s|\s+is)\s+on\s+{_LIST}"
    rf"|(?:show|read)(?:\s+out)?\s+(?:me\s+)?{_LIST}(?:\s+to\s+me)?|shopping\s+list)$",
    re.I,
)

# App areas: spoken names → (view, optional tab)
_VIEWS: dict[str, tuple[str, str | None]] = {
    "dashboard": ("dashboard", None),
    "home": ("dashboard", None),
    "start page": ("dashboard", None),
    "jarvis": ("dashboard", None),
    "overview": ("overview", None),
    "security center": ("security", None),
    "security": ("security", None),
    "alarms": ("security", None),
    "alerts": ("security", None),
    "alarm-test": ("security", "tests"),
    "alarm test": ("security", "tests"),
    "alert test": ("security", "tests"),
    "alert-test": ("security", "tests"),
    "alerts test": ("security", "tests"),
    "network": ("network", None),
    "home network": ("network", None),
    "server": ("network", None),
    "automation": ("automation", None),
    "modes": ("automation", None),
    "orders": ("shop", None),
    "shopping": ("shop", None),
    "shop": ("shop", None),
    "agents": ("agents", None),
    "memory": ("memory", None),
    "audit-log": ("audit", None),
    "audit log": ("audit", None),
    "log": ("audit", None),
    "settings": ("settings", None),
    "world situation": ("world", None),
    "world": ("world", None),
    "markets": ("markets", None),
    "stock market": ("markets", None),
    "stocks": ("markets", None),
    "prices": ("markets", None),
    "crypto": ("markets", None),
    "numbers": ("kpis", None),
    "kpis": ("kpis", None),
    "key figures": ("kpis", None),
    "statistics": ("kpis", None),
    "stats": ("kpis", None),
    "world map": ("world", None),
    "camera": ("camera", None),
    "cameras": ("camera", None),
    "security camera": ("camera", None),
    "security cameras": ("camera", None),
    "cctv": ("camera", None),
    "news": ("world", None),
}
_OPEN = re.compile(
    r"^(?:open|show(?:\s+me)?|go\s+to|take\s+me\s+to|switch\s+to)"
    r"\s+(?:the\s+|my\s+)?(.{2,40})$",
    re.I,
)
_LAUNCH = re.compile(
    r"^(?:start|launch|open)\s+(?:the\s+program\s+)?([a-z0-9][a-z0-9_\-]{1,31})$", re.I
)
_ENGINES = r"(google|youtube|maps|wikipedia)"
_SEARCH_ON = re.compile(
    rf"^(?:search|look\s+up)\s+(?:for\s+)?(.{{1,150}}?)\s+(?:on|in)\s+{_ENGINES}$", re.I
)
_SEARCH = re.compile(
    rf"^(?:search|google|look\s+up)\s+(?:(?:(?:on|in)\s+)?{_ENGINES}\s+(?:for\s+)?)?"
    r"(?:for\s+)?(.{1,150})$",
    re.I,
)
_HOME = re.compile(
    r"^(?:back|go\s+back|close(?:\s+(?:this|that|it|everything|the\s+view))?"
    r"|home|start\s+page|back\s+to\s+(?:you|jarvis|home))$",
    re.I,
)
_BRIEFING = re.compile(
    r"^(?:(?:start\s+)?(?:the\s+|my\s+)?(?:morning\s+|daily\s+)?briefing(?:\s+now)?"
    r"|status\s+report|brief\s+me)$",
    re.I,
)
_WORLD_TOUR = re.compile(
    r"^(?:what(?:['’]s|\s+is)\s+(?:going\s+on|happening)\s+in\s+the\s+world"
    r"|world\s+tour|start\s+the\s+world\s+tour|news\s+tour)$",
    re.I,
)
_PLAY = re.compile(r"^(?:play|show(?:\s+me)?)\s+(.{1,150}?)\s+on\s+youtube$", re.I)


def _open_command(target: str) -> QuickCommand | None:
    from sentinel.automation import web

    key = target.strip().lower()
    if key in _VIEWS:
        view, tab = _VIEWS[key]
        params: dict[str, Any] = {"view": view}
        if tab:
            params["tab"] = tab
        return QuickCommand("open_view", params)
    site = web.resolve_site(key)
    if site:
        return QuickCommand("open_site", {"name": site})
    return None


@dataclass(frozen=True)
class QuickCommand:
    action: str
    params: dict[str, Any] = field(default_factory=dict)


def normalize(text: str) -> str:
    text = _TRAILING.sub("", _PREFIX.sub("", text.strip()))
    return _PLEASE.sub("", text).strip()


def match(text: str) -> QuickCommand | None:
    norm = normalize(text)
    if not norm or len(norm) > 120:
        return None
    if _HOME.match(norm):
        return QuickCommand("open_view", {"view": "dashboard"})
    if _BRIEFING.match(norm):
        return QuickCommand("open_view", {"view": "world", "tab": "briefing"})
    if _WORLD_TOUR.match(norm):
        return QuickCommand("open_view", {"view": "world", "tab": "tour"})
    if _SHOP_LIST.match(norm):
        return QuickCommand("list_shopping")
    found = _SHOP_ADD.match(norm)
    if found:
        return QuickCommand("add_shopping_item", {"text": found.group(1)})
    found = _SHOP_REMOVE.match(norm)
    if found:
        return QuickCommand("remove_shopping_item", {"text": found.group(1)})
    found = _PLAY.match(norm)
    if found:
        return QuickCommand("web_search", {"query": found.group(1), "engine": "youtube"})
    if _MODE_OFF.match(norm) or _MODE_ON.match(norm):
        return _mode_command(norm)
    found = _OPEN.match(norm)
    if found:
        command = _open_command(found.group(1))
        if command:
            return command
    found = _LAUNCH.match(norm)
    if found:
        return QuickCommand("launch_program", {"name": found.group(1).lower()})
    found = _SEARCH_ON.match(norm)
    if found:
        return QuickCommand(
            "web_search", {"query": found.group(1), "engine": found.group(2).lower()}
        )
    found = _SEARCH.match(norm)
    if found:
        engine = (found.group(1) or "google").lower()
        return QuickCommand("web_search", {"query": found.group(2), "engine": engine})
    return None


def _mode_command(norm: str) -> QuickCommand | None:
    if _MODE_OFF.match(norm):
        return QuickCommand("stop_mode")
    found = _MODE_ON.match(norm)
    if found:
        params: dict[str, Any] = {"name": _MODE_WORDS[found.group(1).lower()]}
        if found.group(2):
            params["minutes"] = int(found.group(2))
        return QuickCommand("start_mode", params)
    return None


def reply(command: QuickCommand, outcome: Any) -> str:
    """Short answer to a quick command (result from the engine)."""
    if outcome.status == "executed":
        if command.action == "open_view":
            tab = command.params.get("tab")
            if tab == "briefing":
                return (
                    "Starting the briefing: first the world, then the markets, then your numbers."
                )
            if tab == "tour":
                return "Let me show you what's going on in the world."
            if command.params.get("view") == "dashboard":
                return "Okay."
        return str(outcome.message)
    if outcome.status == "pending":
        return "I need your confirmation for that – check the approvals."
    return f"That didn't work: {outcome.message}"
