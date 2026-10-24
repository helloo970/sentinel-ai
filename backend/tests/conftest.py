from __future__ import annotations

from pathlib import Path

import pytest

from sentinel.config import Settings
from sentinel.llm.offline import ScriptedProvider
from sentinel.services import Services, build_services


def make_settings(tmp_path: Path, **overrides: object) -> Settings:
    managed = tmp_path / "managed"
    managed.mkdir(exist_ok=True)
    values: dict[str, object] = {
        "data_dir": tmp_path / "data",
        "managed_folders": [managed],
        "watched_folders": [],
        "news_feeds": [],
        "anthropic_api_key": None,
        "ntfy_url": None,
        "server_agent_url": None,
        "voice_enabled": False,
        "voice_clap": False,
        "weather_enabled": False,
        "brain_folders": [],
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)  # type: ignore[arg-type]


@pytest.fixture
def scripted() -> ScriptedProvider:
    return ScriptedProvider()


@pytest.fixture
def svc(tmp_path: Path, scripted: ScriptedProvider) -> Services:
    services = build_services(make_settings(tmp_path), llm=scripted)
    services.programs.include_defaults = False
    yield services
    services.db.close()


@pytest.fixture
def managed(svc: Services) -> Path:
    return svc.config.managed_folders[0]


class _MemoryKeyring:
    """Stand-in for the Windows vault: tests must never read real credentials."""

    def __init__(self) -> None:
        self.data: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, name: str) -> str | None:
        return self.data.get((service, name))

    def set_password(self, service: str, name: str, value: str) -> None:
        self.data[(service, name)] = value

    def delete_password(self, service: str, name: str) -> None:
        self.data.pop((service, name), None)


@pytest.fixture(autouse=True)
def _isolated_keyring(monkeypatch) -> None:
    from sentinel.llm import factory
    from sentinel.mail import service as mail_service
    from sentinel.phone import link

    fake = _MemoryKeyring()
    monkeypatch.setattr(link, "_keyring", lambda: fake)
    monkeypatch.setattr(mail_service, "_keyring", lambda: fake)
    monkeypatch.setattr(factory, "_keyring", lambda: fake)
    from sentinel.voice import elevenlabs

    monkeypatch.setattr(elevenlabs, "_keyring", lambda: fake)
    from sentinel.brain import backup

    monkeypatch.setattr(backup, "_keyring", lambda: fake)
    from sentinel import hermes

    monkeypatch.setattr(hermes, "_keyring", lambda: fake)
    from sentinel.pcsec import filescan

    monkeypatch.setattr(filescan, "_keyring", lambda: fake)
