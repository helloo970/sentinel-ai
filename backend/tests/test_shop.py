"""Order suggestions: shop allowlist, research without ordering, opening only with PIN."""

from __future__ import annotations

import json

import pytest

from sentinel.permissions import Risk, Source
from sentinel.services import Services
from sentinel.shop.service import ShopError, parse_product, shop_domain

SHOPS = ["amazon.de", "otto.de"]
PIN = "482913"

PRODUCT = {
    "title": "Headphones X",
    "price": 129.99,
    "currency": "EUR",
    "shop": "Amazon",
    "url": "https://www.amazon.de/dp/B0TEST",
    "summary": "Wireless headphones with noise cancelling.",
    "specs": ["Bluetooth 5.3", "30 h Akku"],
    "availability": "in stock",
}


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://www.amazon.de/dp/1", "amazon.de"),
        ("https://otto.de/p/1", "otto.de"),
        ("http://www.amazon.de/dp/1", None),
        ("https://amazon.de.evil.example/dp/1", None),
        ("https://evilamazon.de/dp/1", None),
        ("https://user:pw@www.amazon.de/dp/1", None),
        ("https://www.amazon.de:8443/dp/1", None),
        ("javascript:alert(1)", None),
        ("", None),
    ],
)
def test_shop_domain_allowlist(url: str, expected: str | None) -> None:
    assert shop_domain(url, SHOPS) == expected


def test_parse_product_from_text_with_noise() -> None:
    text = "Here is the result:\n" + json.dumps(PRODUCT) + "\nHave fun"
    product = parse_product(text, SHOPS)
    assert product["shop"] == "amazon.de" and product["price"] == 129.99
    with pytest.raises(ShopError):
        parse_product(json.dumps({**PRODUCT, "url": "https://phish.example/amazon"}), SHOPS)
    with pytest.raises(ShopError):
        parse_product("no json", SHOPS)


def test_research_creates_proposal_but_orders_nothing(svc: Services, scripted) -> None:
    scripted.completions = [json.dumps(PRODUCT)]
    outcome = svc.engine.request(
        "shop_research", {"query": "good headphones"}, source=Source.ASSISTANT, actor="jarvis"
    )
    assert outcome.status == "executed" and outcome.untrusted
    assert scripted.calls[0]["research"] == svc.shop.shops
    orders = svc.shop.list()
    assert len(orders) == 1 and orders[0]["status"] == "proposed"


def test_research_rejects_secrets_and_offline(svc: Services) -> None:
    outcome = svc.engine.request(
        "shop_research", {"query": "passwort: hunter22 kaufen"}, source=Source.USER, actor="user"
    )
    assert outcome.status == "denied"


def test_open_order_always_needs_confirmation_and_pin(svc: Services, scripted, monkeypatch) -> None:
    opened: list[str] = []
    monkeypatch.setattr("webbrowser.open", lambda url, new=0: opened.append(url) or True)
    svc.pin.set_pin(PIN)
    scripted.completions = [json.dumps(PRODUCT)]
    order = svc.shop.list() or None
    svc.engine.request("shop_research", {"query": "headphones"}, source=Source.USER, actor="user")
    order = svc.shop.list()[0]
    spec = svc.registry.get("open_order")
    assert spec.risk is Risk.FINAL and spec.requires_pin

    svc.engine.set_autonomy(3, actor="user")
    for source in (Source.USER, Source.ASSISTANT):
        pending = svc.engine.request(
            "open_order", {"order_id": order["id"]}, source=source, actor="x"
        )
        assert pending.status == "pending"
    assert opened == []
    approval = svc.engine.list_approvals()[0]
    assert approval["requires_pin"] is True
    assert svc.engine.approve(approval["id"]).status == "denied"
    assert svc.engine.approve(approval["id"], pin="000000").status == "denied"
    assert svc.engine.approve(approval["id"], pin=PIN).status == "executed"
    assert opened == [PRODUCT["url"]]
    assert svc.shop.get(order["id"])["status"] == "opened"


def test_open_order_rechecks_allowlist(svc: Services, monkeypatch) -> None:
    monkeypatch.setattr("webbrowser.open", lambda url, new=0: True)
    order_id = svc.db.execute(
        "INSERT INTO orders(ts, query, data) VALUES(0, 'x', ?)",
        (json.dumps({**PRODUCT, "url": "https://phish.example/x", "shop": "phish.example"}),),
    )
    outcome = svc.engine.request(
        "open_order", {"order_id": order_id}, source=Source.USER, actor="user"
    )
    assert outcome.status == "denied"
