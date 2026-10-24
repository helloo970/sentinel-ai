"""Notifications: app (toast in the UI + Windows notification), voice, ntfy push."""

from __future__ import annotations

import logging
import re
import threading
from datetime import datetime
from datetime import time as dtime
from typing import TYPE_CHECKING, Any

import httpx

from sentinel.permissions.models import Severity
from sentinel.permissions.netguard import url_is_private
from sentinel.secrets_filter import redact

if TYPE_CHECKING:
    from sentinel.services import Services

log = logging.getLogger(__name__)

QUIET_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)-([01]\d|2[0-3]):([0-5]\d)$")
NTFY_PRIORITY = {Severity.INFO: "default", Severity.WARN: "high", Severity.CRITICAL: "urgent"}


class Notifier:
    def __init__(self, svc: Services) -> None:
        self.svc = svc

    @property
    def quiet(self) -> bool:
        """Quiet (gaming/focus): no announcements or pushes, except for critical alerts."""
        return self.svc.store.get("quiet_mode") == "1"

    def quiet_hours(self) -> str:
        """Quiet hours as "HH:MM-HH:MM" (settable in the app, otherwise from the .env), "" = off."""
        value = self.svc.store.get("quiet_hours")
        return self.svc.config.quiet_hours if value is None else value

    def night(self, now: datetime | None = None) -> bool:
        """True during quiet hours: Jarvis only speaks for critical alerts."""
        match = QUIET_RE.match(self.quiet_hours())
        if not match:
            return False
        moment = (now or datetime.now()).time()
        start = dtime(int(match[1]), int(match[2]))
        end = dtime(int(match[3]), int(match[4]))
        if start <= end:
            return start <= moment < end
        return moment >= start or moment < end

    def set_quiet(self, on: bool) -> None:
        self.svc.store.set("quiet_mode", "1" if on else "0")

    def ntfy_configured(self) -> bool:
        cfg = self.svc.config
        return bool(cfg.ntfy_url and cfg.ntfy_topic)

    def notify(
        self,
        title: str,
        message: str,
        *,
        severity: Severity = Severity.INFO,
        speak: bool = False,
        undo_id: str | None = None,
        push: bool | None = None,
    ) -> None:
        title, message = redact(title), redact(message)
        self.svc.bus.publish(
            "notification",
            {"title": title, "message": message, "severity": severity, "undo_id": undo_id},
        )
        if self.quiet and severity is not Severity.CRITICAL:
            return
        if speak and self.night() and severity is not Severity.CRITICAL:
            speak = False  # quiet hours
        if speak and self.svc.voice is not None:
            self.svc.voice.say(f"{title}. {message}")
        should_push = push if push is not None else severity in (Severity.WARN, Severity.CRITICAL)
        if should_push and self.ntfy_configured():
            threading.Thread(
                target=self._push, args=(title, message, severity), daemon=True
            ).start()

    def _push(self, title: str, message: str, severity: Severity) -> dict[str, Any]:
        cfg = self.svc.config
        url = f"{(cfg.ntfy_url or '').rstrip('/')}/{cfg.ntfy_topic}"
        if not url_is_private(url):
            log.warning("ntfy url is not in the private network - push skipped")
            return {"ok": False, "error": "ntfy URL must be on the home network (RFC 1918)"}
        headers = {
            # HTTP headers must be latin-1
            "Title": title.encode("latin-1", "replace").decode("latin-1"),
            "Priority": NTFY_PRIORITY.get(severity, "default"),
            "Tags": "shield" if severity is not Severity.CRITICAL else "rotating_light",
        }
        if cfg.ntfy_token:
            headers["Authorization"] = f"Bearer {cfg.ntfy_token.get_secret_value()}"
        try:
            response = httpx.post(url, content=message.encode(), headers=headers, timeout=8)
            return {"ok": response.status_code < 300, "status": response.status_code}
        except httpx.HTTPError as exc:
            log.warning("ntfy push failed: %s", type(exc).__name__)
            return {"ok": False, "error": type(exc).__name__}

    def test_push(self) -> dict[str, Any]:
        if not self.ntfy_configured():
            return {"ok": False, "error": "ntfy is not configured"}
        return self._push("SENTINEL Test", "Push works.", Severity.INFO)
