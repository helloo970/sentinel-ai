"""Open sites, searches and app areas (Jarvis' "hands" for the browser and the app)."""

from __future__ import annotations

import webbrowser
from typing import TYPE_CHECKING, Literal

from pydantic import Field

from sentinel.automation import web
from sentinel.permissions.models import ActionDenied, ActionResult, ActionSpec, Risk, StrictParams
from sentinel.secrets_filter import contains_secret

if TYPE_CHECKING:
    from sentinel.permissions import ActionRegistry
    from sentinel.services import Services

ViewName = Literal[
    "dashboard",
    "jarvis",
    "brain",
    "markets",
    "kpis",
    "overview",
    "world",
    "security",
    "network",
    "automation",
    "shop",
    "agents",
    "memory",
    "audit",
    "settings",
]
VIEW_TITLES = {
    "dashboard": "Jarvis",
    "jarvis": "Jarvis",
    "brain": "Brain",
    "markets": "Markets",
    "kpis": "KPIs",
    "overview": "Overview",
    "world": "World situation",
    "security": "Security Center",
    "network": "Network",
    "automation": "Automation",
    "shop": "Orders",
    "agents": "Agents",
    "memory": "Memory",
    "audit": "Audit log",
    "settings": "Settings",
}


class SiteParams(StrictParams):
    name: str = Field(min_length=2, max_length=40, description="e.g. gmail, youtube, maps")


class SearchParams(StrictParams):
    query: str = Field(min_length=1, max_length=200)
    engine: Literal["google", "youtube", "maps", "wikipedia"] = "google"


class ViewParams(StrictParams):
    view: ViewName
    tab: str | None = Field(default=None, max_length=30, pattern=r"^[a-z_]{2,30}$")


def _open(url: str, svc: Services, title: str, *, login: bool = False) -> None:
    """In the app (own window) if it is running; pages with a login in the default browser."""
    if svc.config.web_in_app and svc.bus.listeners and not login:
        svc.bus.publish("open_web", {"url": url, "title": title})
        return
    if not webbrowser.open(url, new=2):
        raise ActionDenied("Could not open the browser")


def _check_site(p: SiteParams, svc: Services) -> None:
    if web.resolve_site(p.name) is None:
        raise ActionDenied(f"I don't know '{p.name}'. Known: {', '.join(web.SITES)}")


def _open_site(p: SiteParams, svc: Services) -> ActionResult:
    key = web.resolve_site(p.name)
    if key is None:
        raise ActionDenied("Unknown site")
    title = web.SITES[key][0]
    _open(web.site_url(key, svc.config.config_dir), svc, title, login=key in web.LOGIN_SITES)
    return ActionResult(f"{title} opened", {"site": key})


def _check_search(p: SearchParams, svc: Services) -> None:
    if contains_secret(p.query):
        raise ActionDenied("Search terms must not contain secrets")


def _search(p: SearchParams, svc: Services) -> ActionResult:
    _open(web.search_url(p.engine, p.query), svc, f"{web.SEARCH[p.engine][0]}: {p.query}")
    return ActionResult(f"Search on {web.SEARCH[p.engine][0]}: {p.query}", {"engine": p.engine})


def _open_view(p: ViewParams, svc: Services) -> ActionResult:
    svc.bus.publish("navigate", {"view": p.view, "tab": p.tab, "title": VIEW_TITLES[p.view]})
    return ActionResult(f"{VIEW_TITLES[p.view]} opened", {"view": p.view, "tab": p.tab})


def register(registry: ActionRegistry) -> None:
    specs = [
        ActionSpec(
            "open_site",
            "Open site",
            "Opens a known site in the browser: " + ", ".join(web.SITES) + ".",
            Risk.LOW,
            SiteParams,
            _open_site,
            check=_check_site,
            preview=lambda p, s: f"Open {p.name} in the browser",
        ),
        ActionSpec(
            "web_search",
            "Search the web",
            "Opens a search on Google, YouTube, Google Maps or Wikipedia in the browser.",
            Risk.LOW,
            SearchParams,
            _search,
            check=_check_search,
            preview=lambda p, s: f"Search on {p.engine}: {p.query}",
        ),
        ActionSpec(
            "open_view",
            "Open app area",
            "Opens an area of SENTINEL (e.g. security, network, settings) as a tab.",
            Risk.READ,
            ViewParams,
            _open_view,
        ),
    ]
    for spec in specs:
        registry.register(spec)
