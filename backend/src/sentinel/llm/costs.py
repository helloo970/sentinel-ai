"""Cost estimate and daily/monthly limit for the Claude API."""

from __future__ import annotations

import time
from datetime import datetime
from typing import Any

from sentinel.config import Settings
from sentinel.db import Database
from sentinel.llm.base import Usage

# USD per 1M tokens (input, output)
PRICES_USD: dict[str, tuple[float, float]] = {
    "claude-fable-5-1": (10.0, 50.0),
    "claude-opus-5-5": (4.0, 20.0),
    "claude-opus-5": (5.0, 25.0),
    "claude-sonnet-5-5": (2.0, 10.0),
    "claude-sonnet-5": (2.0, 10.0),
    "claude-haiku-5-5": (0.10, 0.50),
    "claude-haiku-4-5": (1.0, 5.0),
    # OpenRouter (same prices as the provider)
    "anthropic/claude-opus-5.5": (4.0, 20.0),
    "anthropic/claude-sonnet-5.5": (2.0, 10.0),
    "anthropic/claude-haiku-5.5": (0.10, 0.50),
    # Gemini: estimate for the paid tier (the free tier costs nothing)
    "gemini-flash-latest": (0.30, 2.50),
    "gemini-flash-lite-latest": (0.10, 0.40),
    "gemini-pro-latest": (1.25, 10.0),
}
DEFAULT_PRICE = (5.0, 25.0)


def estimate_eur(model: str, usage: Usage, usd_to_eur: float) -> float:
    if model.endswith(":free"):
        return 0.0  # free OpenRouter models
    price_in, price_out = PRICES_USD.get(model, DEFAULT_PRICE)
    usd = (
        usage.input_tokens * price_in
        + usage.output_tokens * price_out
        + usage.cache_read_tokens * price_in * 0.1
        + usage.cache_write_tokens * price_in * 1.25
    ) / 1_000_000
    return usd * usd_to_eur


class CostTracker:
    def __init__(self, db: Database, config: Settings) -> None:
        self.db = db
        self.config = config

    def record(self, model: str, purpose: str, usage: Usage) -> float:
        cost = estimate_eur(model, usage, self.config.usd_to_eur)
        if model in ("offline", "scripted") and purpose != "test":
            cost = 0.0
        self.db.execute(
            "INSERT INTO usage(ts, model, purpose, input_tokens, output_tokens, cost_eur) "
            "VALUES(?,?,?,?,?,?)",
            (
                time.time(),
                model,
                purpose,
                usage.input_tokens + usage.cache_read_tokens + usage.cache_write_tokens,
                usage.output_tokens,
                cost,
            ),
        )
        return cost

    def _sum_since(self, since: float) -> float:
        row = self.db.query_one(
            "SELECT COALESCE(SUM(cost_eur), 0) AS s FROM usage WHERE ts >= ?", (since,)
        )
        return float(row["s"]) if row else 0.0

    def today_eur(self) -> float:
        start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        return self._sum_since(start.timestamp())

    def month_eur(self) -> float:
        start = datetime.now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        return self._sum_since(start.timestamp())

    def check(self) -> tuple[bool, str]:
        today, month = self.today_eur(), self.month_eur()
        if today >= self.config.budget_daily_eur:
            return False, f"Daily limit of €{self.config.budget_daily_eur:.2f} reached"
        if month >= self.config.budget_monthly_eur:
            return False, f"Monthly limit of €{self.config.budget_monthly_eur:.2f} reached"
        return True, ""

    def model_name(self) -> str:
        cfg = self.config
        from sentinel.llm import factory

        if cfg.anthropic_api_key and cfg.anthropic_api_key.get_secret_value():
            return cfg.llm_model
        if factory.openrouter_key(cfg):
            return factory.chosen_model(cfg)
        return cfg.gemini_model

    def summary(self) -> dict[str, Any]:
        return {
            "today_eur": round(self.today_eur(), 4),
            "month_eur": round(self.month_eur(), 4),
            "daily_limit_eur": self.config.budget_daily_eur,
            "monthly_limit_eur": self.config.budget_monthly_eur,
            "model": self.model_name(),
        }
