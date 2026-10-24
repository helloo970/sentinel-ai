"""Agents: security analyst, network watch, server admin, planner.

Analysis agents collect data with fixed code and let the model assess it without tools
(the data is marked). The planner only works with the user's goal and creates steps as
approvals – agents never execute anything themselves.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from dataclasses import asdict
from typing import TYPE_CHECKING, Any

from sentinel.llm.base import LLMError
from sentinel.llm.prompts import ANALYST_SYSTEM, PLANNER_SYSTEM
from sentinel.llm.untrusted import wrap_untrusted
from sentinel.monitor import autostart, processes
from sentinel.permissions.models import Risk, Source

if TYPE_CHECKING:
    from sentinel.services import Services

log = logging.getLogger(__name__)

AGENTS: dict[str, dict[str, str]] = {
    "security_analyst": {
        "title": "Security analyst",
        "description": "Assesses alerts, autostart, ports and processes of the PC.",
    },
    "network_watch": {
        "title": "Network watch",
        "description": "Checks the device inventory of the home network.",
    },
    "server_admin": {
        "title": "Server admin",
        "description": "Assesses the health and security of the Ubuntu server.",
    },
    "planner": {
        "title": "Planner",
        "description": "Breaks a goal down into steps and submits them for approval.",
    },
}

PLAN_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "steps": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "action": {"type": "string"},
                    "params_json": {"type": "string"},
                    "why": {"type": "string"},
                },
                "required": ["title", "action", "params_json", "why"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["summary", "steps"],
    "additionalProperties": False,
}
MAX_PLAN_STEPS = 6


class AgentBlocked(RuntimeError):  # noqa: N818
    pass


class AgentService:
    def __init__(self, svc: Services) -> None:
        self.svc = svc
        self._task: asyncio.Task[None] | None = None
        self.running: set[str] = set()

    def list(self) -> list[dict[str, Any]]:
        return [{"name": k, **v, "running": k in self.running} for k, v in AGENTS.items()]

    def reports(self, limit: int = 50) -> list[dict[str, Any]]:
        return self.svc.db.query("SELECT * FROM agent_reports ORDER BY ts DESC LIMIT ?", (limit,))

    def _store(self, agent: str, title: str, body: str) -> dict[str, Any]:
        report_id = self.svc.db.execute(
            "INSERT INTO agent_reports(agent, ts, title, body) VALUES(?,?,?,?)",
            (agent, time.time(), title, body),
        )
        self.svc.bus.publish("agent_report", {"id": report_id, "agent": agent, "title": title})
        return {"id": report_id, "agent": agent, "title": title, "body": body}

    # ---------------------------------------------------------------- Collecting data

    def _security_data(self) -> dict[str, Any]:
        procs = [asdict(p) for p in processes.list_processes()]
        return {
            "open_alerts": [
                {k: a[k] for k in ("ts", "rule", "severity", "title", "detail", "response")}
                for a in self.svc.alerts.list("new", limit=40)
            ],
            "suspicious_location_processes": [
                {"pid": p["pid"], "name": p["name"], "exe": p["exe"]}
                for p in procs
                if p["exe"] and processes.is_suspicious_location(p["exe"])
            ][:20],
            "public_listeners": [p for p in processes.list_listening_ports() if p["public"]][:40],
            "autostart_entries": autostart.list_autostart()[:60],
            "sysmon": self.svc.monitor.sysmon.status,
        }

    def _network_data(self) -> dict[str, Any]:
        subnet = self.svc.network.subnet()
        return {
            "subnet": str(subnet) if subnet else None,
            "devices": self.svc.network.devices()[:200],
        }

    async def _server_data(self) -> dict[str, Any]:
        if not self.svc.server.configured:
            return {"configured": False}
        try:
            return await self.svc.server.fetch()
        except Exception as exc:
            return {"error": str(exc)}

    # ---------------------------------------------------------------- Running

    async def run(
        self, agent: str, goal: str | None = None, *, scheduled: bool = False
    ) -> dict[str, Any]:
        if agent not in AGENTS:
            raise ValueError("unknown agent")
        if self.svc.engine.halted:
            raise AgentBlocked("Emergency stop active – agents are blocked")
        if agent in self.running:
            raise AgentBlocked("Agent is already running")
        self.running.add(agent)
        self.svc.bus.publish("agent", {"agent": agent, "running": True})
        try:
            if agent == "planner":
                if not goal or not goal.strip():
                    raise ValueError("The planner needs a goal")
                return await self._plan(goal.strip()[:2000])
            if agent == "security_analyst":
                data = await asyncio.to_thread(self._security_data)
            elif agent == "network_watch":
                await asyncio.to_thread(self.svc.network.refresh)
                data = await asyncio.to_thread(self._network_data)
            else:
                data = await self._server_data()
            if scheduled and self.svc.config.ai_only_in_chat:
                # Schedule without AI: report from the raw data
                title = f"{AGENTS[agent]['title']} – report"
                return self._store(agent, title, self._fallback_report(agent, data))
            return await self._analyze(agent, data)
        finally:
            self.running.discard(agent)
            self.svc.bus.publish("agent", {"agent": agent, "running": False})

    def _fallback_report(self, agent: str, data: dict[str, Any]) -> str:
        if agent == "security_analyst":
            alerts = data.get("open_alerts", [])
            critical = sum(1 for a in alerts if a["severity"] == "critical")
            return (
                f"Offline report (no AI): {len(alerts)} open alerts, {critical} of them "
                f"critical. {len(data.get('public_listeners', []))} open ports, "
                f"{len(data.get('autostart_entries', []))} autostart entries, "
                f"Sysmon: {data.get('sysmon')}."
            )
        if agent == "network_watch":
            devices = data.get("devices", [])
            unknown = [d for d in devices if not d["trusted"]]
            return (
                f"Offline report (no AI): {len(devices)} devices in the subnet "
                f"{data.get('subnet')}, {len(unknown)} of them not marked as trusted."
            )
        return f"Offline report (no AI): {json.dumps(data, ensure_ascii=False)[:800]}"

    async def _analyze(self, agent: str, data: dict[str, Any]) -> dict[str, Any]:
        title = f"{AGENTS[agent]['title']} – report"
        ok, _ = self.svc.costs.check()
        if not self.svc.llm.available or not ok:
            return self._store(agent, title, self._fallback_report(agent, data))
        prompt = "Assess the following data and write the status report.\n" + wrap_untrusted(
            agent, data
        )
        try:
            result = await self.svc.llm.complete(
                system=ANALYST_SYSTEM, prompt=prompt, max_tokens=4000
            )
        except LLMError as exc:
            return self._store(agent, title, f"{self._fallback_report(agent, data)}\n\n({exc})")
        self.svc.costs.record(result.model, f"agent:{agent}", result.usage)
        body = result.text.strip() or self._fallback_report(agent, data)
        return self._store(agent, title, body)

    def _catalog(self) -> list[dict[str, Any]]:
        catalog = []
        for spec in self.svc.registry.all():
            if not spec.llm_exposed or spec.risk is Risk.FINAL:
                continue
            schema = spec.params.model_json_schema()
            catalog.append(
                {
                    "action": spec.name,
                    "risk": spec.risk,
                    "description": spec.description,
                    "params": schema.get("properties", {}),
                }
            )
        return catalog

    async def _plan(self, goal: str) -> dict[str, Any]:
        if not self.svc.llm.available:
            return self._store("planner", "Plan", "The planner needs the Claude API (offline).")
        ok, reason = self.svc.costs.check()
        if not ok:
            return self._store("planner", "Plan", f"Paused: {reason}")
        who = self.svc.config.user_name.strip() or "the user"
        prompt = f"Goal of {who}: {goal}\n\nAllowed actions:\n" + json.dumps(
            self._catalog(), ensure_ascii=False
        )
        try:
            result = await self.svc.llm.complete(
                system=PLANNER_SYSTEM, prompt=prompt, max_tokens=4000, json_schema=PLAN_SCHEMA
            )
        except LLMError as exc:
            return self._store("planner", "Plan", f"Planning failed: {exc}")
        self.svc.costs.record(result.model, "agent:planner", result.usage)
        try:
            plan = json.loads(result.text)
        except json.JSONDecodeError:
            return self._store("planner", "Plan", result.text or "No plan received")
        lines = [str(plan.get("summary", ""))]
        for index, step in enumerate(plan.get("steps", [])[:MAX_PLAN_STEPS], start=1):
            line = f"{index}. {step.get('title', '')}"
            action = step.get("action", "manual")
            if action != "manual":
                try:
                    params = json.loads(step.get("params_json") or "{}")
                except json.JSONDecodeError:
                    params = None
                if isinstance(params, dict):
                    outcome = await asyncio.to_thread(
                        self.svc.engine.request,
                        action,
                        params,
                        source=Source.AGENT,
                        actor="agent:planner",
                        reason=str(step.get("why", ""))[:300],
                    )
                    line += f" → {action}: {outcome.status} ({outcome.message})"
                else:
                    line += f" → {action}: invalid parameters"
            else:
                line += " (manual)"
            lines.append(line)
        return self._store("planner", f"Plan: {goal[:60]}", "\n".join(lines))

    # ---------------------------------------------------------------- Schedule

    async def _loop(self) -> None:
        hours = self.svc.config.agents_schedule_hours
        while True:
            await asyncio.sleep(hours * 3600)
            if self.svc.engine.halted:
                continue
            with contextlib.suppress(Exception):
                await self.run("security_analyst", scheduled=True)

    def start(self) -> None:
        if self.svc.config.agents_schedule_hours > 0:
            self._task = asyncio.get_running_loop().create_task(self._loop())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
