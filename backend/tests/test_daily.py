"""Morning briefing, weather, calendar, daily report, conversation mode."""

from __future__ import annotations

import time
from datetime import datetime

from sentinel.monitor.rules import Finding
from sentinel.permissions import Severity, Source
from sentinel.services import Services
from sentinel.weather.service import describe_code, parse_forecast, spoken


def test_weather_parsing_and_speech() -> None:
    raw = {
        "current": {"temperature_2m": 12.4, "weather_code": 61, "wind_speed_10m": 9},
        "daily": {
            "temperature_2m_max": [15.1],
            "temperature_2m_min": [7.8],
            "precipitation_probability_max": [70],
        },
    }
    weather = parse_forecast(raw)
    assert weather["text"] == "light rain" and weather["max"] == 15.1
    assert spoken("Cologne", weather) == (
        "In Cologne it is light rain at 12 degrees, today 8 to 15 degrees, 70 percent chance of rain."
    )
    assert describe_code("x") == "unknown"
    assert "no weather" in spoken("Cologne", parse_forecast({}))


async def test_home_weather_geocodes_once(svc: Services, monkeypatch) -> None:
    svc.config.weather_enabled = True
    svc.config.home_city = "Cologne"
    calls: list[str] = []

    async def fake(url, params):
        calls.append(url)
        if "geocoding" in url:
            return {"results": [{"latitude": 50.94, "longitude": 6.96}]}
        return {"current": {"temperature_2m": 20, "weather_code": 0}, "daily": {}}

    monkeypatch.setattr(svc.weather, "_get_json", fake)
    first = await svc.weather.home_weather()
    svc.weather._cache.clear()
    await svc.weather.home_weather()
    assert first["place"] == "Cologne" and first["text"] == "clear"
    assert sum("geocoding" in c for c in calls) == 1  # coordinates are remembered


async def test_weather_places_multi(svc: Services, monkeypatch) -> None:
    svc.config.weather_enabled = True

    async def fake(url, params):
        assert params["latitude"] == "55.0,40.0"
        return [
            {"current": {"temperature_2m": -3, "weather_code": 71}},
            {"current": {"temperature_2m": 18, "weather_code": 2}},
        ]

    monkeypatch.setattr(svc.weather, "_get_json", fake)
    result = await svc.weather.places([("Russia", 55.0, 37.0), ("USA", 40.0, -98.0)])
    assert [(r["name"], r["temperature"], r["text"]) for r in result] == [
        ("Russia", -3, "light snow"),
        ("USA", 18, "partly cloudy"),
    ]


async def test_weather_disabled_or_unset(svc: Services) -> None:
    assert (await svc.weather.home_weather()) == {"configured": False}
    assert await svc.weather.places([("X", 1.0, 2.0)]) == []


def test_first_wake_today_only_once(svc: Services) -> None:
    morning = datetime.now().replace(hour=7, minute=0).timestamp()
    night = datetime.now().replace(hour=2, minute=0).timestamp()
    assert svc.reports.is_first_wake_today(night) is False
    assert svc.reports.is_first_wake_today(morning) is True
    assert svc.reports.is_first_wake_today(morning + 60) is False


async def test_morning_briefing_combines_parts(svc: Services, monkeypatch) -> None:
    async def weather():
        return {"configured": True, "spoken": "In Cologne it is clear at 20 degrees."}

    monkeypatch.setattr(svc.weather, "home_weather", weather)
    svc.alerts.add(
        Finding(rule="r", severity=Severity.CRITICAL, title="t", detail={}, fingerprint="f1")
    )
    result = await svc.reports.morning()
    assert result["text"].startswith(("Good morning", "Hello", "Good evening"))
    assert "In Cologne it is clear" in result["text"]
    assert "1 critical alerts" in result["parts"]["security"]
    assert result["text"].endswith("Now the world briefing.")
    assert "mail" not in result["parts"]


def test_daily_report_counts_today(svc: Services) -> None:
    svc.alerts.add(
        Finding(rule="r", severity=Severity.WARN, title="New port", detail={}, fingerprint="w1")
    )
    svc.engine.request(
        "set_reminder", {"text": "Tea", "minutes": 5}, source=Source.ASSISTANT, actor="jarvis"
    )
    svc.engine.request("run_shell", {}, source=Source.ASSISTANT, actor="jarvis")
    now = time.time()
    svc.db.execute(
        "INSERT INTO devices(mac, ip, first_seen, last_seen) VALUES(?,?,?,?)",
        ("aa:bb:cc:00:00:01", "10.0.0.9", now, now),
    )
    report = svc.reports.daily_report(notify=False)
    data = report["data"]
    assert data["alerts"]["warn"] == 1 and data["top_alerts"] == ["New port"]
    assert data["executed"] >= 1 and data["autonomous"] == 1 and data["denied"] == 1
    assert data["new_devices"] == 1
    assert "New devices on the home network: 1." in report["body"]
    assert svc.agents.reports()[0]["agent"] == "daily_report"


def test_evening_due_once_after_time(svc: Services) -> None:
    svc.config.evening_report_time = "21:00"
    before = datetime.now().replace(hour=20, minute=59).timestamp()
    after = datetime.now().replace(hour=21, minute=5).timestamp()
    assert svc.reports.evening_due(before) is False
    assert svc.reports.evening_due(after) is True
    assert svc.reports.evening_due(after + 60) is False


def test_followup_window_after_answer(svc: Services) -> None:
    voice = svc.voice
    voice.voice_active = True
    voice._followup_armed = True
    voice._set_state("idle")
    assert voice.in_followup()
    voice.end_followup()
    assert not voice.in_followup()
    voice.voice_active = False
    voice.followup_until = time.time() + 30
    assert not voice.in_followup()  # no conversation mode without speech recognition


def test_farewell_ends_followup() -> None:
    from sentinel.voice.service import FAREWELL

    assert FAREWELL.search("Thanks Jarvis")
    assert FAREWELL.search("ok that's all")
    assert not FAREWELL.search(
        "What is the weather like at the weekend"
    )  # "end" only as a whole word
