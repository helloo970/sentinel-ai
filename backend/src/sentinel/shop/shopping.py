"""Shopping list: local in SQLite, via chat, voice or UI."""

from __future__ import annotations

import re
import time
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from sentinel.services import Services

MAX_ITEMS = 200
MAX_LEN = 60
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


def clean_item(text: str) -> str:
    text = re.sub(r"\s+", " ", _CONTROL.sub(" ", text or "")).strip(" .,;:!?\"'")
    return text[:MAX_LEN]


def split_items(text: str) -> list[str]:
    """'milk, eggs and bread' -> ['milk', 'eggs', 'bread']"""
    parts = re.split(r"\s*(?:,|;|\band\b|\bplus\b|\bund\b|\bsowie\b|&)\s*", text or "")
    return [item for item in (clean_item(p) for p in parts) if item][:10]


class ShoppingList:
    def __init__(self, svc: Services) -> None:
        self.svc = svc

    def items(self, include_done: bool = False) -> list[dict[str, Any]]:
        sql = "SELECT * FROM shopping_items"
        if not include_done:
            sql += " WHERE done = 0"
        return self.svc.db.query(sql + " ORDER BY created")

    def find(self, text: str) -> dict[str, Any] | None:
        return self.svc.db.query_one(
            "SELECT * FROM shopping_items WHERE done = 0 AND lower(text) = lower(?)",
            (clean_item(text),),
        )

    def add(self, text: str) -> dict[str, Any]:
        item = clean_item(text)
        if not item:
            raise ValueError("Empty entry")
        existing = self.find(item)
        if existing:
            return {**existing, "existing": True}
        count = self.svc.db.query_one("SELECT COUNT(*) AS n FROM shopping_items WHERE done = 0")
        if count and count["n"] >= MAX_ITEMS:
            raise ValueError("The shopping list is full")
        item_id = self.svc.db.execute(
            "INSERT INTO shopping_items(text, created) VALUES(?, ?)", (item, time.time())
        )
        return {"id": item_id, "text": item, "existing": False}

    def set_done(self, item_id: int, done: bool) -> bool:
        return bool(
            self.svc.db.execute(
                "UPDATE shopping_items SET done = ? WHERE id = ?", (int(done), item_id)
            )
        )

    def spoken(self) -> str:
        items = [i["text"] for i in self.items()]
        if not items:
            return "The shopping list is empty."
        if len(items) == 1:
            return f"The list has: {items[0]}."
        return f"The list has {len(items)} items: {', '.join(items[:-1])} and {items[-1]}."
