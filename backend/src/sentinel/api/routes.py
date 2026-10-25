"""REST routes of the local API. Blocking routes are sync (they run in the thread pool)."""

from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import TYPE_CHECKING, Any, Literal

from fastapi import APIRouter, HTTPException, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from sentinel import __version__
from sentinel.agents.service import AgentBlocked
from sentinel.brain.backup import BackupError
from sentinel.brain.store import BrainRejected
from sentinel.memory.store import MemoryRejected
from sentinel.monitor import autostart, processes
from sentinel.permissions.models import Source

if TYPE_CHECKING:
    from sentinel.services import Services


class Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ChatBody(Body):
    message: str = Field(min_length=1, max_length=8000)
    conversation: str = Field(default="default", pattern=r"^[a-z0-9\-]{1,40}$")


class HaltBody(Body):
    via: Literal["ui", "tray", "hotkey", "voice"] = "ui"


class AutonomyBody(Body):
    level: int = Field(ge=0, le=3)


class ActionBody(Body):
    params: dict[str, Any] = Field(default_factory=dict)


class MemoryBody(Body):
    text: str = Field(min_length=1, max_length=500)
    category: str = "note"


class BrainNodeBody(Body):
    label: str = Field(min_length=2, max_length=80)
    summary: str = Field(default="", max_length=300)
    category: str = Field(default="ich", pattern=r"^[a-z][a-z0-9_]{2,19}$")


class ClientLogBody(Body):
    where: str = Field(min_length=1, max_length=40, pattern=r"^[a-z_\-]{1,40}$")
    message: str = Field(max_length=2000)


class SetupBody(Body):
    home_city: str | None = Field(default=None, max_length=60)
    google_account: str | None = Field(default=None, max_length=254)
    quiet_hours: str | None = Field(default=None, max_length=11)


class CameraMotionBody(Body):
    enabled: bool


class ExternalCallBody(Body):
    action: str = Field(min_length=2, max_length=60, pattern=r"^[a-z][a-z0-9_]{1,59}$")
    params: dict[str, Any] = Field(default_factory=dict)
    reason: str = Field(default="", max_length=300)


class AgentBody(Body):
    goal: str | None = Field(default=None, max_length=2000)


class PluginBody(Body):
    enabled: bool


class ShopBody(Body):
    query: str = Field(min_length=3, max_length=200)


class SayBody(Body):
    text: str = Field(min_length=1, max_length=1500)


class AlertTestBody(Body):
    rule: str = Field(min_length=3, max_length=40)


class VoiceDeviceBody(Body):
    name: str | None = Field(default=None, max_length=120)
    api: str | None = Field(default=None, max_length=40)


class VoiceEnableBody(Body):
    on: bool


class PhonePairBody(Body):
    pin: str = Field(min_length=4, max_length=64)


class ApproveBody(Body):
    pin: str | None = Field(default=None, max_length=12)


class PinBody(Body):
    new_pin: str = Field(min_length=6, max_length=12)
    old_pin: str | None = Field(default=None, max_length=12)


class ResponseApproveBody(Body):
    file: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_\-]{0,60}\.py$")
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    pin: str = Field(min_length=6, max_length=12)


class ResponseRevokeBody(Body):
    file: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_\-]{0,60}\.py$")


def build_router(svc: Services) -> APIRouter:
    router = APIRouter()
    engine = svc.engine

    # ---------------------------------------------------------------- Status

    def warnings() -> list[dict[str, str]]:
        """Problems the user can fix themselves (for hints in the app)."""
        items = []
        server_error = svc.server.error or svc.relay.error
        if svc.server.configured and server_error:
            items.append({"key": "server", "title": "Server", "message": server_error})
        if svc.config.malware_feed and svc.filescan.feed_error:
            items.append(
                {
                    "key": "malware",
                    "title": "Malware list",
                    "message": svc.filescan.feed_error,
                }
            )
        eleven = svc.eleven.last_error if svc.eleven is not None else None
        stt = svc.voice.cloud_stt.last_error
        for key, title, message in (("tts", "Voice", eleven), ("stt", "Speech recognition", stt)):
            if message:
                items.append({"key": key, "title": title, "message": message})
        if svc.camera.error and svc.camera.configured:
            items.append({"key": "camera", "title": "Camera", "message": svc.camera.error})
        return items

    @router.get("/status")
    def status() -> dict[str, Any]:
        return {
            "version": __version__,
            "user_name": svc.config.user_name,
            "autonomy": int(engine.autonomy),
            "halted": engine.halted,
            "autonomous_last_hour": engine.autonomous_count(),
            "autonomous_limit": engine.max_autonomous_per_hour,
            "undo_window_s": engine.undo_window_s,
            "llm": {
                "provider": svc.llm.name,
                "model": svc.llm.model,
                "available": svc.llm.available,
            },
            "costs": svc.costs.summary(),
            "alerts": svc.alerts.counts(),
            "pending_approvals": len(engine.list_approvals()),
            "monitor": svc.monitor.status(),
            "voice": svc.voice.status(),
            "server": {
                "configured": svc.server.configured,
                "error": svc.server.error or svc.relay.error,
            },
            "warnings": warnings(),
            "night": svc.notifier.night(),
            "time": time.time(),
        }

    @router.get("/system")
    def system() -> dict[str, Any]:
        return processes.system_info()

    # ---------------------------------------------------------------- Control

    @router.post("/control/halt")
    def halt(body: HaltBody) -> dict[str, Any]:
        engine.halt(actor="user", via=body.via)
        return {"halted": True}

    @router.post("/control/resume")
    def resume() -> dict[str, Any]:
        engine.resume(actor="user")
        return {"halted": False}

    @router.put("/control/autonomy")
    def autonomy(body: AutonomyBody) -> dict[str, Any]:
        return {"autonomy": int(engine.set_autonomy(body.level, actor="user"))}

    # ---------------------------------------------------------------- Approvals

    @router.get("/approvals")
    def approvals(
        status: Literal["pending", "approved", "rejected", "expired"] = "pending",
    ) -> list[dict[str, Any]]:
        return engine.list_approvals(status)

    @router.post("/approvals/{approval_id}/approve")
    def approve(approval_id: str, body: ApproveBody | None = None) -> dict[str, Any]:
        pin = body.pin if body else None
        return engine.approve(approval_id, actor="user", pin=pin).to_dict()

    @router.post("/approvals/{approval_id}/reject")
    def reject(approval_id: str) -> dict[str, Any]:
        if not engine.reject(approval_id, actor="user"):
            raise HTTPException(404, "approval not found")
        return {"rejected": True}

    @router.get("/undo")
    def undo_list() -> list[dict[str, Any]]:
        return engine.list_undo()

    @router.post("/undo/{undo_id}")
    def undo(undo_id: str) -> dict[str, Any]:
        return engine.undo(undo_id, actor="user").to_dict()

    # ---------------------------------------------------------------- Audit

    @router.get("/audit")
    def audit(limit: int = 100, offset: int = 0) -> list[dict[str, Any]]:
        return svc.audit.list(limit=max(1, min(limit, 500)), offset=max(0, offset))

    @router.get("/audit/verify")
    def audit_verify() -> dict[str, bool]:
        return {"valid": svc.audit.verify()}

    # ---------------------------------------------------------------- Actions

    @router.get("/actions")
    def actions() -> list[dict[str, Any]]:
        return [
            {
                "name": s.name,
                "title": s.title,
                "risk": s.risk,
                "description": s.description,
                "llm_exposed": s.llm_exposed,
                "undo": s.undo is not None,
            }
            for s in svc.registry.all()
        ]

    @router.post("/actions/{name}")
    def run_action(name: str, body: ActionBody) -> dict[str, Any]:
        """Direct operation from the UI (source USER)."""
        return engine.request(name, body.params, source=Source.USER, actor="user").to_dict()

    # ---------------------------------------------------------------- Chat

    @router.get("/chat/history")
    def chat_history(conversation: str = "default") -> list[dict[str, Any]]:
        return svc.chat.history(conversation)

    @router.delete("/chat/history")
    def chat_clear(conversation: str = "default") -> dict[str, bool]:
        svc.chat.clear(conversation)
        return {"cleared": True}

    # ---------------------------------------------------------------- Memory

    @router.get("/memory")
    def memory_list() -> list[dict[str, Any]]:
        return svc.memory.list()

    @router.get("/memory/search")
    def memory_search(q: str) -> list[dict[str, Any]]:
        return svc.memory.search(q[:200], limit=10)

    @router.post("/memory")
    def memory_add(body: MemoryBody) -> dict[str, Any]:
        try:
            return svc.memory.add(body.text, category=body.category, source="ui")
        except MemoryRejected as exc:
            raise HTTPException(422, str(exc)) from exc

    @router.put("/memory/{memory_id}")
    def memory_update(memory_id: int, body: MemoryBody) -> dict[str, Any]:
        try:
            return svc.memory.update(memory_id, body.text, body.category)
        except MemoryRejected as exc:
            raise HTTPException(422, str(exc)) from exc
        except KeyError as exc:
            raise HTTPException(404, "memory not found") from exc

    @router.delete("/memory/{memory_id}")
    def memory_delete(memory_id: int) -> dict[str, bool]:
        if not svc.memory.delete(memory_id):
            raise HTTPException(404, "memory not found")
        return {"deleted": True}

    @router.delete("/memory")
    def memory_delete_all() -> dict[str, bool]:
        svc.memory.delete_all()
        svc.audit.append(
            actor="user", source="user", action="memory_delete_all", decision="execute"
        )
        return {"deleted": True}

    # ---------------------------------------------------------------- Setup

    def setup_state() -> dict[str, Any]:
        from sentinel.automation import web
        from sentinel.brain import backup as brain_backup
        from sentinel.brain import profile
        from sentinel.llm import factory
        from sentinel.pcsec import filescan
        from sentinel.voice import elevenlabs

        saved = profile.saved(svc)
        return {
            "home_city": svc.store.get("home_city") or svc.config.home_city or "",
            "google_account": web.load_choice(svc.config.config_dir).get("google_account", ""),
            "quiet_hours": svc.notifier.quiet_hours(),
            "connected": {
                "claude": bool(factory.claude_key(svc.config)),
                "openrouter": bool(factory.openrouter_key(svc.config)),
                "elevenlabs": bool(elevenlabs.stored_key()),
                "gmail": bool(svc.mail.configured),
                "abusech": bool(filescan.abusech_key()),
                "brain_backup": brain_backup.stored_key() is not None,
                "server": bool(svc.server.configured),
                "camera": svc.camera.configured,
            },
            "profile": {
                "saved": saved is not None,
                "nodes": len((saved or {}).get("nodes", [])),
                "reminders": len((saved or {}).get("reminders", [])),
            },
        }

    @router.get("/setup")
    def setup_get() -> dict[str, Any]:
        return setup_state()

    @router.put("/setup")
    def setup_put(body: SetupBody) -> dict[str, Any]:
        from sentinel.automation import web
        from sentinel.notify.service import QUIET_RE

        if body.home_city is not None:
            city = body.home_city.strip()
            if city and not re.fullmatch(r"[\w .'\-]{2,60}", city):
                raise HTTPException(400, "Invalid city name")
            svc.store.set("home_city", city)
        if body.google_account is not None:
            account = body.google_account.strip()
            if account and not web.valid_account(account):
                raise HTTPException(400, "That is not an email address")
            web.save_choice(svc.config.config_dir, google_account=account)
        if body.quiet_hours is not None:
            value = body.quiet_hours.strip()
            if value and not QUIET_RE.match(value):
                raise HTTPException(400, "Format: 23:00-07:00")
            svc.store.set("quiet_hours", value)
        svc.audit.append(
            actor="user",
            source="user",
            action="setup_change",
            decision="execute",
            params={k: v for k, v in body.model_dump().items() if v is not None},
        )
        return setup_state()

    @router.post("/brain/profile")
    def brain_profile_upload(body: dict[str, Any]) -> dict[str, Any]:
        """Import a profile from the app (the user's own file) and store it safely."""
        from sentinel.brain import profile

        if not isinstance(body.get("nodes", []), list):
            raise HTTPException(400, "Not a valid profile")
        result = profile.apply(svc, body)
        profile.save_copy(svc, body)
        return result

    @router.post("/brain/profile/reload")
    def brain_profile_reload() -> dict[str, Any]:
        from sentinel.brain import profile

        data = profile.saved(svc)
        if data is None:
            raise HTTPException(404, "No saved profile")
        return profile.apply(svc, data)

    # ---------------------------------------------------------------- External agents (Hermes)

    @router.get("/agent/status")
    def external_status() -> dict[str, Any]:
        return {
            "halted": engine.halted,
            "autonomy": int(engine.autonomy),
            "pending_approvals": len(engine.list_approvals()),
        }

    @router.get("/agent/tools")
    def external_tools() -> list[dict[str, Any]]:
        tools = []
        for spec in svc.registry.all():
            if not spec.llm_exposed:
                continue
            schema = spec.params.model_json_schema()
            schema.pop("title", None)
            schema.setdefault("properties", {})
            tools.append(
                {
                    "name": spec.name,
                    "description": f"[{spec.risk}] {spec.description}",
                    "input_schema": schema,
                }
            )
        return sorted(tools, key=lambda t: t["name"])

    @router.post("/agent/call")
    async def external_call(body: ExternalCallBody) -> dict[str, Any]:
        from sentinel.llm.untrusted import wrap_untrusted

        outcome = await asyncio.to_thread(
            engine.request,
            body.action,
            body.params,
            source=Source.EXTERNAL,
            actor="agent:hermes",
            reason=body.reason or "Hermes",
        )
        result = outcome.to_dict()
        if outcome.untrusted and outcome.status == "executed":
            # Pass third-party data on only when marked as untrusted
            result["data"] = wrap_untrusted(body.action, outcome.data)
        return result

    # ---------------------------------------------------------------- Markets & KPIs

    @router.get("/markets")
    async def markets(force: bool = False) -> dict[str, Any]:
        return await svc.markets.overview(force=force)

    @router.get("/camera")
    async def camera() -> dict[str, Any]:
        return await asyncio.to_thread(svc.camera.status)

    @router.get("/camera/{index}/snapshot")
    async def camera_snapshot(index: int) -> Response:
        from sentinel.camera.onvif import CameraError

        if not 1 <= index <= 64:
            raise HTTPException(404, "Unknown camera")
        try:
            image = await asyncio.to_thread(svc.camera.snapshot, index)
        except CameraError as exc:
            raise HTTPException(503, str(exc)) from exc
        # Live images are never cached or stored
        return Response(
            content=image, media_type="image/jpeg", headers={"Cache-Control": "no-store"}
        )

    @router.get("/camera/{index}/video")
    async def camera_video(index: int, full: bool = False) -> StreamingResponse:
        from sentinel.camera import video
        from sentinel.camera.onvif import CameraError

        if not 1 <= index <= 64:
            raise HTTPException(404, "Unknown camera")
        try:
            args = await asyncio.to_thread(svc.camera.video_args, index, full)
        except CameraError as exc:
            raise HTTPException(503, str(exc)) from exc
        if video.busy():
            raise HTTPException(503, "Too many live videos open at once")
        # ffmpeg starts when the body is read and stops when the viewer leaves.
        # Raw MJPEG frames, never cached or stored.
        return StreamingResponse(
            video.mjpeg_stream(args),
            media_type="video/x-motion-jpeg",
            headers={"Cache-Control": "no-store"},
        )

    @router.put("/camera/motion")
    def camera_motion(body: CameraMotionBody) -> dict[str, Any]:
        svc.camera.set_motion(body.enabled)
        return {"motion": svc.camera.motion_enabled}

    @router.get("/markets/summary")
    async def markets_summary() -> dict[str, str]:
        return {"text": await svc.markets.summary()}

    @router.get("/kpis")
    async def kpis() -> dict[str, Any]:
        from sentinel.markets import kpis as kpi

        tiles = await asyncio.to_thread(kpi.collect, svc)
        return {"tiles": tiles, "text": kpi.spoken(tiles)}

    # ---------------------------------------------------------------- Brain

    @router.get("/brain/graph")
    def brain_graph() -> dict[str, Any]:
        return svc.brain.store.graph()

    @router.get("/brain/status")
    def brain_status() -> dict[str, Any]:
        return svc.brain.status()

    @router.get("/brain/search")
    def brain_search(q: str) -> list[dict[str, Any]]:
        return svc.brain.store.search(q[:200], limit=10)

    @router.get("/brain/nodes/{node_id}")
    def brain_node(node_id: int) -> dict[str, Any]:
        node = svc.brain.store.get(node_id)
        if node is None:
            raise HTTPException(404, "node not found")
        return {**node, "neighbors": svc.brain.store.neighbors(node_id)}

    @router.post("/brain/nodes")
    def brain_add(body: BrainNodeBody) -> dict[str, Any]:
        try:
            node = svc.brain.store.add(
                body.label, category=body.category, summary=body.summary, origin="user", source="ui"
            )
        except BrainRejected as exc:
            raise HTTPException(400, str(exc)) from exc
        svc.brain.changed()
        return node

    @router.delete("/brain/nodes/{node_id}")
    def brain_delete(node_id: int) -> dict[str, bool]:
        if not svc.brain.delete_from_ui(node_id):
            raise HTTPException(404, "node not found")
        return {"deleted": True}

    @router.post("/brain/sync")
    async def brain_sync() -> dict[str, Any]:
        await asyncio.to_thread(svc.brain.sync)
        return svc.brain.status()

    @router.post("/brain/backup")
    async def brain_backup() -> dict[str, Any]:
        try:
            return await svc.brain.backup_now()
        except BackupError as exc:
            raise HTTPException(400, str(exc)) from exc

    # ---------------------------------------------------------------- Security

    @router.get("/security/alerts")
    def alerts(
        status: Literal["new", "ack", "all"] = "all", limit: int = 100
    ) -> list[dict[str, Any]]:
        return svc.alerts.list(None if status == "all" else status, max(1, min(limit, 500)))

    @router.post("/security/alerts/{alert_id}/ack")
    def alert_ack(alert_id: int) -> dict[str, bool]:
        return {"ok": svc.alerts.ack(alert_id)}

    @router.post("/security/alerts/ack-all")
    def alert_ack_all() -> dict[str, bool]:
        svc.alerts.ack_all()
        return {"ok": True}

    @router.get("/security/processes")
    def security_processes(limit: int = 40, q: str | None = None) -> list[dict[str, Any]]:
        return processes.top_processes(max(1, min(limit, 300)), q)

    @router.get("/security/ports")
    def security_ports() -> list[dict[str, Any]]:
        return processes.list_listening_ports()

    @router.get("/security/autostart")
    def security_autostart() -> list[dict[str, Any]]:
        return autostart.list_autostart()

    @router.get("/security/sysmon")
    def security_sysmon(limit: int = 50) -> dict[str, Any]:
        events = svc.monitor.sysmon.read_latest(max(1, min(limit, 200)))
        return {"status": svc.monitor.sysmon.status, "events": events}

    @router.get("/security/quarantine")
    def security_quarantine() -> list[dict[str, Any]]:
        return svc.db.query("SELECT * FROM quarantine ORDER BY ts DESC LIMIT 200")

    @router.post("/security/scan")
    def security_scan() -> dict[str, Any]:
        if engine.halted:
            raise HTTPException(423, "Emergency stop active")
        findings = svc.monitor.scan_once()
        return {"findings": len(findings)}

    @router.get("/security/monitor")
    def security_monitor() -> dict[str, Any]:
        return svc.monitor.status()

    # ---------------------------------------------------------------- Network

    @router.get("/network/devices")
    def network_devices() -> dict[str, Any]:
        subnet = svc.network.subnet()
        return {"subnet": str(subnet) if subnet else None, "devices": svc.network.devices()}

    @router.post("/network/refresh")
    def network_refresh() -> dict[str, Any]:
        if engine.halted:
            raise HTTPException(423, "Emergency stop active")
        return svc.network.refresh()

    @router.get("/phone/status")
    def phone_status() -> dict[str, Any]:
        return {"paired": svc.phone.paired, "url": phone_url()}

    def phone_url() -> str | None:
        from urllib.parse import urlparse

        if not svc.config.server_agent_url:
            return None
        host = urlparse(svc.config.server_agent_url).hostname
        return f"https://{host}:{svc.config.phone_port}/m" if host else None

    @router.post("/phone/pair")
    def phone_pair(body: PhonePairBody) -> dict[str, Any]:
        from sentinel.phone.link import PhoneError

        try:
            pairing = svc.phone.pair(body.pin)
        except PhoneError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        url = phone_url()
        return {
            "code": pairing["code"],
            "link": f"{url}#{pairing['fragment']}" if url else None,
        }

    @router.post("/phone/unpair")
    def phone_unpair() -> dict[str, Any]:
        svc.phone.unpair()
        return {"paired": False}

    @router.get("/server/status")
    def server_status() -> dict[str, Any]:
        return {**svc.server.status(), "relay": svc.relay.status()}

    @router.get("/server/history")
    def server_history(days: int = 7) -> list[dict[str, Any]]:
        return svc.server.history(svc, days)

    @router.get("/server/attackers")
    def server_attackers() -> dict[str, Any]:
        return {"geoip": svc.geoip.available, "sources": svc.server.attackers(svc.geoip)}

    @router.get("/security/pc")
    def security_pc() -> dict[str, Any]:
        from sentinel.pcsec import devices as pcdevices

        return {
            "usb": pcdevices.usb_devices(),
            "drives": pcdevices.removable_drives(),
            "extensions": pcdevices.browser_extensions(),
            "camera": pcdevices.capability_usage("webcam")[:20],
            "microphone": pcdevices.capability_usage("microphone")[:20],
            "feed": {
                "hashes": svc.filescan.hash_count(),
                "last": svc.filescan.last_feed or None,
                "error": svc.filescan.feed_error,
                "enabled": svc.config.malware_feed,
            },
        }

    @router.get("/security/tests")
    def security_tests() -> list[dict[str, Any]]:
        from sentinel.monitor import simulate

        return simulate.catalog()

    @router.post("/security/tests")
    def security_test_run(body: AlertTestBody) -> dict[str, Any]:
        from sentinel.monitor import simulate

        try:
            return simulate.run_test(svc, body.rule)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.get("/security/scans")
    def security_scans() -> list[dict[str, Any]]:
        return svc.filescan.recent()

    @router.post("/security/feed/update")
    def security_feed_update() -> dict[str, Any]:
        return {
            "loaded": svc.filescan.update_feed(),
            "hashes": svc.filescan.hash_count(),
            "error": svc.filescan.feed_error,
        }

    @router.get("/security/certs")
    def security_certs() -> dict[str, Any]:
        return {
            "targets": svc.certs.targets(),
            "results": svc.certs.status(),
            "last_check": svc.certs.last_check or None,
        }

    @router.post("/security/certs/check")
    def security_certs_check() -> dict[str, Any]:
        svc.certs.check()
        return security_certs()

    @router.get("/network/data")
    def network_data() -> dict[str, bool]:
        return {"geoip": svc.geoip.available, "oui": svc.oui.available}

    @router.post("/server/refresh")
    async def server_refresh() -> dict[str, Any]:
        try:
            svc.server.last_poll = 0.0
            await svc.server.poll_and_check(svc)
            if svc.server.last is None:
                await svc.server.fetch()
        except Exception as exc:
            svc.server.error = str(exc) or type(exc).__name__
        return svc.server.status()

    # ---------------------------------------------------------------- Automation

    @router.get("/automation/programs")
    def programs() -> list[dict[str, Any]]:
        return [
            {"name": p.name, "title": p.title, "path": str(p.path)}
            for p in svc.programs.all().values()
        ]

    @router.get("/automation/routines")
    def routines() -> list[dict[str, Any]]:
        return [
            {
                "name": r.name,
                "title": r.title,
                "origin": r.origin,
                "steps": [{"action": a, "params": p} for a, p in r.steps],
            }
            for r in svc.routines.all().values()
        ]

    @router.get("/automation/reminders")
    def reminders() -> list[dict[str, Any]]:
        return svc.reminders.list()

    @router.get("/automation/modes")
    def modes() -> dict[str, Any]:
        cfg = svc.config
        return {
            **svc.modes.status(),
            "cleanup": {
                "enabled": cfg.downloads_cleanup,
                "day": cfg.downloads_cleanup_day,
                "time": cfg.downloads_cleanup_time,
                "folders": [str(f) for f in cfg.managed_folders],
                "last_week": svc.store.get("downloads_cleanup_week"),
            },
        }

    # ---------------------------------------------------------------- Messages

    @router.get("/news/briefing")
    async def briefing(speak: bool = False) -> dict[str, Any]:
        result = await svc.news.briefing()
        if speak:
            svc.voice.say(result["text"])
        return result

    # ---------------------------------------------------------------- Order suggestions

    @router.get("/shop/orders")
    def shop_orders() -> list[dict[str, Any]]:
        return svc.shop.list()

    @router.post("/shop/research")
    def shop_research(body: ShopBody) -> dict[str, Any]:
        result = engine.request(
            "shop_research", {"query": body.query}, source=Source.USER, actor="user"
        )
        if result.status != "executed":
            raise HTTPException(422, result.message)
        return svc.shop.get(int(result.data["order_id"])) or {}

    @router.post("/shop/orders/{order_id}/open")
    def shop_open(order_id: int) -> dict[str, Any]:
        return engine.request(
            "open_order", {"order_id": order_id}, source=Source.USER, actor="user"
        ).to_dict()

    # ---------------------------------------------------------------- Weather, calendar, reports

    @router.get("/weather/home")
    async def weather_home() -> dict[str, Any]:
        return await svc.weather.home_weather()

    @router.get("/mail/parcels")
    def mail_parcels(refresh: bool = False) -> dict[str, Any]:
        from sentinel.mail.service import MailError

        if not svc.mail.configured:
            return {"configured": False, "parcels": []}
        try:
            return {"configured": True, "parcels": svc.mail.parcels(force=refresh)}
        except MailError as exc:
            return {"configured": True, "parcels": [], "error": str(exc)}

    @router.get("/shop/prices")
    def shop_prices() -> list[dict[str, Any]]:
        return svc.prices.list()

    @router.get("/shop/list")
    def shop_list() -> list[dict[str, Any]]:
        return svc.shopping.items()

    @router.get("/mail/unread")
    def mail_unread(refresh: bool = False) -> dict[str, Any]:
        from sentinel.mail.service import MailError

        if not svc.mail.configured:
            return {"configured": False, "mails": []}
        try:
            return {
                "configured": True,
                "address": svc.mail.address,
                "mails": svc.mail.unread(force=refresh),
            }
        except MailError as exc:
            return {"configured": True, "mails": [], "error": str(exc)}

    @router.get("/briefing/morning")
    async def briefing_morning() -> dict[str, Any]:
        return await svc.reports.morning()

    @router.post("/reports/daily")
    def report_daily() -> dict[str, Any]:
        return svc.reports.daily_report(notify=False)

    @router.get("/news/tour")
    async def news_tour() -> dict[str, Any]:
        return await svc.tour.tour()

    # ---------------------------------------------------------------- Speech

    @router.post("/client/log")
    def client_log(body: ClientLogBody) -> dict[str, bool]:
        """Errors from the UI end up in the backend log (redacted)."""
        from sentinel.secrets_filter import redact

        logging.getLogger("sentinel.client").warning("%s: %s", body.where, redact(body.message))
        return {"ok": True}

    @router.post("/voice/confirm")
    async def voice_confirm() -> dict[str, Any]:
        """Thumbs up: only confirms an open conversation question (e.g. "Forget ...?")."""
        return {"text": await svc.brain.handle_command("yes", via="voice")}

    @router.post("/voice/say")
    def voice_say(body: SayBody) -> dict[str, Any]:
        """Speaks text via Piper and waits until it finishes (for the world tour)."""
        return {"spoken": svc.voice.say_blocking(body.text)}

    @router.post("/voice/tts")
    async def voice_tts(body: SayBody) -> Response:
        """Text as MP3 from the online voice (the app plays it)."""
        if svc.config.tts_engine != "edge":
            raise HTTPException(status_code=404, detail="Online voice not active")
        # ElevenLabs first (if set up), otherwise (or on error) the Microsoft voice
        if svc.eleven.configured:
            try:
                audio = await svc.eleven.synthesize(body.text)
                return Response(content=audio, media_type="audio/mpeg")
            except RuntimeError:
                pass
        if not svc.edge_voice.available():
            raise HTTPException(status_code=503, detail="No online voice available")
        try:
            audio = await svc.edge_voice.synthesize(body.text)
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        return Response(content=audio, media_type="audio/mpeg")

    @router.get("/voice/devices")
    def voice_devices() -> dict[str, Any]:
        return svc.voice.devices()

    @router.post("/voice/device")
    def voice_device(body: VoiceDeviceBody) -> dict[str, Any]:
        return svc.voice.set_device(body.name, body.api)

    @router.get("/voice/level")
    def voice_level() -> dict[str, Any]:
        """Microphone level for the display (just a number, no audio)."""
        return {"level": round(svc.voice.level, 4), "state": svc.voice.state}

    @router.post("/voice/speaking")
    def voice_speaking(body: VoiceEnableBody) -> dict[str, bool]:
        svc.voice.set_speaking(body.on)
        return {"ok": True}

    @router.post("/voice/enable")
    def voice_enable(body: VoiceEnableBody) -> dict[str, Any]:
        return svc.voice.set_enabled(body.on)

    @router.get("/voice/status")
    def voice_status() -> dict[str, Any]:
        return svc.voice.status()

    @router.post("/voice/listen")
    def voice_listen() -> dict[str, bool]:
        return {"listening": svc.voice.listen_once()}

    @router.post("/voice/ptt/start")
    def ptt_start() -> dict[str, bool]:
        return {"recording": svc.voice.ptt_start()}

    @router.post("/voice/ptt/stop")
    def ptt_stop() -> dict[str, bool]:
        svc.voice.ptt_stop()
        return {"recording": False}

    # ---------------------------------------------------------------- Agents

    @router.get("/agents")
    def agents() -> list[dict[str, Any]]:
        return svc.agents.list()

    @router.get("/agents/reports")
    def agent_reports() -> list[dict[str, Any]]:
        return svc.agents.reports()

    @router.post("/agents/{name}/run")
    async def agent_run(name: str, body: AgentBody) -> dict[str, Any]:
        try:
            return await svc.agents.run(name, body.goal)
        except AgentBlocked as exc:
            raise HTTPException(423, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    # ---------------------------------------------------------------- Plugins & settings

    @router.get("/plugins")
    def plugins() -> list[dict[str, Any]]:
        return svc.plugins.list()

    @router.put("/plugins/{name}")
    def plugin_toggle(name: str, body: PluginBody) -> dict[str, Any]:
        try:
            svc.plugins.set_enabled(name, body.enabled)
        except KeyError as exc:
            raise HTTPException(404, "plugin not found") from exc
        return {"name": name, "enabled": body.enabled}

    # ---------------------------------------------------------------- PIN & defense commands

    @router.get("/pin")
    def pin_status() -> dict[str, Any]:
        return svc.pin.status()

    @router.post("/pin")
    def pin_set(body: PinBody) -> dict[str, Any]:
        from sentinel.pin import PinError

        try:
            svc.pin.set_pin(body.new_pin, body.old_pin, actor="user")
        except PinError as exc:
            raise HTTPException(422, str(exc)) from exc
        return svc.pin.status()

    @router.get("/response/plugins")
    def response_plugins() -> list[dict[str, Any]]:
        return svc.responses.list()

    @router.post("/response/plugins/reload")
    def response_reload() -> list[dict[str, Any]]:
        svc.responses.reload()
        return svc.responses.list()

    @router.post("/response/plugins/approve")
    def response_approve(body: ResponseApproveBody) -> list[dict[str, Any]]:
        if not svc.responses.approve(body.file, body.sha256, body.pin, actor="user"):
            raise HTTPException(403, "PIN wrong/locked or file has changed")
        return svc.responses.list()

    @router.post("/response/plugins/revoke")
    def response_revoke(body: ResponseRevokeBody) -> list[dict[str, Any]]:
        svc.responses.revoke(body.file, actor="user")
        return svc.responses.list()

    @router.get("/security/alerts/{alert_id}/responses")
    def alert_responses(alert_id: int) -> list[dict[str, Any]]:
        from sentinel.permissions.models import ActionDenied

        try:
            return svc.responses.matching(alert_id)
        except ActionDenied as exc:
            raise HTTPException(404, str(exc)) from exc

    @router.get("/settings")
    def settings() -> dict[str, Any]:
        cfg = svc.config
        subnet = svc.network.subnet()
        return {
            "user_name": cfg.user_name,
            "llm_model": cfg.llm_model,
            "llm_effort": cfg.llm_effort,
            "api_key_set": bool(cfg.anthropic_api_key),
            "budget_daily_eur": cfg.budget_daily_eur,
            "budget_monthly_eur": cfg.budget_monthly_eur,
            "managed_folders": [str(p) for p in cfg.managed_folders],
            "watched_folders": [str(p) for p in cfg.watched_folders],
            "home_subnet": str(subnet) if subnet else None,
            "subnet_configured": cfg.home_subnet is not None,
            "ntfy_configured": svc.notifier.ntfy_configured(),
            "server_configured": svc.server.configured,
            "voice_enabled": cfg.voice_enabled,
            "home_city": cfg.home_city,
            "gmail_configured": svc.mail.configured,
            "evening_report_time": cfg.evening_report_time,
            "data_dir": str(cfg.data_dir),
            "config_dir": str(cfg.config_dir),
        }

    @router.post("/notify/test")
    def notify_test() -> dict[str, Any]:
        svc.notifier.notify("SENTINEL", "Test notification", push=False)
        return svc.notifier.test_push()

    return router
