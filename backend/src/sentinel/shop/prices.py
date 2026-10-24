"""Price alert: regularly check product pages of allowed shops, report a price drop.

Security:
- Only https pages from domains on the shop allowlist. Redirects are checked one by one
  (at most 3), never followed automatically.
- Only price, currency and title are read from the page (structured data per
  schema.org or meta tags). None of it is passed to the AI as an instruction; the title
  is treated as untrusted and only displayed.
- Many large shops block automated requests. This is shown as an error.
"""

from __future__ import annotations

import html
import json
import logging
import re
import time
from typing import TYPE_CHECKING, Any
from urllib.parse import urljoin

import httpx

from sentinel.shop.service import shop_domain

if TYPE_CHECKING:
    from sentinel.services import Services

log = logging.getLogger(__name__)

CHECK_INTERVAL_S = 6 * 3600
MAX_WATCHES = 30
MAX_BYTES = 3_000_000
MAX_REDIRECTS = 3
DROP_PERCENT = 5.0
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) SENTINEL-AI price watch"

_LDJSON = re.compile(
    r"<script[^>]+type=[\"']application/ld\+json[\"'][^>]*>(.*?)</script>", re.I | re.S
)
_META = re.compile(r"<meta\s+([^>]+)>", re.I)
_ATTR = re.compile(r"([a-zA-Z:_-]+)\s*=\s*[\"']([^\"']*)[\"']")
_TITLE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)


class PriceError(ValueError):
    pass


def parse_price(value: Any) -> float | None:
    """'1.299,00 EUR', '1299.00', 1299 -> 1299.0 (also handles German number formats)"""
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value) if value > 0 else None
    text = re.sub(r"[^\d.,]", "", str(value or ""))
    if not text:
        return None
    if "," in text and "." in text:
        if text.rfind(",") > text.rfind("."):
            text = text.replace(".", "").replace(",", ".")
        else:
            text = text.replace(",", "")
    elif "," in text:
        whole, _, frac = text.rpartition(",")
        text = f"{whole.replace(',', '')}.{frac}" if len(frac) <= 2 else text.replace(",", "")
    elif text.count(".") > 1:
        whole, _, frac = text.rpartition(".")
        text = f"{whole.replace('.', '')}.{frac}"
    elif "." in text and len(text.rpartition(".")[2]) == 3:
        text = text.replace(".", "")  # 1.299 = thousands separator
    try:
        price = float(text)
    except ValueError:
        return None
    return price if 0 < price < 1_000_000 else None


def _offers(node: Any) -> list[dict[str, Any]]:
    """All Offer objects from JSON-LD (also inside @graph and lists)."""
    found: list[dict[str, Any]] = []
    stack = [node]
    while stack and len(found) < 50:
        item = stack.pop()
        if isinstance(item, list):
            stack.extend(item)
        elif isinstance(item, dict):
            kind = item.get("@type")
            kinds = kind if isinstance(kind, list) else [kind]
            if any(k in ("Offer", "AggregateOffer") for k in kinds):
                found.append(item)
            for key in ("@graph", "offers", "mainEntity", "itemOffered"):
                if key in item:
                    stack.append(item[key])
    return found


def _ld_name(node: Any) -> str:
    stack = [node]
    while stack:
        item = stack.pop()
        if isinstance(item, list):
            stack.extend(item)
        elif isinstance(item, dict):
            kind = item.get("@type")
            kinds = kind if isinstance(kind, list) else [kind]
            if "Product" in kinds and item.get("name"):
                return str(item["name"])
            if "@graph" in item:
                stack.append(item["@graph"])
    return ""


def extract_offer(page: str) -> dict[str, Any]:
    """Price, currency and title from a product page (without running scripts)."""
    page = page[:MAX_BYTES]
    price: float | None = None
    currency = "EUR"
    title = ""
    for block in _LDJSON.findall(page)[:20]:
        try:
            data = json.loads(html.unescape(block.strip()))
        except ValueError:
            continue
        title = title or _ld_name(data)
        for offer in _offers(data):
            value = parse_price(offer.get("price") or offer.get("lowPrice"))
            if value is not None:
                price = value if price is None else min(price, value)
                currency = str(offer.get("priceCurrency") or currency)[:3].upper()
    if price is None:
        meta: dict[str, str] = {}
        for raw in _META.findall(page)[:400]:
            attrs = {k.lower(): v for k, v in _ATTR.findall(raw)}
            key = attrs.get("property") or attrs.get("itemprop") or attrs.get("name")
            if key and "content" in attrs:
                meta.setdefault(key.lower(), attrs["content"])
        for key in ("product:price:amount", "og:price:amount", "price"):
            price = parse_price(meta.get(key))
            if price is not None:
                currency = (
                    meta.get("product:price:currency")
                    or meta.get("og:price:currency")
                    or meta.get("pricecurrency")
                    or currency
                )[:3].upper()
                break
        title = title or meta.get("og:title", "")
    if not title:
        found = _TITLE.search(page)
        title = found.group(1) if found else ""
    title = re.sub(r"\s+", " ", html.unescape(title)).strip()[:160]
    if price is None:
        raise PriceError("No price found on the page")
    return {"price": round(price, 2), "currency": currency or "EUR", "title": title}


class PriceWatcher:
    def __init__(self, svc: Services) -> None:
        self.svc = svc

    @property
    def shops(self) -> list[str]:
        return list(self.svc.config.shop_domains)

    # ------------------------------------------------------------------ Fetching

    def fetch(self, url: str) -> str:
        current = url
        with httpx.Client(
            timeout=20,
            follow_redirects=False,
            headers={"User-Agent": USER_AGENT, "Accept-Language": "de-DE,de;q=0.9,en;q=0.5"},
        ) as client:
            for _ in range(MAX_REDIRECTS + 1):
                if shop_domain(current, self.shops) is None:
                    raise PriceError("Link does not lead to an allowed shop")
                with client.stream("GET", current) as response:
                    if response.is_redirect:
                        current = urljoin(current, response.headers.get("location", ""))
                        continue
                    if response.status_code in (403, 429, 503):
                        raise PriceError(f"Shop blocks automated requests ({response.status_code})")
                    if response.status_code != 200:
                        raise PriceError(f"Page not reachable (HTTP {response.status_code})")
                    chunks: list[bytes] = []
                    size = 0
                    for chunk in response.iter_bytes():
                        chunks.append(chunk)
                        size += len(chunk)
                        if size >= MAX_BYTES:
                            break
                    encoding = response.encoding or "utf-8"
                    return b"".join(chunks).decode(encoding, errors="replace")
        raise PriceError("Too many redirects")

    # ------------------------------------------------------------------ Management

    def add(self, url: str, target: float | None) -> dict[str, Any]:
        domain = shop_domain(url, self.shops)
        if domain is None:
            raise PriceError("Only product pages of allowed shops (https)")
        existing = self.svc.db.query_one("SELECT * FROM price_watches WHERE url = ?", (url,))
        if existing:
            self.svc.db.execute(
                "UPDATE price_watches SET active = 1, target = ? WHERE id = ?",
                (target, existing["id"]),
            )
            return self.get(existing["id"]) or {}
        count = self.svc.db.query_one("SELECT COUNT(*) AS n FROM price_watches WHERE active = 1")
        if count and count["n"] >= MAX_WATCHES:
            raise PriceError(f"At most {MAX_WATCHES} price alerts")
        watch_id = self.svc.db.execute(
            "INSERT INTO price_watches(url, shop, target, created) VALUES(?,?,?,?)",
            (url, domain, target, time.time()),
        )
        return self.get(watch_id) or {}

    def get(self, watch_id: int) -> dict[str, Any] | None:
        return self.svc.db.query_one("SELECT * FROM price_watches WHERE id = ?", (watch_id,))

    def set_active(self, watch_id: int, active: bool) -> bool:
        return bool(
            self.svc.db.execute(
                "UPDATE price_watches SET active = ? WHERE id = ?", (int(active), watch_id)
            )
        )

    def list(self) -> list[dict[str, Any]]:
        watches = self.svc.db.query(
            "SELECT * FROM price_watches WHERE active = 1 ORDER BY created DESC"
        )
        for watch in watches:
            watch["history"] = self.svc.db.query(
                "SELECT ts, price FROM price_history WHERE watch_id = ? ORDER BY ts DESC LIMIT 30",
                (watch["id"],),
            )[::-1]
        return watches

    # ------------------------------------------------------------------ Checking

    def check(self, watch: dict[str, Any]) -> dict[str, Any]:
        now = time.time()
        try:
            offer = extract_offer(self.fetch(watch["url"]))
        except (PriceError, httpx.HTTPError) as exc:
            message = str(exc) or type(exc).__name__
            self.svc.db.execute(
                "UPDATE price_watches SET last_check = ?, last_error = ? WHERE id = ?",
                (now, message[:200], watch["id"]),
            )
            return {"id": watch["id"], "error": message}
        before = watch.get("last_price")
        self.svc.db.execute(
            "UPDATE price_watches SET last_check = ?, last_error = NULL, last_price = ?, "
            "currency = ?, title = CASE WHEN title = '' THEN ? ELSE title END WHERE id = ?",
            (now, offer["price"], offer["currency"], offer["title"], watch["id"]),
        )
        self.svc.db.execute(
            "INSERT INTO price_history(watch_id, ts, price) VALUES(?,?,?)",
            (watch["id"], now, offer["price"]),
        )
        title = watch.get("title") or offer["title"] or watch["shop"]
        reason = alert_reason(before, offer["price"], watch.get("target"))
        if reason:
            self.svc.notifier.notify(
                "Price alert",
                f"{title}: {euro(offer['price'])} {offer['currency']} ({reason})",
                push=True,
            )
        return {"id": watch["id"], **offer, "before": before, "alert": reason}

    def check_due(self, now: float | None = None, limit: int = 1) -> list[dict[str, Any]]:
        now = now or time.time()
        due = self.svc.db.query(
            "SELECT * FROM price_watches WHERE active = 1 AND last_check < ? "
            "ORDER BY last_check LIMIT ?",
            (now - CHECK_INTERVAL_S, limit),
        )
        return [self.check(w) for w in due]


def euro(value: float) -> str:
    return f"{value:.2f}"


def alert_reason(before: float | None, now: float, target: float | None) -> str | None:
    if target is not None and now <= target and (before is None or before > target):
        return f"target price {euro(target)} reached"
    if before and now < before * (1 - DROP_PERCENT / 100):
        return f"{(before - now) / before:.0%} cheaper"
    return None
