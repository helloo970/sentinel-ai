"""News briefing (Germany + world) from RSS/Atom feeds.

Headlines are untrusted: the summary runs through a model call without tools, and the
data is marked.
"""

from __future__ import annotations

import asyncio
import html
import logging
import re
import time
from typing import TYPE_CHECKING, Any

import httpx
from defusedxml import ElementTree

from sentinel.llm.base import LLMError
from sentinel.llm.prompts import BRIEFING_SYSTEM
from sentinel.llm.untrusted import wrap_untrusted

if TYPE_CHECKING:
    from sentinel.services import Services

log = logging.getLogger(__name__)

ATOM = "{http://www.w3.org/2005/Atom}"
PER_FEED = 6
CACHE_S = 900
MAX_BYTES = 2_000_000
_TAGS = re.compile(r"<[^>]+>")


def _text(value: str | None, limit: int) -> str:
    cleaned = html.unescape(_TAGS.sub(" ", value or ""))
    return re.sub(r"\s+", " ", cleaned).strip()[:limit]


def parse_feed(xml_bytes: bytes, limit: int = PER_FEED) -> list[dict[str, str]]:
    root = ElementTree.fromstring(xml_bytes)
    items = []
    for item in root.iter("item"):
        items.append(
            {
                "title": _text(item.findtext("title"), 200),
                "summary": _text(item.findtext("description"), 300),
            }
        )
    if not items:
        for entry in root.iter(f"{ATOM}entry"):
            items.append(
                {
                    "title": _text(entry.findtext(f"{ATOM}title"), 200),
                    "summary": _text(entry.findtext(f"{ATOM}summary"), 300),
                }
            )
    return [i for i in items if i["title"]][:limit]


class NewsService:
    def __init__(self, svc: Services) -> None:
        self.svc = svc
        self._cache: tuple[float, dict[str, Any]] | None = None

    async def _fetch_feed(self, client: httpx.AsyncClient, feed: dict[str, str]) -> dict[str, Any]:
        try:
            response = await client.get(feed["url"])
            response.raise_for_status()
            content = response.content[:MAX_BYTES]
            items = await asyncio.to_thread(parse_feed, content)
            return {
                "name": feed.get("name", "?"),
                "region": feed.get("region", "world"),
                "items": items,
            }
        except (httpx.HTTPError, ElementTree.ParseError, ValueError) as exc:
            log.info("feed %s failed: %s", feed.get("name"), type(exc).__name__)
            return {
                "name": feed.get("name", "?"),
                "region": feed.get("region", "world"),
                "items": [],
                "error": type(exc).__name__,
            }

    def feeds(self) -> list[dict[str, str]]:
        feeds = [
            f for f in self.svc.config.news_feeds if str(f.get("url", "")).startswith("https://")
        ]
        if self.svc.plugins is not None:
            feeds += self.svc.plugins.feeds()
        return feeds

    async def headlines(self) -> dict[str, Any]:
        if self._cache and time.time() - self._cache[0] < CACHE_S:
            return self._cache[1]
        async with httpx.AsyncClient(
            timeout=10, follow_redirects=True, headers={"User-Agent": "SENTINEL-AI/0.1"}
        ) as client:
            results = await asyncio.gather(*(self._fetch_feed(client, f) for f in self.feeds()))
        data = {"fetched": time.time(), "feeds": list(results)}
        self._cache = (time.time(), data)
        return data

    async def briefing(self) -> dict[str, Any]:
        data = await self.headlines()
        de = [i["title"] for f in data["feeds"] if f["region"] == "de" for i in f["items"]]
        world = [i["title"] for f in data["feeds"] if f["region"] != "de" for i in f["items"]]
        if not de and not world:
            return {"text": "I couldn't fetch any news right now.", "headlines": data}

        fallback = "Germany: " + "; ".join(de[:5]) + ". World: " + "; ".join(world[:5]) + "."
        ok, _reason = self.svc.costs.check()
        if not self.svc.llm.available or not ok or self.svc.config.ai_only_in_chat:
            return {"text": fallback, "headlines": data, "summarized": False}
        grouped: dict[str, list[dict[str, Any]]] = {"germany": [], "world": []}
        for feed in data["feeds"]:
            key = "germany" if feed["region"] == "de" else "world"
            grouped[key].append({"source": feed["name"], "headlines": feed["items"]})
        prompt = (
            f"Create the news briefing for {self.svc.config.user_name or 'the user'}.\n"
            + wrap_untrusted("news", grouped)
        )
        try:
            result = await self.svc.llm.complete(
                system=BRIEFING_SYSTEM, prompt=prompt, max_tokens=2000
            )
        except LLMError:
            return {"text": fallback, "headlines": data, "summarized": False}
        self.svc.costs.record(result.model, "briefing", result.usage)
        text = result.text.strip() if result.stop_reason != "refusal" else ""
        return {"text": text or fallback, "headlines": data, "summarized": bool(text)}
