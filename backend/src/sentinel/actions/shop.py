"""Shop actions: research a product (read) and open the order page (approval + PIN)."""

from __future__ import annotations

import asyncio
import webbrowser
from typing import TYPE_CHECKING

from pydantic import Field

from sentinel.permissions.models import (
    ActionDenied,
    ActionResult,
    ActionSpec,
    Risk,
    StrictParams,
)
from sentinel.shop.service import ShopError, shop_domain

if TYPE_CHECKING:
    from sentinel.permissions import ActionRegistry
    from sentinel.services import Services


class QueryParams(StrictParams):
    query: str = Field(min_length=3, max_length=200)


class OrderParams(StrictParams):
    order_id: int = Field(ge=1)


def _research(p: QueryParams, svc: Services) -> ActionResult:
    try:
        order = asyncio.run(svc.shop.research(p.query))
    except ShopError as exc:
        raise ActionDenied(str(exc)) from exc
    data = order["data"]
    return ActionResult(
        f"Suggestion #{order['id']}: {data['title']} at {data['shop']}",
        {"order_id": order["id"], **data},
        untrusted=True,
    )


def _order(p: OrderParams, svc: Services) -> dict:
    order = svc.shop.get(p.order_id)
    if order is None:
        raise ActionDenied("Order suggestion not found")
    if shop_domain(order["data"]["url"], svc.shop.shops) is None:
        raise ActionDenied("Shop is not (or no longer) on the allowlist")
    return order


def _check_open(p: OrderParams, svc: Services) -> None:
    _order(p, svc)


def _preview_open(p: OrderParams, svc: Services) -> str:
    data = _order(p, svc)["data"]
    price = f"{data['price']:.2f} {data['currency']}" if data.get("price") else "price open"
    return f"Open order page: {data['title']} - {price} at {data['shop']}"


def _open(p: OrderParams, svc: Services) -> ActionResult:
    order = _order(p, svc)
    if not webbrowser.open(order["data"]["url"], new=2):
        raise ActionDenied("Could not open the browser")
    svc.shop.set_status(p.order_id, "opened")
    return ActionResult(
        f"Opened the {order['data']['shop']} shop page - "
        "you complete the purchase and payment yourself",
        {"url": order["data"]["url"]},
    )


def register(registry: ActionRegistry) -> None:
    registry.register(
        ActionSpec(
            "shop_research",
            "Research product",
            "Searches for a product in allowed "
            "online shops and creates an order suggestion with details (orders nothing).",
            Risk.READ,
            QueryParams,
            _research,
            untrusted_output=True,
        )
    )
    registry.register(
        ActionSpec(
            "open_order",
            "Open order",
            "After approval with PIN, opens the "
            "product page of an order suggestion in the browser. Never pays by itself.",
            Risk.FINAL,
            OrderParams,
            _open,
            check=_check_open,
            preview=_preview_open,
            requires_pin=True,
        )
    )
