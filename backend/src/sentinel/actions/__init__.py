"""All permitted actions (allowlist). Anything not registered here is rejected."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sentinel.permissions import ActionRegistry


def register_all(registry: ActionRegistry) -> None:
    from sentinel.actions import automation, brain, everyday, markets, read, security, shop, web

    read.register(registry)
    automation.register(registry)
    security.register(registry)
    shop.register(registry)
    everyday.register(registry)
    web.register(registry)
    brain.register(registry)
    markets.register(registry)
