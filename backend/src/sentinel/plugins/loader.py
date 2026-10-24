"""Declarative plugins: data only (routines, news feeds), no executable code.

Layout: <data_dir>/plugins/<name>/plugin.json
  {"name": "...", "version": "1.0.0", "description": "...",
   "routines": {...like routines.json...}, "feeds": [{"region": "de", "name": "...", "url": "https://..."}]}

New plugins are disabled until the user enables them in the UI.
"""

from __future__ import annotations

import json
import logging
import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from sentinel.automation.routines import Routine
    from sentinel.services import Services

log = logging.getLogger(__name__)

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_\-]{0,31}$")
MAX_BYTES = 200_000
MAX_FEEDS = 10


class PluginManager:
    def __init__(self, svc: Services) -> None:
        self.svc = svc
        self.dir = svc.config.plugins_dir

    def _enabled(self) -> set[str]:
        raw = self.svc.store.get("plugins_enabled", "[]") or "[]"
        try:
            return set(json.loads(raw))
        except json.JSONDecodeError:
            return set()

    def set_enabled(self, name: str, enabled: bool) -> None:
        if name not in {p["name"] for p in self.list()}:
            raise KeyError(name)
        names = self._enabled()
        if enabled:
            names.add(name)
        else:
            names.discard(name)
        self.svc.store.set("plugins_enabled", json.dumps(sorted(names)))
        self.svc.audit.append(
            actor="user",
            source="user",
            action="plugin_toggle",
            params={"name": name, "enabled": enabled},
            decision="execute",
        )

    def _load(self) -> list[dict[str, Any]]:
        plugins = []
        if not self.dir.is_dir():
            return plugins
        for folder in sorted(self.dir.iterdir()):
            manifest = folder / "plugin.json"
            if not folder.is_dir() or not NAME_RE.match(folder.name) or not manifest.is_file():
                continue
            try:
                if manifest.stat().st_size > MAX_BYTES:
                    raise ValueError("plugin.json too large")
                data = json.loads(manifest.read_text(encoding="utf-8"))
                if not isinstance(data, dict):
                    raise ValueError("plugin.json must be an object")
            except (OSError, ValueError) as exc:
                log.warning("plugin %s invalid: %s", folder.name, exc)
                plugins.append({"name": folder.name, "error": str(exc), "data": {}})
                continue
            plugins.append({"name": folder.name, "error": None, "data": data})
        return plugins

    def list(self) -> list[dict[str, Any]]:
        enabled = self._enabled()
        result = []
        for plugin in self._load():
            data = plugin["data"]
            result.append(
                {
                    "name": plugin["name"],
                    "version": str(data.get("version", "?")),
                    "description": str(data.get("description", ""))[:300],
                    "routines": sorted((data.get("routines") or {}).keys())
                    if isinstance(data.get("routines"), dict)
                    else [],
                    "feeds": len(data.get("feeds") or [])
                    if isinstance(data.get("feeds"), list)
                    else 0,
                    "enabled": plugin["name"] in enabled,
                    "error": plugin["error"],
                }
            )
        return result

    def routines(self) -> dict[str, Routine]:
        from sentinel.automation.routines import parse_routines

        enabled = self._enabled()
        result: dict[str, Routine] = {}
        for plugin in self._load():
            if plugin["name"] not in enabled or plugin["error"]:
                continue
            raw = plugin["data"].get("routines")
            if isinstance(raw, dict):
                for name, routine in parse_routines(raw, origin=f"plugin:{plugin['name']}").items():
                    result[f"{plugin['name']}-{name}"[:32]] = routine
        return result

    def feeds(self) -> list[dict[str, str]]:
        enabled = self._enabled()
        feeds: list[dict[str, str]] = []
        for plugin in self._load():
            if plugin["name"] not in enabled or plugin["error"]:
                continue
            for feed in (plugin["data"].get("feeds") or [])[:MAX_FEEDS]:
                if (
                    isinstance(feed, dict)
                    and str(feed.get("url", "")).startswith("https://")
                    and feed.get("region") in ("de", "world")
                ):
                    feeds.append(
                        {
                            "region": feed["region"],
                            "name": str(feed.get("name", "?"))[:40],
                            "url": str(feed["url"])[:300],
                        }
                    )
        return feeds
