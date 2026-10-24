"""Entry point: `python -m sentinel` (or as a Tauri sidecar)."""

from __future__ import annotations

import json
import logging
import os
import sys
import threading
import time
from pathlib import Path

import uvicorn

from sentinel.api.app import create_app
from sentinel.api.security import generate_token
from sentinel.config import get_settings
from sentinel.services import build_services


def _write_session_file(path: os.PathLike[str] | str, port: int, token: str) -> None:
    """For development mode: Tauri reads port and token from this file."""
    text = json.dumps({"port": port, "token": token, "pid": os.getpid()})
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(text)


def _watch_parent(pid: int) -> None:
    """Stop the backend when the Tauri app is no longer running (even after a hard kill)."""
    import psutil

    while psutil.pid_exists(pid):
        time.sleep(3)
    os._exit(0)


def _ensure_streams(log_dir: Path) -> None:
    """Without a console (sidecar build) stdout/stderr are None - write to the log then."""
    if sys.stdout is not None and sys.stderr is not None:
        return
    log_dir.mkdir(parents=True, exist_ok=True)
    log = open(log_dir / "backend.log", "a", encoding="utf-8", buffering=1)  # noqa: SIM115
    if sys.stdout is None:
        sys.stdout = log
    if sys.stderr is None:
        sys.stderr = log


class _RedactFilter(logging.Filter):
    """Never log secrets (keys, tokens, passwords)."""

    def filter(self, record: logging.LogRecord) -> bool:
        from sentinel.secrets_filter import redact

        record.msg = redact(record.getMessage())
        record.args = None
        return True


def _file_logging(log_dir: Path) -> None:
    """Always also log to a file (the sidecar output would otherwise be lost)."""
    from logging.handlers import RotatingFileHandler

    log_dir.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(
        log_dir / "backend.log", maxBytes=1_000_000, backupCount=2, encoding="utf-8"
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    handler.addFilter(_RedactFilter())
    logging.getLogger().addHandler(handler)


def main() -> None:
    from sentinel.cli import run

    code = run(sys.argv[1:])
    if code is not None:
        sys.exit(code)
    config = get_settings()
    _ensure_streams(config.data_dir)
    logging.basicConfig(
        level=config.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    _file_logging(config.data_dir / "logs")
    # No token in the log: either supplied by Tauri or generated randomly per start
    token = config.api_token.get_secret_value() if config.api_token else generate_token()
    from sentinel.db import InstanceLock

    lock = InstanceLock(config.data_dir)
    # On app restart the old backend may still be running briefly - wait up to 10 s
    for _ in range(20):
        if lock.acquire():
            break
        time.sleep(0.5)
    else:
        # Two backends on the same database can corrupt it
        logging.getLogger(__name__).error("SENTINEL is already running - second start aborted")
        print("SENTINEL is already running.", file=sys.stderr)
        sys.exit(1)
    svc = build_services(config)
    if not config.api_token:
        session = config.data_dir / "session.json"
        _write_session_file(session, config.port, token)
        print(
            f"SENTINEL backend: http://127.0.0.1:{config.port} (token in {session})",
            file=sys.stderr,
        )
    parent = os.environ.get("SENTINEL_PARENT_PID", "")
    if parent.isdigit():
        threading.Thread(target=_watch_parent, args=(int(parent),), daemon=True).start()
    from sentinel import hermes

    hermes.write_info(config.data_dir, config.port)
    app = create_app(svc, token=token, agent_token=hermes.stored_token())
    uvicorn.run(app, host=config.host, port=config.port, log_level="warning", access_log=False)


if __name__ == "__main__":
    main()
