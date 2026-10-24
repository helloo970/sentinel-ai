"""Backup watcher: how old is the newest file in each backup folder?

A root timer writes a small overview every hour (only timestamps, count, size, no file
names) to /run/sentinel-agent/backups.json. The agent only reads this file and so needs
no read access to the backups themselves.

Usage (as root, via timer):
    python3 -m sentinel_agent.backups --config /etc/sentinel-agent/config.json
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import time
from pathlib import Path
from typing import Any

BACKUP_SNAPSHOT = "/run/sentinel-agent/backups.json"
MAX_ENTRIES = 200_000
MAX_DEPTH = 8
MAX_SNAPSHOT_AGE_S = 3 * 3600


def scan_path(path: str, *, max_entries: int = MAX_ENTRIES) -> dict[str, Any]:
    root = Path(path)
    result: dict[str, Any] = {"path": path, "exists": root.is_dir()}
    if not result["exists"]:
        return result
    newest = 0.0
    files = 0
    size = 0
    seen = 0
    truncated = False
    stack: list[tuple[str, int]] = [(str(root), 0)]
    while stack:
        current, depth = stack.pop()
        try:
            with os.scandir(current) as entries:
                for entry in entries:
                    seen += 1
                    if seen > max_entries:
                        truncated = True
                        break
                    try:
                        if entry.is_symlink():
                            continue
                        if entry.is_dir(follow_symlinks=False):
                            if depth < MAX_DEPTH:
                                stack.append((entry.path, depth + 1))
                            continue
                        stat = entry.stat(follow_symlinks=False)
                    except OSError:
                        continue
                    files += 1
                    size += stat.st_size
                    newest = max(newest, stat.st_mtime)
        except OSError:
            continue
        if truncated:
            break
    result.update(
        {
            "files": files,
            "bytes": size,
            "newest": newest or None,
            "truncated": truncated,
        }
    )
    return result


def scan(paths: list[str]) -> dict[str, Any]:
    return {"ts": time.time(), "paths": [scan_path(p) for p in paths[:20]]}


def backup_status(path: str = BACKUP_SNAPSHOT, now: float | None = None) -> dict[str, Any]:
    snapshot = Path(path)
    if not snapshot.is_file():
        return {"available": False, "paths": [], "reason": "no backup snapshot"}
    try:
        data = json.loads(snapshot.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"available": False, "paths": [], "reason": "snapshot unreadable"}
    age = (now or time.time()) - float(data.get("ts") or 0)
    return {
        "available": True,
        "stale": age > MAX_SNAPSHOT_AGE_S,
        "ts": data.get("ts"),
        "paths": list(data.get("paths") or [])[:20],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="SENTINEL backup snapshot (run as root)")
    parser.add_argument("--config", default="/etc/sentinel-agent/config.json")
    args = parser.parse_args()
    config = json.loads(Path(args.config).read_text(encoding="utf-8"))
    paths = [str(p) for p in config.get("backup_paths") or [] if str(p).startswith("/")]
    out = Path(config.get("backup_snapshot") or BACKUP_SNAPSHOT)
    tmp = out.with_suffix(".tmp")
    tmp.write_text(json.dumps(scan(paths)), encoding="utf-8")
    os.chmod(tmp, 0o640)
    if hasattr(os, "chown"):
        import grp

        with contextlib.suppress(KeyError):
            os.chown(tmp, 0, grp.getgrnam("sentinel-agent").gr_gid)
    tmp.replace(out)


if __name__ == "__main__":
    main()
