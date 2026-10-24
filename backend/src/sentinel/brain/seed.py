"""Starter knowledge from the setup – added once on the first start."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sentinel.brain.store import BrainRejected

if TYPE_CHECKING:
    from sentinel.brain.store import BrainStore

# (label, category key, summary) – generic on purpose; personal details come from the profile
SEED_NODES: list[tuple[str, str, str]] = [
    ("School", "schule", "Goes to school."),
    ("Training / University", "schule", "Doing vocational training or studying."),
    ("Side projects", "projekte", "Builds own projects in their spare time."),
    ("SENTINEL AI", "projekte", "Own Jarvis assistant with cybersecurity for the PC."),
    ("Programming & AI", "interessen", "Interested in programming and AI."),
    ("Cybersecurity", "interessen", "Interested in security, networks and servers."),
    ("Gaming", "interessen", "Likes playing games."),
    ("News & world affairs", "interessen", "Follows the news."),
    ("English", "sprachen", "Speaks English."),
    ("Finish SENTINEL", "ziele", "Turn Jarvis into a real assistant."),
    ("Pass school / training", "ziele", "Pass the exams and graduate."),
]
SEED_LINKS: list[tuple[str, str, str]] = [
    ("SENTINEL AI", "Side projects", "part of"),
    ("SENTINEL AI", "Programming & AI", "uses"),
    ("SENTINEL AI", "Cybersecurity", "uses"),
    ("Finish SENTINEL", "SENTINEL AI", "goal"),
    ("Pass school / training", "School", "goal"),
    ("Pass school / training", "Training / University", "goal"),
    ("News & world affairs", "English", "source"),
]


def seed(store: BrainStore) -> int:
    added = 0
    for label, category, summary in SEED_NODES:
        try:
            store.add(label, category=category, summary=summary, origin="seed", source="setup")
            added += 1
        except BrainRejected:
            continue
    for a, b, relation in SEED_LINKS:
        node_a, node_b = store.find(a), store.find(b)
        if node_a and node_b:
            store.link(node_a["id"], node_b["id"], relation)
    return added
