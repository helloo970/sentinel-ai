"""News world tour: assign headlines to the most important countries.

The assignment runs locally via keywords (German and English, since feeds may be
either). Optionally Claude words the texts - without tools, the headlines are marked as
untrusted. `code` is the ISO-3166 number (as in the app's world map).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from sentinel.llm.base import LLMError
from sentinel.llm.untrusted import UNTRUSTED_RULES, wrap_untrusted

if TYPE_CHECKING:
    from sentinel.services import Services

MAX_COUNTRIES = 6
MAX_HEADLINES = 3


@dataclass(frozen=True)
class Country:
    code: str
    name: str
    lon: float
    lat: float
    zoom: float
    keywords: tuple[str, ...]


# Order = priority when the number of headlines is equal
COUNTRIES: tuple[Country, ...] = (
    Country(
        "643",
        "Russia",
        60.0,
        58.0,
        2.2,
        (
            "russland",
            "russisch",
            "russia",
            "russian",
            "moskau",
            "moscow",
            "kreml",
            "kremlin",
            "putin",
        ),
    ),
    Country(
        "804",
        "Ukraine",
        31.5,
        49.0,
        5.0,
        ("ukrain", "kiew", "kyiv", "selenskyj", "zelensky", "charkiw", "kharkiv"),
    ),
    Country(
        "840",
        "USA",
        -98.0,
        39.0,
        2.4,
        (
            "vereinigte staaten",
            "united states",
            "washington",
            "weißes haus",
            "weisses haus",
            "white house",
            "amerikan",
            "american",
            "pentagon",
            "trump",
        ),
    ),
    Country(
        "156",
        "China",
        104.0,
        35.0,
        2.6,
        ("china", "chines", "peking", "beijing", "xi jinping", "taiwan"),
    ),
    Country(
        "276",
        "Germany",
        10.4,
        51.2,
        6.0,
        ("deutschland", "german", "berlin", "bundesregierung", "bundestag", "bundeswehr"),
    ),
    Country(
        "376",
        "Israel",
        35.0,
        31.4,
        9.0,
        ("israel", "gaza", "netanjahu", "netanyahu", "jerusalem", "tel aviv", "hamas"),
    ),
    Country("364", "Iran", 53.7, 32.4, 4.0, ("iran", "teheran", "tehran")),
    Country(
        "826",
        "United Kingdom",
        -2.5,
        54.0,
        6.0,
        (
            "großbritannien",
            "grossbritannien",
            "britisch",
            "britain",
            "british",
            "london",
            "england",
            "starmer",
        ),
    ),
    Country(
        "250",
        "France",
        2.4,
        46.6,
        6.0,
        ("frankreich", "französ", "franzoes", "france", "french", "paris", "macron"),
    ),
    Country(
        "792",
        "Turkey",
        35.2,
        39.0,
        5.0,
        ("türkei", "tuerkei", "türkisch", "turkey", "turkish", "erdogan", "ankara", "istanbul"),
    ),
    Country(
        "356",
        "India",
        79.0,
        22.0,
        3.2,
        ("indien", "indisch", "india", "indian", "neu-delhi", "new delhi", "modi"),
    ),
    Country("392", "Japan", 138.0, 37.0, 5.0, ("japan", "tokio", "tokyo")),
    Country("076", "Brazil", -52.0, -10.0, 2.8, ("brasilien", "brazil", "brasília", "lula")),
)

_PATTERNS = {
    c.code: re.compile(r"\b(" + "|".join(re.escape(k) for k in c.keywords) + r")", re.IGNORECASE)
    for c in COUNTRIES
}
_US = re.compile(r"\b(USA|US)\b")

TOUR_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "intro": {"type": "string"},
        "countries": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"code": {"type": "string"}, "text": {"type": "string"}},
                "required": ["code", "text"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["intro", "countries"],
    "additionalProperties": False,
}

TOUR_SYSTEM = f"""You deliver a short news world tour for text-to-speech, in English.
"intro": one or two sentences on the state of the world. For each country (keep "code"
unchanged) two to three natural sentences, only facts from the headlines, without
judgement, without markdown.

{UNTRUSTED_RULES}"""


def match_countries(text: str) -> list[str]:
    codes = [code for code, pattern in _PATTERNS.items() if pattern.search(text)]
    if _US.search(text) and "840" not in codes:
        codes.append("840")
    return codes


def build_tour(items: list[dict[str, str]]) -> list[dict[str, Any]]:
    """Assign headlines to countries and pick the most important (most headlines)."""
    buckets: dict[str, list[str]] = {}
    for item in items:
        title = item.get("title", "")
        for code in match_countries(f"{title} {item.get('summary', '')}"):
            bucket = buckets.setdefault(code, [])
            if title and title not in bucket:
                bucket.append(title)
    priority = {c.code: index for index, c in enumerate(COUNTRIES)}
    ranked = sorted(buckets, key=lambda code: (-len(buckets[code]), priority[code]))
    tour = []
    for code in ranked[:MAX_COUNTRIES]:
        country = next(c for c in COUNTRIES if c.code == code)
        headlines = buckets[code][:MAX_HEADLINES]
        tour.append(
            {
                "code": country.code,
                "name": country.name,
                "lon": country.lon,
                "lat": country.lat,
                "zoom": country.zoom,
                "headlines": headlines,
                "text": f"{country.name}: " + ". ".join(h.rstrip(".") for h in headlines) + ".",
            }
        )
    return tour


class NewsTour:
    def __init__(self, svc: Services) -> None:
        self.svc = svc

    async def _attach_weather(self, result: dict[str, Any]) -> None:
        """Weather for home and the tour's countries (best effort, errors don't matter)."""
        places = [(c["name"], c["lat"], c["lon"]) for c in result["countries"]]
        try:
            home = await self.svc.weather.home()
        except Exception:
            home = None
        if home:
            places.append(home)
        weather = await self.svc.weather.places(places)
        by_name = {w["name"]: w for w in weather}
        for country in result["countries"]:
            if country["name"] in by_name:
                country["weather"] = by_name[country["name"]]
        if home and home[0] in by_name:
            result["home_weather"] = by_name[home[0]]

    async def tour(self) -> dict[str, Any]:
        data = await self.svc.news.headlines()
        items = [i for f in data["feeds"] for i in f["items"]]
        countries = build_tour(items)
        world = [i["title"] for f in data["feeds"] if f["region"] != "de" for i in f["items"]]
        intro = "Here is the state of the world." if countries else "I couldn't fetch any news."
        result = {"intro": intro, "countries": countries, "summarized": False, "world": world[:8]}
        await self._attach_weather(result)
        ok, _ = self.svc.costs.check()
        if not countries or not self.svc.llm.available or not ok or self.svc.config.ai_only_in_chat:
            return result
        payload = [
            {"code": c["code"], "country": c["name"], "headlines": c["headlines"]}
            for c in countries
        ]
        prompt = "Create the world tour.\n" + wrap_untrusted(
            "news", {"countries": payload, "more_world_headlines": world[:8]}
        )
        try:
            answer = await self.svc.llm.complete(
                system=TOUR_SYSTEM, prompt=prompt, max_tokens=3000, json_schema=TOUR_SCHEMA
            )
        except LLMError:
            return result
        self.svc.costs.record(answer.model, "news_tour", answer.usage)
        try:
            parsed = json.loads(answer.text)
        except json.JSONDecodeError:
            return result
        texts = {
            str(c.get("code")): str(c.get("text", ""))[:600]
            for c in parsed.get("countries", [])
            if isinstance(c, dict)
        }
        for country in countries:
            if texts.get(country["code"]):
                country["text"] = texts[country["code"]]
        result["intro"] = str(parsed.get("intro") or intro)[:600]
        result["summarized"] = True
        return result
