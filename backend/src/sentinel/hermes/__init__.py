"""Connecting external agents (Hermes) to SENTINEL via MCP."""

from __future__ import annotations

import json
import secrets
from pathlib import Path
from typing import Any

KEYRING = ("SentinelAI-Hermes", "agent_token")
AGENT_PATH = "/agent/"
INFO_FILE = "agent.json"


def _keyring() -> Any:
    import keyring

    return keyring


def stored_token() -> str | None:
    try:
        return _keyring().get_password(*KEYRING)
    except Exception:
        return None


def create_token() -> str:
    token = secrets.token_urlsafe(32)
    _keyring().set_password(*KEYRING, token)
    return token


def delete_token() -> None:
    try:
        _keyring().delete_password(*KEYRING)
    except Exception:  # already deleted
        return


def write_info(data_dir: Path, port: int) -> None:
    """Only the port for the MCP bridge – never a token."""
    (data_dir / INFO_FILE).write_text(json.dumps({"port": port}) + "\n", encoding="utf-8")


def read_port(data_dir: Path) -> int | None:
    try:
        port = int(json.loads((data_dir / INFO_FILE).read_text(encoding="utf-8"))["port"])
    except (OSError, ValueError, KeyError, TypeError):
        return None
    return port if 1024 <= port <= 65535 else None
