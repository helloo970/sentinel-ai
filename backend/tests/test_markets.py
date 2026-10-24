"""Markets, KPIs, voice commands for views, web pages in the app window."""

from __future__ import annotations

import httpx

from sentinel.chat import quick
from sentinel.markets import kpis
from sentinel.markets.service import (
    MarketService,
    parse_crypto,
    parse_fx,
    parse_yahoo,
    spoken_summary,
)
from sentinel.permissions.models import Source
from sentinel.services import Services

CRYPTO = [
    {
        "id": "bitcoin",
        "current_price": 61234.5,
        "price_change_percentage_24h": 2.13,
        "sparkline_in_7d": {"price": [60000, 60500, None, 61234.5]},
    },
    {"id": "evilcoin", "current_price": 1},
]
YAHOO = {
    "chart": {
        "result": [
            {
                "meta": {"regularMarketPrice": 19500.0, "currency": "EUR"},
                "indicators": {"quote": [{"close": [19000.0, None, 19500.0]}]},
            }
        ]
    }
}
FX = {"rates": {"2026-10-01": {"USD": 1.05, "TRY": 36.0}, "2026-10-22": {"USD": 1.08, "TRY": 38.2}}}


def test_parsers_take_only_numbers_and_known_names() -> None:
    crypto = parse_crypto(CRYPTO)
    assert [c["symbol"] for c in crypto] == ["BTC"]
    assert crypto[0]["series"] == [60000, 60500, 61234.5]
    dax = parse_yahoo("^GDAXI", "DAX", "DAX", YAHOO)
    assert dax and dax["price"] == 19500.0 and dax["change_pct"] == 2.63
    assert parse_yahoo("x", "x", "x", {"chart": {}}) is None
    fx = {i["symbol"]: i for i in parse_fx(FX)}
    assert fx["EUR/USD"]["price"] == 1.08 and fx["EUR/TRY"]["change_pct"] == 6.11


def test_spoken_summary() -> None:
    data = {
        "groups": [
            {"items": parse_crypto(CRYPTO)},
            {"items": [parse_yahoo("^GDAXI", "DAX", "DAX", YAHOO)]},
            {"items": parse_fx(FX)},
        ]
    }
    text = spoken_summary(data)
    assert "Bitcoin is at 61,234 euros, up 2.1 percent" in text
    assert "One euro costs 1.08 dollars" in text
    assert spoken_summary({"groups": []}).startswith("The market data")


async def test_service_fetches_all_groups_and_caches(svc: Services) -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.host)
        if "coingecko" in request.url.host:
            return httpx.Response(200, json=CRYPTO)
        if "yahoo" in request.url.host:
            return httpx.Response(200, json=YAHOO)
        return httpx.Response(200, json=FX)

    service = MarketService(svc, client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    data = await service.overview()
    assert [len(g["items"]) for g in data["groups"]] == [1, 6, 3] and not data["errors"]
    before = len(calls)
    await service.overview()
    assert len(calls) == before  # from the cache


async def test_service_reports_unreachable_sources(svc: Services) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    service = MarketService(svc, client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    data = await service.overview()
    assert data["errors"] == ["Crypto", "Stocks & indices", "Currencies"]


def test_kpis_cover_all_areas(svc: Services) -> None:
    svc.shopping.add("Milk")
    tiles = {t["key"]: t for t in kpis.collect(svc)}
    assert {"alerts", "cpu", "devices", "ai_today", "reminders", "shopping", "brain"} <= set(tiles)
    assert tiles["shopping"]["value"] == 1
    assert "No open alerts" in kpis.spoken(list(tiles.values()))


def test_voice_commands_for_views() -> None:
    assert quick.match("Show me the markets") == quick.QuickCommand(
        "open_view", {"view": "markets"}
    )
    assert quick.match("Jarvis, show me the numbers").params == {"view": "kpis"}
    assert quick.match("Briefing").params == {"view": "world", "tab": "briefing"}
    assert quick.match("What is going on in the world?").params == {"view": "world", "tab": "tour"}
    assert quick.match("Back").params == {"view": "dashboard"}
    assert quick.match("Close that").params == {"view": "dashboard"}


def test_open_site_in_app_window(svc: Services, monkeypatch) -> None:
    seen: list[tuple[str, dict]] = []
    monkeypatch.setattr(type(svc.bus), "listeners", property(lambda self: 1))
    original = svc.bus.publish
    monkeypatch.setattr(
        svc.bus,
        "publish",
        lambda kind, data=None: (seen.append((kind, data)), original(kind, data)),
    )
    opened: list[str] = []
    monkeypatch.setattr("webbrowser.open", lambda url, new=0: opened.append(url) or True)
    outcome = svc.engine.request(
        "open_site", {"name": "wikipedia"}, source=Source.USER, actor="user"
    )
    assert outcome.status == "executed" and not opened
    assert any(kind == "open_web" and data["url"].startswith("https://") for kind, data in seen)


def test_markets_action_is_untrusted(svc: Services, monkeypatch) -> None:
    import time

    svc.markets._cache = (time.time(), {"groups": [{"items": parse_crypto(CRYPTO)}], "errors": []})
    outcome = svc.engine.request("markets_overview", {}, source=Source.ASSISTANT, actor="jarvis")
    assert outcome.status == "executed" and outcome.untrusted and "Bitcoin" in outcome.message


def test_login_sites_open_in_browser_with_account(svc: Services, monkeypatch) -> None:
    from sentinel.automation import web

    monkeypatch.setattr(type(svc.bus), "listeners", property(lambda self: 1))
    opened: list[str] = []
    monkeypatch.setattr("webbrowser.open", lambda url, new=0: opened.append(url) or True)
    web.save_choice(svc.config.config_dir, google_account="user@example.com")
    outcome = svc.engine.request("open_site", {"name": "gmail"}, source=Source.USER, actor="user")
    assert outcome.status == "executed"
    assert opened == ["https://mail.google.com/mail/u/?authuser=user@example.com"]
    assert not web.valid_account("no-account")
    assert web.resolve_site("insta") == "instagram"


async def test_ai_only_in_chat_keeps_background_free_of_llm(
    svc: Services, scripted, monkeypatch
) -> None:
    async def fake_headlines():
        return {
            "feeds": [
                {"name": "Test", "region": "de", "items": [{"title": "Merz in Berlin", "link": ""}]}
            ]
        }

    monkeypatch.setattr(svc.news, "headlines", fake_headlines)
    assert svc.config.ai_only_in_chat
    await svc.tour.tour()
    await svc.news.briefing()
    assert not svc.brain.can_learn()
    assert scripted.calls == []  # no language model outside the conversation


def test_night_quiet_hours(svc: Services) -> None:
    from datetime import datetime

    n = svc.notifier
    assert n.quiet_hours() == "23:00-07:00"
    assert n.night(datetime(2026, 10, 24, 23, 30)) and n.night(datetime(2026, 10, 24, 6, 59))
    assert not n.night(datetime(2026, 10, 24, 7, 0)) and not n.night(datetime(2026, 10, 24, 22, 0))
    svc.store.set("quiet_hours", "")
    assert not n.night(datetime(2026, 10, 24, 23, 30))


def test_setup_api_and_profile_upload(svc: Services) -> None:
    from fastapi.testclient import TestClient

    from sentinel.api.app import create_app

    api = TestClient(
        create_app(svc, token="t-1", start_background=False), base_url="http://127.0.0.1:8765"
    )
    h = {"Authorization": "Bearer t-1"}
    state = api.get("/setup", headers=h).json()
    assert state["quiet_hours"] == "23:00-07:00" and state["profile"]["saved"] is False
    r = api.put(
        "/setup", json={"home_city": "Exampletown", "quiet_hours": "22:30-06:30"}, headers=h
    )
    assert r.status_code == 200 and r.json()["home_city"] == "Exampletown"
    assert api.put("/setup", json={"quiet_hours": "late"}, headers=h).status_code == 400
    assert api.put("/setup", json={"google_account": "nope"}, headers=h).status_code == 400
    r = api.post(
        "/brain/profile", json={"nodes": [{"label": "Test profile", "category": "ich"}]}, headers=h
    )
    assert r.json()["nodes"] == 1
    assert api.get("/setup", headers=h).json()["profile"] == {
        "saved": True,
        "nodes": 1,
        "reminders": 0,
    }
    assert api.post("/brain/profile/reload", headers=h).status_code == 200
    status = api.get("/status", headers=h).json()
    assert "warnings" in status and "night" in status
