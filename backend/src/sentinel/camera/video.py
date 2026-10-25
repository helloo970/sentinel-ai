"""Live video: ffmpeg turns the recorder's RTSP stream into MJPEG for the app.

ffmpeg runs with a fixed argument list (no shell). The RTSP URL carries the camera login,
so it is never logged and ffmpeg's own output is discarded.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import subprocess
import sys
import threading
from collections.abc import AsyncIterator
from pathlib import Path
from urllib.parse import quote, urlparse

from sentinel.camera.onvif import CameraError
from sentinel.permissions.netguard import is_rfc1918

log = logging.getLogger(__name__)

MAX_STREAMS = 10
GRID = {"fps": 8, "width": 640}
FULL = {"fps": 15, "width": 1280}
_active = threading.BoundedSemaphore(MAX_STREAMS)


def find_ffmpeg(configured: Path | None = None) -> Path | None:
    """ffmpeg from the settings, PATH or a winget install (`winget install Gyan.FFmpeg`)."""
    candidates: list[Path] = []
    if configured:
        candidates.append(configured)
    found = shutil.which("ffmpeg")
    if found:
        candidates.append(Path(found))
    local = os.environ.get("LOCALAPPDATA")
    if local:
        winget = Path(local) / "Microsoft" / "WinGet"
        candidates.append(winget / "Links" / "ffmpeg.exe")
        candidates.extend(sorted((winget / "Packages").glob("Gyan.FFmpeg*/*/bin/ffmpeg.exe")))
    return next((c for c in candidates if c.is_file()), None)


def with_login(rtsp_url: str, user: str, password: str) -> str:
    parsed = urlparse(rtsp_url)
    if parsed.scheme != "rtsp" or not is_rfc1918(parsed.hostname or ""):
        raise CameraError("Video only from private IP addresses")
    login = f"{quote(user, safe='')}:{quote(password, safe='')}@"
    return parsed._replace(netloc=f"{login}{parsed.hostname}:{parsed.port or 554}").geturl()


def ffmpeg_args(ffmpeg: Path, url: str, fps: int, width: int) -> list[str]:
    return [
        str(ffmpeg),
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
        "-rtsp_transport",
        "tcp",
        "-i",
        url,
        "-an",
        "-vf",
        f"fps={fps},scale='min({width},iw)':-2",
        "-q:v",
        "6",
        "-f",
        "mjpeg",
        "pipe:1",
    ]


async def mjpeg_stream(args: list[str]) -> AsyncIterator[bytes]:
    """Yield raw MJPEG bytes until the viewer disconnects; ffmpeg is always stopped."""
    if not _active.acquire(blocking=False):
        return  # the route checked before; an empty answer lets the app fall back
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    try:
        proc = subprocess.Popen(  # noqa: S603 - fixed binary and argument list, no shell
            args,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            creationflags=flags,
        )
    except OSError as exc:
        _active.release()
        raise CameraError("ffmpeg could not be started") from exc
    try:
        out = proc.stdout
        while out is not None:
            chunk = await asyncio.to_thread(out.read1, 65536)
            if not chunk:
                break
            yield chunk
    finally:
        # No await here: a cancelled stream must still free its slot and stop ffmpeg
        proc.kill()
        _active.release()
        threading.Thread(target=proc.wait, daemon=True).start()


def busy() -> bool:
    """True if no further live video may start right now."""
    if not _active.acquire(blocking=False):
        return True
    _active.release()
    return False
