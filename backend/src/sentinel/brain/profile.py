"""Import a personal profile (`sentinel brain profile <file>`).

Personal details (family, appointments …) don't belong in the repo. They live in a local
JSON file that the user imports with a command. Only this direct command also creates
reminders – never an automatic import.

Format (example: docs/brain-profile.example.json):
{"nodes": [{"label": "…", "category": "ich", "summary": "…", "links": ["…"]}],
 "reminders": [{"text": "…", "due": "2026-11-01T18:00"}]}
"""

from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from sentinel.brain.store import BrainRejected

if TYPE_CHECKING:
    from sentinel.services import Services

MAX_NODES = 500
MAX_REMINDERS = 100
MAX_BYTES = 512_000


class ProfileError(ValueError):
    pass


def load(path: Path) -> dict[str, Any]:
    try:
        if path.stat().st_size > MAX_BYTES:
            raise ProfileError("Profile file is too large")
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ProfileError(f"File not readable: {exc}") from exc
    except ValueError as exc:
        raise ProfileError(f"Not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ProfileError("The profile must be a JSON object")
    return data


PROFILE_FILE = "brain-profile.json"


def save_copy(svc: Services, data: dict[str, Any]) -> Path:
    """Safe copy in the SENTINEL data folder (not in the temp folder, not in the repo)."""
    target = svc.config.config_dir / PROFILE_FILE
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    return target


def saved(svc: Services) -> dict[str, Any] | None:
    path = svc.config.config_dir / PROFILE_FILE
    if not path.is_file():
        return None
    try:
        return load(path)
    except ProfileError:
        return None


def apply(svc: Services, data: dict[str, Any]) -> dict[str, int]:
    store = svc.brain.store
    nodes = [n for n in data.get("nodes", []) if isinstance(n, dict)][:MAX_NODES]
    added, rejected = 0, 0
    for node in nodes:
        try:
            store.add(
                str(node.get("label", "")),
                category=str(node.get("category", "ich")),
                category_label=str(node.get("category_label", "")) or None,
                summary=str(node.get("summary", "")),
                origin="user",
                source="profile",
            )
            added += 1
        except BrainRejected:
            rejected += 1
    for node in nodes:
        own = store.find(str(node.get("label", "")))
        links = node.get("links") if isinstance(node.get("links"), list) else []
        for name in links[:20]:
            other = store.find(str(name))
            if own and other:
                store.link(int(own["id"]), int(other["id"]))

    reminders = 0
    existing = {(r["text"], round(r["due"])) for r in svc.reminders.list()}
    now = time.time()
    for item in [r for r in data.get("reminders", []) if isinstance(r, dict)][:MAX_REMINDERS]:
        text = str(item.get("text", "")).strip()[:200]
        try:
            due = datetime.fromisoformat(str(item.get("due", ""))).timestamp()
        except ValueError:
            rejected += 1
            continue
        if not text or due <= now or (text, round(due)) in existing:
            continue
        try:
            svc.reminders.add(text, due)
        except ValueError:
            break  # too many open reminders
        existing.add((text, round(due)))
        reminders += 1
    svc.audit.append(
        actor="user",
        source="user",
        action="brain_profile",
        decision="execute",
        result=f"{added} nodes, {reminders} reminders, {rejected} rejected",
    )
    svc.brain.changed()
    return {"nodes": added, "reminders": reminders, "rejected": rejected}
