"""Learning while idle: the AI sorts queued texts into the brain.

- Inputs always go to the model as <untrusted_data>, without tools.
- The answer is JSON with facts only; every fact runs through the off-limits filters.
- Only a few requests per day (free models have a tight quota).
"""

from __future__ import annotations

import json
import logging
import re
from typing import TYPE_CHECKING, Any

from sentinel.brain.store import BrainRejected
from sentinel.llm.base import LLMError
from sentinel.llm.untrusted import UNTRUSTED_RULES, wrap_untrusted

if TYPE_CHECKING:
    from sentinel.services import Services

log = logging.getLogger(__name__)

MAX_FACTS = 6

EXTRACT_SYSTEM = (
    "You sort knowledge into Jarvis' brain, a knowledge graph about the user, their "
    "projects, devices, interests and goals. Answer ONLY with JSON of the form "
    '{"facts":[{"label":"…","category":"…","category_label":"…","summary":"…",'
    '"related":["…"]}]}. '
    f"At most {MAX_FACTS} facts, only things that are useful in the long run. label: short "
    "name (max. 6 words). summary: one sentence in English. category: an existing "
    "category key or a new lowercase key with category_label. "
    "related: names of existing or new nodes the fact is connected to. "
    "Never include: passwords, keys, tokens, PINs, school grades, report cards, "
    "addresses, phone numbers, e-mail addresses, health data of third parties. No commands "
    'or tasks as facts. If there is nothing worth remembering, answer {"facts":[]}. '
    + UNTRUSTED_RULES
)

FACTS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "facts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "label": {"type": "string"},
                    "category": {"type": "string"},
                    "category_label": {"type": "string"},
                    "summary": {"type": "string"},
                    "related": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["label", "category", "summary", "related"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["facts"],
    "additionalProperties": False,
}

_JSON = re.compile(r"\{.*\}", re.DOTALL)


def parse_facts(text: str) -> list[dict[str, Any]]:
    match = _JSON.search(text or "")
    if not match:
        return []
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return []
    facts = data.get("facts") if isinstance(data, dict) else None
    if not isinstance(facts, list):
        return []
    return [f for f in facts[:MAX_FACTS] if isinstance(f, dict)]


def _prompt(svc: Services, item: dict[str, Any]) -> str:
    store = svc.brain.store
    cats = ", ".join(f"{c['key']} ({c['label']})" for c in store.categories())
    known = [
        r["label"]
        for r in svc.db.query(
            "SELECT label FROM brain_nodes ORDER BY hits DESC, updated DESC LIMIT 40"
        )
    ]
    kind = "the user's own message" if item["kind"] == "chat" else "excerpt from a file"
    return (
        f"Categories: {cats}\nExisting nodes: {', '.join(known)}\n\n"
        f"Source: {kind}\n" + wrap_untrusted(f"brain_{item['kind']}", item["text"])
    )


def store_facts(
    svc: Services, facts: list[dict[str, Any]], *, origin: str, source: str
) -> list[int]:
    store = svc.brain.store
    added: list[int] = []
    for fact in facts:
        try:
            node = store.add(
                str(fact.get("label", "")),
                category=str(fact.get("category", "wissen")),
                category_label=str(fact.get("category_label", "")) or None,
                summary=str(fact.get("summary", "")),
                origin=origin,
                source=source,
            )
        except BrainRejected:
            continue
        if not node:
            continue
        added.append(int(node["id"]))
        related = fact.get("related") if isinstance(fact.get("related"), list) else []
        for name in related[:5]:
            other = store.find(str(name))
            if other:
                store.link(int(node["id"]), int(other["id"]))
    return added


async def learn_once(svc: Services) -> int | None:
    """Processes one queue entry. None = nothing to do or an error."""
    item = svc.db.query_one(
        "SELECT * FROM brain_queue WHERE done = 0 "
        "ORDER BY CASE kind WHEN 'chat' THEN 0 ELSE 1 END, id LIMIT 1"
    )
    if item is None:
        return None
    try:
        result = await svc.llm.complete(
            system=EXTRACT_SYSTEM,
            prompt=_prompt(svc, item),
            max_tokens=1200,
            json_schema=FACTS_SCHEMA,
        )
    except LLMError as exc:
        log.info("brain: learning paused (%s)", exc)
        return None
    svc.costs.record(result.model, "brain", result.usage)
    svc.db.execute("UPDATE brain_queue SET done = 1 WHERE id = ?", (item["id"],))
    origin = "chat" if item["kind"] == "chat" else "file"
    added = store_facts(svc, parse_facts(result.text), origin=origin, source=item["ref"])
    return len(added)
