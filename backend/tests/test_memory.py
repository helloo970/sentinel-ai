"""Memory: storing, searching (FTS5 + vectors), secret filter, extraction."""

from __future__ import annotations

import pytest

from sentinel.memory.extract import extract_candidates
from sentinel.memory.store import MemoryRejected
from sentinel.secrets_filter import contains_secret, redact
from sentinel.services import Services


@pytest.mark.parametrize(
    "text",
    [
        "my password is Summer2024!",
        "The Wi-Fi passcode is secret123",
        "mein Passwort ist Sommer2024!",
        "api_key=sk-ant-api03-abcdefghijklmnop",
        "Token: ghp_abcdefghijklmnopqrstuvwxyz0123",
        "-----BEGIN OPENSSH PRIVATE KEY-----\nabc\n-----END OPENSSH PRIVATE KEY-----",
        "my PIN is 1234",
        "my credit card number is 4111111111111111",
    ],
)
def test_secrets_are_never_stored(svc: Services, text: str) -> None:
    with pytest.raises(MemoryRejected):
        svc.memory.add(text, source="test")
    assert svc.memory.list() == []


def test_memory_requires_source(svc: Services) -> None:
    with pytest.raises(MemoryRejected):
        svc.memory.add("Likes coffee", source="")


def test_add_search_update_delete(svc: Services) -> None:
    coffee = svc.memory.add("Drinks black coffee in the morning", category="preference", source="t")
    svc.memory.add("Works as a system administrator", category="work", source="t")
    svc.memory.add("Lives in Hamburg", category="place", source="t")

    assert svc.memory.search("coffee")[0]["id"] == coffee["id"]
    # Semantic/fuzzy: word form and typo
    assert svc.memory.search("cofee drinking")[0]["id"] == coffee["id"]
    assert svc.memory.search("administrator job")[0]["category"] == "work"

    updated = svc.memory.update(coffee["id"], "Drinks green tea in the morning")
    assert "green tea" in updated["text"]
    assert svc.memory.search("tea")[0]["id"] == coffee["id"]
    with pytest.raises(MemoryRejected):
        svc.memory.update(coffee["id"], "Password: hunter22")

    assert svc.memory.delete(coffee["id"])
    assert all(m["id"] != coffee["id"] for m in svc.memory.search("tea"))
    svc.memory.delete_all()
    assert svc.memory.list() == []


def test_duplicates_are_merged(svc: Services) -> None:
    a = svc.memory.add("Likes pizza", source="t")
    b = svc.memory.add("  likes   pizza ", source="t")
    assert a["id"] == b["id"]
    assert len(svc.memory.list()) == 1


def test_fts_query_injection_is_harmless(svc: Services) -> None:
    svc.memory.add("Likes pizza", source="t")
    for query in ['" OR 1=1 --', "NEAR(a b)", "*", 'pizza"*) OR (x']:
        svc.memory.search(query)  # must not raise


def test_extract_candidates() -> None:
    texts = [c.text for c in extract_candidates("Hi, my name is Alex and I live in Cologne.")]
    assert "Name is Alex" in texts
    assert "Lives in Cologne" in texts
    note = extract_candidates("Remember that my server makes backups every Sunday")
    assert note and "backups" in note[0].text
    assert extract_candidates("I like Python and Rust")[0].text.startswith("Likes Python")
    both = extract_candidates("I live in Cologne and I work as a sysadmin")
    assert [c.text for c in both] == ["Lives in Cologne", "Works as a sysadmin"]
    assert extract_candidates("My favorite game is Minecraft")[0].text == "Favorite game: Minecraft"


def test_extract_ignores_questions_and_small_talk() -> None:
    assert extract_candidates("Do you remember that song?") == []
    assert extract_candidates("my name is not important") == []


def test_extract_ignores_secret_messages() -> None:
    assert extract_candidates("Remember my password: Dog123") == []
    assert extract_candidates("remember the token abc") == []


def test_secret_detection_and_redaction() -> None:
    assert contains_secret("Bearer abcdefghijklmnopqrstuvwxyz0123")
    assert not contains_secret("I like Sundays")
    assert "hunter22" not in redact("password=hunter22 and more")
    assert redact("normal text stays") == "normal text stays"
