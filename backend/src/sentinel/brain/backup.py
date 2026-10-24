"""Encrypted backup of the brain on the user's own server.

Password → scrypt → AES-256-GCM. The Windows credential vault only holds the derived key
with its salt, never the password. The server only sees encrypted bytes.
Format: b"SBRN1" + salt(16) + nonce(12) + ciphertext.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import zlib
from typing import Any

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

MAGIC = b"SBRN1"
KEYRING = ("SentinelAI-Brain", "backup_key")
MIN_PASSWORD = 10
SCRYPT_N = 2**15


class BackupError(RuntimeError):
    pass


def _keyring() -> Any:
    import keyring

    return keyring


def derive(password: str, salt: bytes) -> bytes:
    return Scrypt(salt=salt, length=32, n=SCRYPT_N, r=8, p=1).derive(password.encode("utf-8"))


def set_password(password: str) -> None:
    if len(password) < MIN_PASSWORD:
        raise BackupError(f"Password too short (at least {MIN_PASSWORD} characters)")
    salt = os.urandom(16)
    key = derive(password, salt)
    _keyring().set_password(*KEYRING, f"{salt.hex()}:{key.hex()}")


def stored_key() -> tuple[bytes, bytes] | None:
    try:
        value = _keyring().get_password(*KEYRING)
    except Exception:
        return None
    if not value or ":" not in value:
        return None
    salt_hex, key_hex = value.split(":", 1)
    try:
        return bytes.fromhex(salt_hex), bytes.fromhex(key_hex)
    except ValueError:
        return None


def encrypt(data: dict[str, Any], salt: bytes, key: bytes) -> bytes:
    plain = zlib.compress(json.dumps(data, ensure_ascii=False, default=str).encode("utf-8"))
    nonce = os.urandom(12)
    return MAGIC + salt + nonce + AESGCM(key).encrypt(nonce, plain, MAGIC)


def decrypt(blob: bytes, password: str | None = None, key: bytes | None = None) -> dict[str, Any]:
    if not blob.startswith(MAGIC) or len(blob) < len(MAGIC) + 28 + 16:
        raise BackupError("Not a valid brain backup")
    salt = blob[len(MAGIC) : len(MAGIC) + 16]
    nonce = blob[len(MAGIC) + 16 : len(MAGIC) + 28]
    if key is None:
        if not password:
            raise BackupError("Password missing")
        key = derive(password, salt)
    try:
        plain = AESGCM(key).decrypt(nonce, blob[len(MAGIC) + 28 :], MAGIC)
    except InvalidTag as exc:
        raise BackupError("Wrong password or damaged backup") from exc
    return json.loads(zlib.decompress(plain).decode("utf-8"))


def fingerprint(data: dict[str, Any]) -> str:
    raw = json.dumps(data, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def to_b64(blob: bytes) -> str:
    return base64.b64encode(blob).decode("ascii")


def from_b64(text: str) -> bytes:
    try:
        return base64.b64decode(text, validate=True)
    except ValueError as exc:
        raise BackupError("The backup from the server is damaged") from exc
