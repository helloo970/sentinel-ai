"""Markets: crypto (CoinGecko), stocks & indices (Yahoo Finance), currencies (ECB via Frankfurter).

All without keys. Quotes are foreign web data: only numbers are taken over, names come
from the fixed list below. Results are cached for 10 minutes.
"""

from __future__ import annotations

import asyncio
import logging
import math
import time
from datetime import date, timedelta
from typing import TYPE_CHECKING, Any

import httpx

if TYPE_CHECKING:
    from sentinel.services import Services

log = logging.getLogger(__name__)

CACHE_S = 600
CRYPTO = [
    ("bitcoin", "BTC", "Bitcoin"),
    ("ethereum", "ETH", "Ethereum"),
    ("solana", "SOL", "Solana"),
]
STOCKS = [
    ("^GDAXI", "DAX", "DAX"),
    ("^GSPC", "S&P 500", "S&P 500"),
    ("^IXIC", "NASDAQ", "Nasdaq"),
    ("NVDA", "NVDA", "Nvidia"),
    ("AAPL", "AAPL", "Apple"),
    ("MSFT", "MSFT", "Microsoft"),
]
FX = [("USD", "Dollar"), ("TRY", "Lira"), ("GBP", "Pound")]
COINGECKO = "https://api.coingecko.com/api/v3/coins/markets"
YAHOO = "https://query1.finance.yahoo.com/v8/finance/chart/"
FRANKFURTER = "https://api.frankfurter.app/"
HEADERS = {"User-Agent": "Mozilla/5.0 (SENTINEL AI)", "Accept": "application/json"}


def _num(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _series(values: Any, limit: int = 120) -> list[float]:
    if not isinstance(values, list):
        return []
    clean = [n for n in (_num(v) for v in values) if n is not None]
    if len(clean) > limit:
        step = len(clean) / limit
        clean = [clean[int(i * step)] for i in range(limit)]
    return [round(v, 6) for v in clean]


def _change(series: list[float], price: float | None) -> float | None:
    if not series or price is None or not series[0]:
        return None
    return round((price - series[0]) / series[0] * 100, 2)


def parse_crypto(data: Any) -> list[dict[str, Any]]:
    by_id = (
        {row.get("id"): row for row in data if isinstance(row, dict)}
        if isinstance(data, list)
        else {}
    )
    items = []
    for coin_id, symbol, name in CRYPTO:
        row = by_id.get(coin_id)
        if not row:
            continue
        price = _num(row.get("current_price"))
        series = _series((row.get("sparkline_in_7d") or {}).get("price"))
        items.append(
            {
                "symbol": symbol,
                "name": name,
                "price": price,
                "currency": "EUR",
                "change_pct": _num(row.get("price_change_percentage_24h")),
                "period": "24 h",
                "series": series,
            }
        )
    return items


def parse_yahoo(symbol: str, name: str, label: str, data: Any) -> dict[str, Any] | None:
    try:
        result = data["chart"]["result"][0]
        meta = result.get("meta", {})
        closes = result["indicators"]["quote"][0]["close"]
    except (KeyError, IndexError, TypeError):
        return None
    series = _series(closes)
    price = _num(meta.get("regularMarketPrice")) or (series[-1] if series else None)
    return {
        "symbol": label,
        "name": name,
        "price": price,
        # Indices have points, not a currency
        "currency": "PTS" if symbol.startswith("^") else str(meta.get("currency") or "")[:4],
        "change_pct": _change(series, price),
        "period": "1 month",
        "series": series,
    }


def parse_fx(data: Any) -> list[dict[str, Any]]:
    rates = data.get("rates") if isinstance(data, dict) else None
    if not isinstance(rates, dict):
        return []
    days = sorted(rates)
    items = []
    for code, name in FX:
        series = _series([(rates[d] or {}).get(code) for d in days])
        price = series[-1] if series else None
        items.append(
            {
                "symbol": f"EUR/{code}",
                "name": f"Euro in {name}",
                "price": price,
                "currency": code,
                "change_pct": _change(series, price),
                "period": "1 month",
                "series": series,
            }
        )
    return items


class MarketService:
    def __init__(self, svc: Services, client: httpx.AsyncClient | None = None) -> None:
        self.svc = svc
        self._client = client
        self._cache: tuple[float, dict[str, Any]] | None = None
        self._lock = asyncio.Lock()

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(8.0, connect=5.0), headers=HEADERS, follow_redirects=True
            )
        return self._client

    async def _crypto(self) -> list[dict[str, Any]]:
        resp = await self._http().get(
            COINGECKO,
            params={
                "vs_currency": "eur",
                "ids": ",".join(c[0] for c in CRYPTO),
                "sparkline": "true",
                "price_change_percentage": "24h",
            },
        )
        resp.raise_for_status()
        return parse_crypto(resp.json())

    async def _stocks(self) -> list[dict[str, Any]]:
        async def one(symbol: str, label: str, name: str) -> dict[str, Any] | None:
            resp = await self._http().get(YAHOO + symbol, params={"range": "1mo", "interval": "1d"})
            resp.raise_for_status()
            return parse_yahoo(symbol, name, label, resp.json())

        results = await asyncio.gather(*(one(*s) for s in STOCKS), return_exceptions=True)
        return [r for r in results if isinstance(r, dict)]

    async def _fx(self) -> list[dict[str, Any]]:
        start = (date.today() - timedelta(days=31)).isoformat()
        resp = await self._http().get(
            f"{FRANKFURTER}{start}..", params={"from": "EUR", "to": ",".join(c for c, _ in FX)}
        )
        resp.raise_for_status()
        return parse_fx(resp.json())

    async def overview(self, *, force: bool = False) -> dict[str, Any]:
        async with self._lock:
            if self._cache and not force and time.time() - self._cache[0] < CACHE_S:
                return self._cache[1]
            groups, errors = [], []
            for key, title, loader in (
                ("crypto", "Crypto", self._crypto),
                ("stocks", "Stocks & indices", self._stocks),
                ("fx", "Currencies", self._fx),
            ):
                try:
                    items = await loader()
                except (httpx.HTTPError, ValueError) as exc:
                    log.info("markets %s failed: %s", key, exc)
                    items = []
                if not items:
                    errors.append(title)
                groups.append({"key": key, "title": title, "items": items})
            data = {"updated": time.time(), "groups": groups, "errors": errors}
            self._cache = (time.time(), data)
            return data

    async def summary(self) -> str:
        return spoken_summary(await self.overview())


def _price(value: float | None, currency: str) -> str:
    if value is None:
        return "unknown"
    digits = 2 if value < 1000 else 0
    text = f"{value:,.{digits}f}"
    units = {"EUR": "euros", "USD": "dollars", "TRY": "lira", "GBP": "pounds", "PTS": "points"}
    unit = units.get(currency, currency)
    return f"{text} {unit}".strip()


def _move(change: float | None) -> str:
    if change is None:
        return ""
    word = "up" if change >= 0 else "down"
    return f", {word} {abs(change):.1f} percent"


def spoken_summary(data: dict[str, Any]) -> str:
    """Short announcement: the most important quotes and the biggest move."""
    items = {i["symbol"]: i for g in data.get("groups", []) for i in g.get("items", [])}
    parts = []
    for symbol in ("BTC", "DAX", "S&P 500"):
        item = items.get(symbol)
        if item:
            parts.append(
                f"{item['name']} is at {_price(item['price'], item['currency'])}"
                f"{_move(item['change_pct'])} ({item['period']})"
            )
    movers = [i for i in items.values() if i.get("change_pct") is not None]
    if movers:
        top = max(movers, key=lambda i: abs(i["change_pct"]))
        parts.append(f"Biggest move: {top['name']}{_move(top['change_pct'])}")
    for symbol in ("EUR/USD", "EUR/TRY"):
        item = items.get(symbol)
        if item and item["price"] is not None:
            parts.append(f"One euro costs {_price(item['price'], item['currency'])}")
    if not parts:
        return "The market data is unreachable right now."
    return ". ".join(parts) + "."
