"""Order suggestions: research a product, show info, open the shop page after approval.

Security:
- Research only via the Claude web search, restricted to a shop allowlist.
  Results from the web are not trustworthy.
- Every URL must be https and belong to an allowed shop domain (against phishing).
- "Order" only opens the product page in the browser - after confirmation with a PIN.
  SENTINEL never pays by itself and stores no payment data.
"""

from __future__ import annotations

import json
import re
import time
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

from sentinel.config import DEFAULT_SHOPS
from sentinel.llm.base import LLMError
from sentinel.llm.untrusted import UNTRUSTED_RULES
from sentinel.secrets_filter import contains_secret

if TYPE_CHECKING:
    from sentinel.services import Services

__all__ = ["DEFAULT_SHOPS", "ShopError", "ShopService", "parse_product", "shop_domain"]


RESEARCH_SYSTEM = f"""You research a product for a purchase suggestion. Search only on the
allowed shops. Answer exclusively with a JSON object (no Markdown):
{{"title": str, "price": number|null, "currency": "EUR", "shop": str, "url": str,
  "summary": str, "specs": [str], "availability": str}}
"url" must be the concrete product page. "summary" at most 3 sentences in English,
"specs" at most 8 short key facts. No inventions: leave unknown things empty.

{UNTRUSTED_RULES}"""

_JSON = re.compile(r"\{.*\}", re.DOTALL)


class ShopError(ValueError):
    pass


def shop_domain(url: str, allowed: list[str]) -> str | None:
    """Return the allowed shop domain or None (https only, no credentials)."""
    try:
        parsed = urlparse(url)
    except ValueError:
        return None
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        return None
    if parsed.port not in (None, 443):
        return None
    host = parsed.hostname.lower().rstrip(".")
    for domain in allowed:
        if host == domain or host.endswith("." + domain):
            return domain
    return None


def _clean(value: Any, limit: int) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:limit]


def parse_product(text: str, allowed: list[str]) -> dict[str, Any]:
    match = _JSON.search(text or "")
    if not match:
        raise ShopError("No product data found")
    try:
        raw = json.loads(match.group(0))
    except json.JSONDecodeError as exc:
        raise ShopError("Product data unreadable") from exc
    url = _clean(raw.get("url"), 500)
    domain = shop_domain(url, allowed)
    if domain is None:
        raise ShopError("Link does not lead to an allowed shop")
    price = raw.get("price")
    try:
        price = round(float(price), 2) if price is not None else None
    except (TypeError, ValueError):
        price = None
    specs = [_clean(s, 120) for s in (raw.get("specs") or []) if _clean(s, 120)][:8]
    return {
        "title": _clean(raw.get("title"), 200) or "Unknown product",
        "price": price,
        "currency": _clean(raw.get("currency"), 3) or "EUR",
        "shop": domain,
        "url": url,
        "summary": _clean(raw.get("summary"), 600),
        "specs": specs,
        "availability": _clean(raw.get("availability"), 80),
    }


class ShopService:
    def __init__(self, svc: Services) -> None:
        self.svc = svc

    @property
    def shops(self) -> list[str]:
        return [d.lower() for d in self.svc.config.shop_domains]

    async def research(self, query: str) -> dict[str, Any]:
        query = query.strip()
        if not 3 <= len(query) <= 200:
            raise ShopError("Search term must be 3 to 200 characters")
        if contains_secret(query):
            raise ShopError("Search term looks like a secret")
        ok, reason = self.svc.costs.check()
        if not ok:
            raise ShopError(reason)
        try:
            result = await self.svc.llm.research(
                system=RESEARCH_SYSTEM,
                prompt=f"Product request from {self.svc.config.user_name or 'the user'}: {query}",
                allowed_domains=self.shops,
            )
        except LLMError as exc:
            raise ShopError(str(exc)) from exc
        self.svc.costs.record(result.model, "shop_research", result.usage)
        product = parse_product(result.text, self.shops)
        order_id = self.svc.db.execute(
            "INSERT INTO orders(ts, query, data) VALUES(?,?,?)",
            (time.time(), query, json.dumps(product, ensure_ascii=False)),
        )
        return self.get(order_id) or {}

    def get(self, order_id: int) -> dict[str, Any] | None:
        row = self.svc.db.query_one("SELECT * FROM orders WHERE id = ?", (order_id,))
        if row is None:
            return None
        return {**row, "data": json.loads(row["data"])}

    def list(self) -> list[dict[str, Any]]:
        rows = self.svc.db.query("SELECT * FROM orders ORDER BY ts DESC LIMIT 50")
        return [{**r, "data": json.loads(r["data"])} for r in rows]

    def set_status(self, order_id: int, status: str) -> None:
        self.svc.db.execute("UPDATE orders SET status = ? WHERE id = ?", (status, order_id))
