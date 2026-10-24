"""Open web pages: fixed sites (Gmail, YouTube ...) and searches in the default browser.

Jarvis only picks a name or a search term. SENTINEL builds the address itself from
a fixed list, so no text from emails or web pages can lead to foreign sites.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.parse import quote, quote_plus

SITES: dict[str, tuple[str, str]] = {
    "gmail": ("Gmail", "https://mail.google.com/"),
    "google": ("Google", "https://www.google.com/"),
    "youtube": ("YouTube", "https://www.youtube.com/"),
    "maps": ("Google Maps", "https://www.google.com/maps"),
    "kalender": ("Google Calendar", "https://calendar.google.com/"),
    "drive": ("Google Drive", "https://drive.google.com/"),
    "wikipedia": ("Wikipedia", "https://en.wikipedia.org/"),
    "github": ("GitHub", "https://github.com/"),
    "netflix": ("Netflix", "https://www.netflix.com/"),
    "spotify": ("Spotify Web", "https://open.spotify.com/"),
    "amazon": ("Amazon", "https://www.amazon.de/"),
    "wetter": ("Weather", "https://www.wetter.com/"),
    "tagesschau": ("Tagesschau", "https://www.tagesschau.de/"),
    "whatsapp": ("WhatsApp Web", "https://web.whatsapp.com/"),
    "twitch": ("Twitch", "https://www.twitch.tv/"),
    "instagram": ("Instagram", "https://www.instagram.com/"),
    "tiktok": ("TikTok", "https://www.tiktok.com/"),
    "chatgpt": ("ChatGPT", "https://chatgpt.com/"),
    "claude": ("Claude", "https://claude.ai/"),
    # Live news (following the world situation)
    "trt_haber": ("TRT Haber live", "https://www.trthaber.com/canli-yayin-izle.html"),
    "tagesschau24": ("tagesschau24 live", "https://www.tagesschau.de/multimedia/livestreams"),
}

SEARCH: dict[str, tuple[str, str]] = {
    "google": ("Google", "https://www.google.com/search?q={q}"),
    "youtube": ("YouTube", "https://www.youtube.com/results?search_query={q}"),
    "maps": ("Google Maps", "https://www.google.com/maps/search/{q}"),
    "wikipedia": ("Wikipedia", "https://en.wikipedia.org/w/index.php?search={q}"),
}

ALIASES = {
    "google mail": "gmail",
    "mail": "gmail",
    "mails": "gmail",
    "you tube": "youtube",
    "google maps": "maps",
    "karte": "maps",
    "map": "maps",
    "calendar": "kalender",
    "weather": "wetter",
    "google calendar": "kalender",
    "kalender": "kalender",
    "google drive": "drive",
    "whatsapp web": "whatsapp",
    "trt": "trt_haber",
    "trt haber": "trt_haber",
    "tagesschau 24": "tagesschau24",
    "insta": "instagram",
    "tik tok": "tiktok",
    "chat gpt": "chatgpt",
}

# Sites with a login: always in the default browser, where you are already logged in.
# (Google blocks sign-ins in embedded app windows.)
LOGIN_SITES = {
    "gmail",
    "kalender",
    "drive",
    "youtube",
    "github",
    "netflix",
    "spotify",
    "amazon",
    "whatsapp",
    "twitch",
    "instagram",
    "tiktok",
    "chatgpt",
    "claude",
}
# Google services that can open a specific account (?authuser=...)
GOOGLE_ACCOUNT_URLS = {
    "gmail": "https://mail.google.com/mail/u/?authuser={account}",
    "kalender": "https://calendar.google.com/calendar/u/?authuser={account}",
    "drive": "https://drive.google.com/drive/u/?authuser={account}",
}
_EMAIL = re.compile(r"^[A-Za-z0-9._%+\-]{1,64}@[A-Za-z0-9.\-]{1,190}\.[A-Za-z]{2,24}$")


def valid_account(value: str) -> bool:
    return bool(_EMAIL.match(value.strip()))


def load_choice(config_dir: Path) -> dict[str, str]:
    try:
        data = json.loads((config_dir / "web.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {k: str(v) for k, v in data.items()} if isinstance(data, dict) else {}


def save_choice(config_dir: Path, **values: str) -> None:
    config_dir.mkdir(parents=True, exist_ok=True)
    data = load_choice(config_dir)
    data.update(values)
    (config_dir / "web.json").write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def site_url(key: str, config_dir: Path | None = None) -> str:
    """Address of a known site; for Google services with the chosen account."""
    account = load_choice(config_dir).get("google_account", "") if config_dir else ""
    if account and key in GOOGLE_ACCOUNT_URLS and valid_account(account):
        return GOOGLE_ACCOUNT_URLS[key].format(account=quote(account, safe="@"))
    return SITES[key][1]


def resolve_site(name: str) -> str | None:
    key = name.strip().lower()
    key = ALIASES.get(key, key)
    return key if key in SITES else None


def search_url(engine: str, query: str) -> str:
    return SEARCH[engine][1].format(q=quote_plus(query.strip()))
