"""Camera service: login from the vault, live still images and motion notifications.

Images are only kept in memory for a moment and never saved. Motion events are
untrusted data: they only create a notification, never an action.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import threading
import time
from typing import TYPE_CHECKING, Any

from sentinel.camera.onvif import CameraError, Channel, OnvifClient, as_dict, private_url
from sentinel.permissions.models import Severity
from sentinel.secrets_filter import redact

if TYPE_CHECKING:
    from sentinel.services import Services

log = logging.getLogger(__name__)

KEYRING = ("SentinelAI-Camera", "login")
CHANNELS_TTL_S = 600
SNAPSHOT_TTL_S = 0.8
MOTION_COOLDOWN_S = 120
RETRY_S = 60
RENEW_EVERY_S = 300


def _keyring() -> Any:
    import keyring

    return keyring


def load_login() -> dict[str, str] | None:
    try:
        raw = _keyring().get_password(*KEYRING)
    except Exception:
        return None
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except ValueError:
        return None
    if not all(isinstance(data.get(k), str) for k in ("url", "user", "password")):
        return None
    return data if private_url(data["url"]) else None


def save_login(url: str, user: str, password: str) -> None:
    if not private_url(url):
        raise CameraError("Camera address must be a private IP address (RFC 1918)")
    _keyring().set_password(*KEYRING, json.dumps({"url": url, "user": user, "password": password}))


def delete_login() -> None:
    with contextlib.suppress(Exception):
        _keyring().delete_password(*KEYRING)


class CameraService:
    def __init__(self, svc: Services, client_factory: Any = OnvifClient) -> None:
        self.svc = svc
        self._factory = client_factory
        self._client: OnvifClient | None = None
        self._login: dict[str, str] | None = None
        self._channels: list[Channel] = []
        self._channels_at = 0.0
        self._uris: dict[int, str] = {}
        self._cache: dict[int, tuple[float, bytes]] = {}
        self._last_motion: dict[str, float] = {}
        self.last_motion: dict[str, Any] | None = None
        self.error: str | None = None
        self._lock = threading.Lock()
        self._task: asyncio.Task[None] | None = None
        self._stopping = False

    # ------------------------------------------------------------- login

    @property
    def configured(self) -> bool:
        return load_login() is not None

    def _get_client(self) -> OnvifClient:
        login = load_login()
        if login is None:
            raise CameraError("No camera set up – in a terminal: sentinel camera login")
        if self._client is None or login != self._login:
            self._client = self._factory(login["url"], login["user"], login["password"])
            self._login = login
            self._channels, self._channels_at, self._uris, self._cache = [], 0.0, {}, {}
        return self._client

    @property
    def motion_enabled(self) -> bool:
        return self.svc.store.get("camera_motion", "on") != "off"

    def set_motion(self, on: bool) -> None:
        self.svc.store.set("camera_motion", "on" if on else "off")

    # ------------------------------------------------------------- images

    def channels(self, force: bool = False) -> list[Channel]:
        with self._lock:
            client = self._get_client()
            if force or not self._channels or time.time() - self._channels_at > CHANNELS_TTL_S:
                try:
                    self._channels = client.channels()
                    self._channels_at = time.time()
                    self.error = None
                except CameraError as exc:
                    self.error = str(exc)
                    raise
            return list(self._channels)

    def snapshot(self, index: int) -> bytes:
        cached = self._cache.get(index)
        if cached and time.time() - cached[0] < SNAPSHOT_TTL_S:
            return cached[1]
        channel = next((c for c in self.channels() if c.index == index), None)
        if channel is None:
            raise CameraError("Unknown camera")
        client = self._get_client()
        uri = self._uris.get(index) or client.snapshot_uri(channel.profile)
        self._uris[index] = uri
        image = client.snapshot(uri)
        self._cache[index] = (time.time(), image)
        return image

    def status(self) -> dict[str, Any]:
        if not self.configured:
            return {"configured": False, "channels": [], "error": None, "motion": False}
        try:
            channels = [as_dict(c) for c in self.channels()]
        except CameraError:
            channels = []
        return {
            "configured": True,
            "channels": channels,
            "error": self.error,
            "motion": self.motion_enabled,
            "last_motion": self.last_motion,
        }

    # ------------------------------------------------------------- motion

    def handle_motion(self, source: str, now: float | None = None) -> bool:
        """One motion event from the camera -> at most one notification per channel and cooldown."""
        now = now or time.time()
        channel = next(
            (c for c in self._channels if source in (c.source, c.config, c.profile)), None
        )
        name = redact(channel.name if channel else "Camera")[:40]
        key = channel.source if channel else source
        if now - self._last_motion.get(key, 0) < MOTION_COOLDOWN_S:
            return False
        self._last_motion[key] = now
        self.last_motion = {"channel": channel.index if channel else None, "name": name, "at": now}
        self.svc.bus.publish("camera_motion", self.last_motion)
        self.svc.notifier.notify(
            "Motion detected", f"{name} saw movement.", severity=Severity.INFO, push=True
        )
        return True

    def _watch_once(self) -> float:
        """Subscribe and pull motion events until something fails. Returns the retry delay."""
        if not self.configured or not self.motion_enabled:
            return RETRY_S
        client = self._get_client()
        self.channels()
        address = client.create_pull_point()
        renewed = time.time()
        while not self._stopping and self.motion_enabled and self._client is client:
            for event in client.pull_motion(address):
                if event.active:
                    self.handle_motion(event.source)
            if time.time() - renewed > RENEW_EVERY_S:
                client.renew(address)
                renewed = time.time()
        return 1

    async def _loop(self) -> None:
        while True:
            try:
                delay = await asyncio.to_thread(self._watch_once)
            except CameraError as exc:
                self.error = str(exc)
                delay = RETRY_S
            except Exception:
                log.exception("camera motion watch failed")
                delay = RETRY_S
            await asyncio.sleep(delay)

    def start(self) -> None:
        self._task = asyncio.get_running_loop().create_task(self._loop())

    async def stop(self) -> None:
        self._stopping = True
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
