"""Weather via Open-Meteo (free, no account). Place from SENTINEL_HOME_CITY or coordinates."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

import httpx

if TYPE_CHECKING:
    from sentinel.services import Services

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
CACHE_S = 900
MAX_PLACES = 12

# WMO weather codes -> English
WMO: dict[int, str] = {
    0: "clear",
    1: "mostly clear",
    2: "partly cloudy",
    3: "overcast",
    45: "fog",
    48: "rime fog",
    51: "light drizzle",
    53: "drizzle",
    55: "heavy drizzle",
    56: "freezing drizzle",
    57: "freezing drizzle",
    61: "light rain",
    63: "rain",
    65: "heavy rain",
    66: "freezing rain",
    67: "freezing rain",
    71: "light snow",
    73: "snow",
    75: "heavy snow",
    77: "snow grains",
    80: "rain showers",
    81: "rain showers",
    82: "violent rain showers",
    85: "snow showers",
    86: "heavy snow showers",
    95: "thunderstorm",
    96: "thunderstorm with hail",
    99: "thunderstorm with hail",
}


class WeatherError(RuntimeError):
    pass


def describe_code(code: Any) -> str:
    try:
        return WMO.get(int(code), "unknown")
    except (TypeError, ValueError):
        return "unknown"


def parse_forecast(raw: dict[str, Any]) -> dict[str, Any]:
    current = raw.get("current") or {}
    daily = raw.get("daily") or {}

    def first(key: str) -> Any:
        values = daily.get(key) or []
        return values[0] if values else None

    return {
        "temperature": current.get("temperature_2m"),
        "wind": current.get("wind_speed_10m"),
        "code": current.get("weather_code"),
        "text": describe_code(current.get("weather_code")),
        "max": first("temperature_2m_max"),
        "min": first("temperature_2m_min"),
        "rain_chance": first("precipitation_probability_max"),
    }


def spoken(place: str, weather: dict[str, Any]) -> str:
    if weather.get("temperature") is None:
        return f"I have no weather for {place} right now."
    text = f"In {place} it is {weather['text']} at {round(weather['temperature'])} degrees"
    if weather.get("max") is not None and weather.get("min") is not None:
        text += f", today {round(weather['min'])} to {round(weather['max'])} degrees"
    if weather.get("rain_chance"):
        text += f", {weather['rain_chance']} percent chance of rain"
    return text + "."


class WeatherService:
    def __init__(self, svc: Services) -> None:
        self.svc = svc
        self._cache: dict[str, tuple[float, Any]] = {}

    async def _get_json(self, url: str, params: dict[str, Any]) -> Any:
        async with httpx.AsyncClient(
            timeout=10, headers={"User-Agent": "SENTINEL-AI/0.1"}
        ) as client:
            response = await client.get(url, params=params)
            response.raise_for_status()
            return response.json()

    async def _cached(self, key: str, loader: Any) -> Any:
        hit = self._cache.get(key)
        if hit and time.time() - hit[0] < CACHE_S:
            return hit[1]
        value = await loader()
        self._cache[key] = (time.time(), value)
        return value

    async def home(self) -> tuple[str, float, float] | None:
        cfg = self.svc.config
        if not cfg.weather_enabled:
            return None
        # Home place from the in-app setup takes precedence over the .env
        city = self.svc.store.get("home_city") or cfg.home_city
        if (
            cfg.home_lat is not None
            and cfg.home_lon is not None
            and not self.svc.store.get("home_city")
        ):
            return (city or "Home", cfg.home_lat, cfg.home_lon)
        if not city:
            return None
        stored = self.svc.store.get(f"geocode:{city}")
        if stored:
            lat, lon = (float(x) for x in stored.split(","))
            return (city, lat, lon)
        data = await self._get_json(GEOCODE_URL, {"name": city, "count": 1, "language": "en"})
        results = data.get("results") or []
        if not results:
            raise WeatherError(f"Place '{city}' not found")
        lat, lon = float(results[0]["latitude"]), float(results[0]["longitude"])
        self.svc.store.set(f"geocode:{city}", f"{lat},{lon}")
        return (city, lat, lon)

    async def forecast(self, lat: float, lon: float) -> dict[str, Any]:
        async def load() -> dict[str, Any]:
            raw = await self._get_json(
                FORECAST_URL,
                {
                    "latitude": round(lat, 3),
                    "longitude": round(lon, 3),
                    "timezone": "auto",
                    "forecast_days": 1,
                    "current": "temperature_2m,weather_code,wind_speed_10m",
                    "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max",
                },
            )
            return parse_forecast(raw)

        return await self._cached(f"{lat:.2f},{lon:.2f}", load)

    async def home_weather(self) -> dict[str, Any]:
        try:
            place = await self.home()
            if place is None:
                return {"configured": False}
            name, lat, lon = place
            weather = await self.forecast(lat, lon)
        except (httpx.HTTPError, WeatherError, ValueError, KeyError) as exc:
            return {"configured": True, "error": str(exc) or type(exc).__name__}
        return {"configured": True, "place": name, **weather, "spoken": spoken(name, weather)}

    async def places(self, places: list[tuple[str, float, float]]) -> list[dict[str, Any]]:
        """Current weather for several places in one request (world map)."""
        places = places[:MAX_PLACES]
        if not places or not self.svc.config.weather_enabled:
            return []
        key = "multi:" + "|".join(f"{lat:.1f},{lon:.1f}" for _, lat, lon in places)

        async def load() -> list[dict[str, Any]]:
            raw = await self._get_json(
                FORECAST_URL,
                {
                    "latitude": ",".join(str(round(p[1], 2)) for p in places),
                    "longitude": ",".join(str(round(p[2], 2)) for p in places),
                    "current": "temperature_2m,weather_code",
                    "timezone": "auto",
                },
            )
            entries = raw if isinstance(raw, list) else [raw]
            result = []
            for (name, lat, lon), entry in zip(places, entries, strict=False):
                current = entry.get("current") or {}
                result.append(
                    {
                        "name": name,
                        "lat": lat,
                        "lon": lon,
                        "temperature": current.get("temperature_2m"),
                        "text": describe_code(current.get("weather_code")),
                    }
                )
            return result

        try:
            return await self._cached(key, load)
        except (httpx.HTTPError, ValueError):
            return []
