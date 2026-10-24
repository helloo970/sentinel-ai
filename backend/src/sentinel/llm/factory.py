"""Picks the matching LLM provider based on the configuration.

Order: Claude → OpenRouter → Google Gemini → offline.
Keys live in the Windows credential vault ("sentinel claude/openrouter/gemini login") or in .env.
"""

from __future__ import annotations

from typing import Any

from sentinel.config import Settings
from sentinel.llm.base import LLMProvider
from sentinel.llm.offline import OfflineProvider

GEMINI_KEYRING = ("SentinelAI-Gemini", "api_key")
OPENROUTER_KEYRING = ("SentinelAI-OpenRouter", "api_key")
CLAUDE_KEYRING = ("SentinelAI-Claude", "api_key")


def _keyring() -> Any:
    import keyring

    return keyring


def _stored(entry: tuple[str, str]) -> str | None:
    try:
        return _keyring().get_password(*entry)
    except Exception:
        return None


def stored_gemini_key() -> str | None:
    return _stored(GEMINI_KEYRING)


def openrouter_key(config: Settings) -> str | None:
    if config.openrouter_api_key and config.openrouter_api_key.get_secret_value():
        return config.openrouter_api_key.get_secret_value()
    return _stored(OPENROUTER_KEYRING)


def claude_key(config: Settings) -> str | None:
    if config.anthropic_api_key and config.anthropic_api_key.get_secret_value():
        return config.anthropic_api_key.get_secret_value()
    return _stored(CLAUDE_KEYRING)


def _choice(config: Settings, name: str) -> str:
    import json

    try:
        data = json.loads((config.config_dir / "llm.json").read_text(encoding="utf-8"))
        return str(data.get(name) or "")
    except (OSError, ValueError, AttributeError):
        return ""


def claude_model(config: Settings) -> str:
    """Claude model: choice via "sentinel claude model" (config/llm.json) before .env."""
    return _choice(config, "claude_model") or config.llm_model


def gemini_key(config: Settings) -> str | None:
    if config.gemini_api_key and config.gemini_api_key.get_secret_value():
        return config.gemini_api_key.get_secret_value()
    return stored_gemini_key()


def chosen_model(config: Settings) -> str:
    """OpenRouter model: choice via "sentinel openrouter model" (config/llm.json) before .env."""
    return _choice(config, "openrouter_model") or config.openrouter_model


def save_model(config: Settings, model: str, *, name: str = "openrouter_model") -> None:
    import json

    config.config_dir.mkdir(parents=True, exist_ok=True)
    path = config.config_dir / "llm.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    data[name] = model
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def create_provider(config: Settings) -> LLMProvider:
    key = claude_key(config)
    if key:
        from sentinel.llm.anthropic_provider import AnthropicProvider

        return AnthropicProvider(
            api_key=key,
            model=claude_model(config),
            effort=config.llm_effort,
            fallbacks=config.llm_fallbacks,
        )
    key = openrouter_key(config)
    if key:
        from sentinel.llm.openrouter_provider import OpenRouterProvider

        return OpenRouterProvider(
            api_key=key, model=chosen_model(config), reasoning=config.openrouter_reasoning
        )
    key = gemini_key(config)
    if key:
        from sentinel.llm.gemini_provider import GeminiProvider

        return GeminiProvider(api_key=key, model=config.gemini_model)
    return OfflineProvider()
