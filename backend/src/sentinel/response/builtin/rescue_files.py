"""Back up important folders on a critical alert (copy only, never move)."""

from __future__ import annotations

import shutil
import time
from pathlib import Path

from sentinel.response.base import (
    CommandResult,
    PluginContext,
    ResponseCommand,
    ResponseEvent,
    RiskLevel,
)


class RescueFiles(ResponseCommand):
    name = "rescue_files"
    description = (
        "Copies the rescue folders (e.g. Documents) into a backup in the SENTINEL data folder."
    )
    risk_level = RiskLevel.LOW
    requires_pin = False
    requires_confirm = True
    timeout_s = 60.0

    def can_run(self, event: ResponseEvent) -> bool:
        return event.severity == "critical"

    def run(self, event: ResponseEvent, ctx: PluginContext) -> CommandResult:
        target = Path(ctx.rescue_dir) / time.strftime("%Y%m%d-%H%M%S")
        budget = ctx.rescue_max_mb * 1024 * 1024
        copied, skipped, used = 0, 0, 0
        log: list[str] = []
        deadline = time.monotonic() + self.timeout_s - 5
        for folder in ctx.rescue_folders:
            source = Path(folder)
            if not source.is_dir():
                log.append(f"skipped (missing): {source}")
                continue
            for item in source.rglob("*"):
                if time.monotonic() > deadline:
                    log.append("Time limit reached - backup incomplete")
                    break
                if item.is_symlink() or not item.is_file():
                    continue
                size = item.stat().st_size
                if used + size > budget:
                    skipped += 1
                    continue
                destination = target / source.name / item.relative_to(source)
                destination.parent.mkdir(parents=True, exist_ok=True)
                if destination.exists():
                    continue
                shutil.copy2(item, destination)
                copied += 1
                used += size
        if skipped:
            log.append(f"{skipped} files skipped (limit {ctx.rescue_max_mb} MB)")
        return CommandResult(
            ok=True,
            message=f"{copied} files ({used / 2**20:.0f} MB) backed up to {target}",
            log=log,
        )
