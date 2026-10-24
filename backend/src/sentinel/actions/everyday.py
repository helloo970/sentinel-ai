"""Everyday: shopping list, price alerts and parcels."""

from __future__ import annotations

import webbrowser
from typing import TYPE_CHECKING, Any, Literal

from pydantic import Field

from sentinel.permissions.models import (
    ActionDenied,
    ActionResult,
    ActionSpec,
    Risk,
    StrictParams,
)
from sentinel.secrets_filter import contains_secret
from sentinel.shop.prices import PriceError, euro
from sentinel.shop.service import shop_domain
from sentinel.shop.shopping import split_items

if TYPE_CHECKING:
    from sentinel.permissions import ActionRegistry
    from sentinel.services import Services


class NoParams(StrictParams):
    pass


class ItemParams(StrictParams):
    text: str = Field(min_length=1, max_length=200, description="e.g. 'milk, eggs and bread'")


class IdParams(StrictParams):
    id: int = Field(ge=1)


# ---------------------------------------------------------------------- Shopping list


def _check_item(p: ItemParams, svc: Services) -> None:
    if contains_secret(p.text):
        raise ActionDenied("Entries must not contain secrets")
    if not split_items(p.text):
        raise ActionDenied("Empty entry")


def _add_items(p: ItemParams, svc: Services) -> ActionResult:
    added, existing = [], []
    for text in split_items(p.text):
        try:
            item = svc.shopping.add(text)
        except ValueError as exc:
            raise ActionDenied(str(exc)) from exc
        (existing if item["existing"] else added).append(item)
    names = [i["text"] for i in added]
    if not names:
        message = "Already on the list"
    elif len(names) == 1:
        message = f"{names[0]} is on the list"
    else:
        message = f"{', '.join(names[:-1])} and {names[-1]} are on the list"
    return ActionResult(
        message, {"added": added}, undo={"ids": [i["id"] for i in added]} if added else None
    )


def _undo_add(data: dict[str, Any], svc: Services) -> str:
    for item_id in data["ids"]:
        svc.shopping.set_done(int(item_id), True)
    return "Taken off the list again"


def _remove_item(p: ItemParams, svc: Services) -> ActionResult:
    removed = []
    for text in split_items(p.text):
        item = svc.shopping.find(text)
        if item:
            svc.shopping.set_done(item["id"], True)
            removed.append(item)
    if not removed:
        raise ActionDenied("That is not on the list")
    names = ", ".join(i["text"] for i in removed)
    return ActionResult(
        f"{names} crossed off the list", None, undo={"ids": [i["id"] for i in removed]}
    )


def _undo_remove(data: dict[str, Any], svc: Services) -> str:
    for item_id in data["ids"]:
        svc.shopping.set_done(int(item_id), False)
    return "Back on the list"


def _list_items(p: NoParams, svc: Services) -> ActionResult:
    return ActionResult(svc.shopping.spoken(), svc.shopping.items())


# ---------------------------------------------------------------------- Price alert


class WatchParams(StrictParams):
    url: str = Field(min_length=12, max_length=500)
    target: float | None = Field(default=None, gt=0, lt=1_000_000)


def _check_watch(p: WatchParams, svc: Services) -> None:
    if shop_domain(p.url, svc.prices.shops) is None:
        raise ActionDenied("Only product pages of allowed shops (https)")


def _watch(p: WatchParams, svc: Services) -> ActionResult:
    try:
        watch = svc.prices.add(p.url, p.target)
    except PriceError as exc:
        raise ActionDenied(str(exc)) from exc
    result = svc.prices.check(watch)
    if result.get("error"):
        message = f"Price alert created at {watch['shop']} - lookup currently not possible: "
        message += result["error"]
    else:
        message = f"Price alert created: currently {euro(result['price'])} {result['currency']}"
    if p.target:
        message += f", target {euro(p.target)}"
    return ActionResult(message, {"id": watch["id"]}, undo={"id": watch["id"]}, untrusted=True)


def _undo_watch(data: dict[str, Any], svc: Services) -> str:
    svc.prices.set_active(int(data["id"]), False)
    return "Price alert removed"


def _check_unwatch(p: IdParams, svc: Services) -> None:
    watch = svc.prices.get(p.id)
    if watch is None or not watch["active"]:
        raise ActionDenied("Price alert not found")


def _unwatch(p: IdParams, svc: Services) -> ActionResult:
    svc.prices.set_active(p.id, False)
    return ActionResult("Price alert removed", None, undo={"id": p.id})


def _undo_unwatch(data: dict[str, Any], svc: Services) -> str:
    svc.prices.set_active(int(data["id"]), True)
    return "Price alert active again"


def _list_watches(p: NoParams, svc: Services) -> ActionResult:
    watches = [
        {k: w[k] for k in ("id", "shop", "title", "last_price", "target", "currency", "last_error")}
        for w in svc.prices.list()
    ]
    return ActionResult(f"{len(watches)} price alerts", watches, untrusted=True)


# ---------------------------------------------------------------------- Parcels


def _parcels(p: NoParams, svc: Services) -> ActionResult:
    from sentinel.mail.service import MailError

    if not svc.mail.configured:
        raise ActionDenied("Gmail is not connected")
    try:
        parcels = svc.mail.parcels()
    except MailError as exc:
        raise ActionDenied(str(exc)) from exc
    data = [{k: v for k, v in p.items() if k != "subject"} for p in parcels]
    return ActionResult(f"{len(data)} shipments found", data, untrusted=True)


class TrackingParams(StrictParams):
    carrier: Literal["dhl", "hermes", "dpd", "gls", "ups", "amazon"]
    number: str = Field(pattern=r"^[0-9A-Z]{10,22}$")


def _open_tracking(p: TrackingParams, svc: Services) -> ActionResult:
    from sentinel.mail.parcels import tracking_url

    url = tracking_url(p.carrier, p.number)
    if not webbrowser.open(url, new=2):
        raise ActionDenied("Could not open the browser")
    return ActionResult("Tracking opened in the browser", {"url": url})


def register(registry: ActionRegistry) -> None:
    specs = [
        ActionSpec(
            "add_shopping_item",
            "Add to shopping list",
            "Puts one or more items on the shopping list.",
            Risk.LOW,
            ItemParams,
            _add_items,
            undo=_undo_add,
            check=_check_item,
            preview=lambda p, s: f"Shopping list: {p.text}",
        ),
        ActionSpec(
            "remove_shopping_item",
            "Remove from shopping list",
            "Crosses items off the shopping list.",
            Risk.LOW,
            ItemParams,
            _remove_item,
            undo=_undo_remove,
            check=_check_item,
        ),
        ActionSpec(
            "list_shopping",
            "Shopping list",
            "Reads the shopping list.",
            Risk.READ,
            NoParams,
            _list_items,
        ),
        ActionSpec(
            "watch_price",
            "Price alert",
            "Watches the price of a product page of an allowed shop "
            "(optionally with a target price in euros) and reports price drops.",
            Risk.LOW,
            WatchParams,
            _watch,
            undo=_undo_watch,
            check=_check_watch,
            preview=lambda p, s: f"Price alert for {shop_domain(p.url, s.prices.shops)}",
        ),
        ActionSpec(
            "unwatch_price",
            "Remove price alert",
            "Removes a price alert.",
            Risk.LOW,
            IdParams,
            _unwatch,
            undo=_undo_unwatch,
            check=_check_unwatch,
        ),
        ActionSpec(
            "list_price_watches",
            "Price alerts",
            "Shows all price alerts with their current price.",
            Risk.READ,
            NoParams,
            _list_watches,
            untrusted_output=True,
        ),
        ActionSpec(
            "open_tracking",
            "Track shipment",
            "Opens the carrier's shipment tracking (link from a fixed template).",
            Risk.LOW,
            TrackingParams,
            _open_tracking,
            llm_exposed=False,
        ),
        ActionSpec(
            "list_parcels",
            "Parcels",
            "Shipments from Gmail of the last three weeks (carrier, number, status).",
            Risk.READ,
            NoParams,
            _parcels,
            untrusted_output=True,
        ),
    ]
    for spec in specs:
        registry.register(spec)
