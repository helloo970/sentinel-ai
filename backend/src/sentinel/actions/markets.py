"""Markets and KPIs for Jarvis (read only)."""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING

from sentinel.markets import kpis
from sentinel.markets.service import CACHE_S, MarketService, spoken_summary
from sentinel.permissions.models import ActionResult, ActionSpec, NoParams, Risk

if TYPE_CHECKING:
    from sentinel.permissions import ActionRegistry
    from sentinel.services import Services


def _markets(p: NoParams, svc: Services) -> ActionResult:
    cache = svc.markets._cache
    if cache and time.time() - cache[0] < CACHE_S:
        data = cache[1]
    else:
        # Runs in the worker thread: own client and own event loop
        data = asyncio.run(MarketService(svc).overview())
        svc.markets._cache = (time.time(), data)
    return ActionResult(spoken_summary(data), data, untrusted=True)


def _kpis(p: NoParams, svc: Services) -> ActionResult:
    tiles = kpis.collect(svc)
    return ActionResult(kpis.spoken(tiles), tiles)


def register(registry: ActionRegistry) -> None:
    registry.register(
        ActionSpec(
            "markets_overview",
            "Markets",
            "Current prices: Bitcoin, Ethereum, Solana, DAX, S&P 500, Nasdaq, Nvidia, Apple, "
            "Microsoft and the euro in dollars, lira, pounds (with change).",
            Risk.READ,
            NoParams,
            _markets,
            untrusted_output=True,
        )
    )
    registry.register(
        ActionSpec(
            "kpis_overview",
            "KPIs",
            "All key figures at a glance: alerts, PC, home network, server, backups, AI costs, "
            "appointments, shopping list, brain.",
            Risk.READ,
            NoParams,
            _kpis,
        )
    )
