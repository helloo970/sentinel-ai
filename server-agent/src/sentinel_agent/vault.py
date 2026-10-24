"""Storage for encrypted backups from the PC (e.g. the brain).

The agent cannot read the data: it only stores base64 bytes that were encrypted on the PC
with the user's password. Fixed names, limited size, the latest versions are kept.
"""

from __future__ import annotations

import base64
import binascii
import os
import re
import time
from pathlib import Path
from typing import Any

VAULT_DIR = "/var/lib/sentinel-agent/vault"
NAME_RE = re.compile(r"^[a-z]{3,20}$")
MAGIC = b"SBRN1"
MAX_BYTES = 8 * 1024 * 1024
MAX_BODY = MAX_BYTES * 4 // 3 + 1024
KEEP = 5


class VaultError(ValueError):
    pass


def _dir(root: str, name: str) -> Path:
    if not NAME_RE.match(name):
        raise VaultError("invalid name")
    return Path(root) / name


def store(root: str, name: str, body: dict[str, Any]) -> dict[str, Any]:
    data = body.get("data") if isinstance(body, dict) else None
    if not isinstance(data, str):
        raise VaultError("data missing")
    try:
        raw = base64.b64decode(data, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise VaultError("invalid base64") from exc
    if not raw.startswith(MAGIC) or len(raw) > MAX_BYTES:
        raise VaultError("not an encrypted backup")
    folder = _dir(root, name)
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    target = folder / f"{stamp}.bin"
    tmp = folder / f".{stamp}.tmp"
    with open(tmp, "wb") as fh:
        fh.write(raw)
        fh.flush()
        os.fsync(fh.fileno())
    os.chmod(tmp, 0o600)
    os.replace(tmp, target)
    versions = sorted(folder.glob("*.bin"))
    for old in versions[:-KEEP]:
        old.unlink(missing_ok=True)
    return {"stored": target.name, "bytes": len(raw), "versions": min(len(versions), KEEP)}


def latest(root: str, name: str) -> dict[str, Any] | None:
    folder = _dir(root, name)
    versions = sorted(folder.glob("*.bin")) if folder.is_dir() else []
    if not versions:
        return None
    newest = versions[-1]
    return {
        "name": newest.name,
        "data": base64.b64encode(newest.read_bytes()).decode("ascii"),
    }
