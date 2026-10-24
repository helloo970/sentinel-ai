"""Knowledge from SENTINEL itself: hand (skills), voice, sources, devices, logbook (no AI)."""

from __future__ import annotations

import logging
import time
from collections import Counter, defaultdict
from collections.abc import Callable
from datetime import datetime
from typing import TYPE_CHECKING

from sentinel.brain.store import BrainRejected
from sentinel.permissions.models import Risk

if TYPE_CHECKING:
    from sentinel.services import Services

log = logging.getLogger(__name__)

# Action module → group in the hand
SKILL_GROUPS = {
    "read": "Reading & Status",
    "automation": "Automation",
    "security": "Defense",
    "shop": "Shopping",
    "everyday": "Everyday",
    "web": "Browser & Apps",
    "brain": "Editing the brain",
    "markets": "Markets & Numbers",
}
LOGBOOK_DAYS = 14


def sync(svc: Services) -> int:
    """Syncs the system nodes: creates new ones, removes the ones that are gone."""
    store = svc.brain.store
    keep: set[str] = set()
    keep_ids: set[int] = set()

    def add(
        label: str, category: str, summary: str, source: str, *, untrusted: bool = False
    ) -> int | None:
        keep.add(source)
        try:
            node = store.add(
                label,
                category=category,
                summary=summary,
                origin="sensor" if untrusted else "system",
                source=source,
            )
        except BrainRejected:
            return None
        if not node:
            return None
        keep_ids.add(int(node["id"]))
        return int(node["id"])

    def link(a: int | None, b: int | None, relation: str = "") -> None:
        if a and b:
            store.link(a, b, relation)

    # ---------------------------------------------------------------- Hand
    hub = add("Jarvis", "skills", "Your assistant in SENTINEL AI.", "sentinel:jarvis")
    sentinel = store.find("SENTINEL AI")
    link(hub, int(sentinel["id"]) if sentinel else None, "part of")
    groups: dict[str, int | None] = {}
    for spec in svc.registry.all():
        if not spec.llm_exposed:
            continue
        module = spec.execute.__module__.rsplit(".", 1)[-1]
        group = SKILL_GROUPS.get(module, "More skills")
        if group not in groups:
            groups[group] = add(group, "skills", "Skills of Jarvis.", f"sentinel:group:{group}")
            link(hub, groups[group], "can")
        node = add(spec.title, "skills", spec.description, f"sentinel:action:{spec.name}")
        link(groups[group], node, "can")
    flows = add("Routines & Modes", "skills", "Routines and modes.", "sentinel:group:flows")
    link(hub, flows, "can")
    for name, routine in sorted(svc.routines.all().items()):
        title = str(getattr(routine, "title", "") or name)
        link(
            flows,
            add(
                f"Routine {title}",
                "skills",
                "Routine with fixed steps.",
                f"sentinel:routine:{name}",
            ),
        )
    for name, mode in sorted(svc.modes.all().items()):
        title = str(getattr(mode, "title", "") or name)
        link(
            flows,
            add(
                f"Mode {title}",
                "skills",
                "Gaming, focus or another mode.",
                f"sentinel:mode:{name}",
            ),
        )
    programs = add("Programs", "skills", "Programs from the allowlist.", "sentinel:group:programs")
    link(hub, programs, "starts")
    for name in sorted(svc.programs.all()):
        node = add(
            f"Program {name}", "skills", "Can be started by Jarvis.", f"sentinel:program:{name}"
        )
        link(programs, node, "starts")

    # ---------------------------------------------------------------- Voice
    cfg = svc.config
    add("Microphone", "stimme", "Push-to-talk on the PC.", "sentinel:voice:mic")
    add("Wake word", "stimme", '"Hey Jarvis" wakes Jarvis up.', "sentinel:voice:wake")
    if cfg.voice_clap:
        add(
            "Clapping",
            "stimme",
            f"Clapping {cfg.voice_clap_count}× opens the app.",
            "sentinel:voice:clap",
        )
    voice_out = "ElevenLabs voice" if svc.eleven and svc.eleven.configured else "Microsoft voice"
    add("Speech output", "stimme", f"Jarvis speaks with the {voice_out}.", "sentinel:voice:tts")
    if svc.server.configured:
        add("Phone app", "stimme", "Phone view through the server.", "sentinel:voice:phone")

    # ---------------------------------------------------------------- Sources
    add("Conversations", "quellen", "What you tell Jarvis.", "sentinel:source:chat")
    for folder in cfg.brain_folders:
        add(
            folder.name,
            "quellen",
            "Folder Jarvis learns from.",
            f"sentinel:source:folder:{folder.name}",
        )
    if cfg.news_feeds:
        add("News", "quellen", "Headlines for the world situation.", "sentinel:source:news")

    # ---------------------------------------------------------------- Devices
    pc = add("Your PC", "geraete", "Windows PC that runs SENTINEL.", "sentinel:pc")
    for row in svc.db.query("SELECT mac, hostname, label, trusted FROM devices LIMIT 200"):
        name = row["label"] or row["hostname"]
        if not name:
            continue
        summary = (
            "Trusted device on the home network."
            if row["trusted"]
            else "Device on the home network."
        )
        node = add(str(name), "geraete", summary, f"sentinel:device:{row['mac']}", untrusted=True)
        link(pc, node, "home network")
    if svc.server.configured:
        server = add(
            "Ubuntu server", "geraete", "Own server with the SENTINEL agent.", "sentinel:server"
        )
        link(pc, server, "monitors")
        last = svc.server.last or {}
        for service in (last.get("services") or {}).get("services", [])[:30]:
            name = str(service.get("name", "")) if isinstance(service, dict) else ""
            if name:
                node = add(
                    f"Service {name}", "geraete", "Runs on the server.", f"sentinel:svc:{name}"
                )
                link(server, node)

    # ---------------------------------------------------------------- Everything else from SENTINEL
    _app_data(svc, add, link)

    # ---------------------------------------------------------------- Logbook
    _logbook(svc, add)

    # By node id, so renamed system nodes (e.g. old German labels) are cleaned up too
    removed = store.delete_where_source_prefix("sentinel:", keep_ids)
    if removed:
        log.info("brain: removed %d outdated system nodes", removed)
    return len(keep)


def _short(text: str, limit: int = 60) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[: limit - 2].rsplit(" ", 1)[0] + " …"


def _app_data(svc: Services, add: Callable[..., int | None], link: Callable[..., None]) -> None:
    """Memory, reminders, shopping list, price alerts, alerts and agent reports."""
    for row in svc.db.query(
        "SELECT id, text, category FROM memories ORDER BY updated DESC LIMIT 300"
    ):
        add(_short(row["text"]), "gedaechtnis", row["text"], f"sentinel:memory:{row['id']}")
    for row in svc.db.query(
        "SELECT id, text, due FROM reminders WHERE done = 0 ORDER BY due LIMIT 100"
    ):
        when = datetime.fromtimestamp(row["due"]).strftime("%b %d %H:%M")
        add(
            f"{_short(row['text'], 50)} ({when})",
            "aufgaben",
            f"Reminder on {when}: {row['text']}",
            f"sentinel:reminder:{row['id']}",
        )
    items = svc.db.query("SELECT id, text FROM shopping_items WHERE done = 0 ORDER BY id LIMIT 100")
    if items:
        shopping = add(
            "My shopping list",
            "einkaufen",
            ", ".join(r["text"] for r in items),
            "sentinel:shopping",
        )
        for row in items:
            link(
                shopping,
                add(
                    row["text"],
                    "einkaufen",
                    "On the shopping list.",
                    f"sentinel:shop:{row['id']}",
                ),
            )
    for row in svc.db.query(
        "SELECT id, title, shop, last_price, target FROM price_watches WHERE active = 1 LIMIT 50"
    ):
        price = f"€{row['last_price']:.2f}" if row["last_price"] is not None else "no price yet"
        target = f", target €{row['target']:.2f}" if row["target"] is not None else ""
        add(
            _short(f"Price alert {row['title'] or row['shop']}"),
            "einkaufen",
            f"Currently {price}{target} at {row['shop']}.",
            f"sentinel:price:{row['id']}",
            untrusted=True,
        )
    for row in svc.db.query(
        "SELECT id, title, severity, status, ts FROM alerts ORDER BY ts DESC LIMIT 25"
    ):
        when = datetime.fromtimestamp(row["ts"]).strftime("%b %d %H:%M")
        state = "open" if row["status"] == "new" else "resolved"
        add(
            _short(f"Alert: {row['title']}"),
            "sicherheit",
            f"{row['severity']}, {state}, {when}.",
            f"sentinel:alert:{row['id']}",
            untrusted=True,
        )
    for row in svc.db.query(
        "SELECT id, agent, title, ts FROM agent_reports ORDER BY ts DESC LIMIT 10"
    ):
        when = datetime.fromtimestamp(row["ts"]).strftime("%b %d")
        add(
            _short(f"Report {row['title']}"),
            "sicherheit",
            f"Agent report ({row['agent']}) from {when}.",
            f"sentinel:report:{row['id']}",
            untrusted=True,
        )


def _logbook(svc: Services, add: Callable[[str, str, str, str], int | None]) -> None:
    """What Jarvis did in the last days: one node per day (without pure read actions)."""
    since = time.time() - LOGBOOK_DAYS * 86400
    titles = {spec.name: spec.title for spec in svc.registry.all() if spec.risk is not Risk.READ}
    days: dict[str, list[str]] = defaultdict(list)
    for row in svc.audit.list(limit=500):
        action = str(row.get("action", ""))
        if action not in titles or row.get("result") != "ok":
            continue
        try:
            moment = datetime.fromisoformat(str(row["ts"])).astimezone()
        except (KeyError, ValueError):
            continue
        if moment.timestamp() < since:
            continue
        days[moment.strftime("%b %d")].append(titles[action])
    for day, actions in days.items():
        counts = Counter(actions).most_common(6)
        summary = ", ".join(f"{t} ×{n}" if n > 1 else t for t, n in counts)
        add(
            f"Logbook {day}",
            "logbuch",
            f"{len(actions)} actions: {summary}",
            f"sentinel:log:{day}",
        )
