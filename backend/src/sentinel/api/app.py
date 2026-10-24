"""FastAPI app: bound to 127.0.0.1 only, session token, CORS only for the own app."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from sentinel import __version__
from sentinel.api import routes
from sentinel.api.security import LocalGuardMiddleware
from sentinel.services import Services

log = logging.getLogger(__name__)


def sse(event: dict[str, Any]) -> bytes:
    return f"data: {json.dumps(event, ensure_ascii=False, default=str)}\n\n".encode()


def create_app(
    svc: Services, *, token: str, start_background: bool = True, agent_token: str | None = None
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        svc.bus.bind_loop(asyncio.get_running_loop())
        if start_background:
            svc.monitor.start()
            svc.reminders.start()
            svc.scheduler.start()
            svc.relay.start()
            svc.agents.start()
            svc.reports.start()
            svc.voice.start()
            svc.brain.start()
        try:
            yield
        finally:
            if start_background:
                await svc.monitor.stop()
                await svc.reminders.stop()
                await svc.scheduler.stop()
                await svc.relay.stop()
                await svc.agents.stop()
                await svc.reports.stop()
                svc.voice.stop()
                await svc.brain.stop()
            svc.db.close()

    app = FastAPI(
        title="SENTINEL AI backend",
        version=__version__,
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.svc = svc
    app.add_middleware(
        LocalGuardMiddleware,
        token=token,
        port=svc.config.port,
        allowed_origins=svc.config.allowed_origins,
        agent_token=agent_token,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=svc.config.allowed_origins,
        allow_methods=["GET", "POST", "PUT", "DELETE"],
        allow_headers=["authorization", "content-type"],
        allow_credentials=False,
        max_age=600,
    )

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    @app.get("/events")
    async def events(request: Request) -> StreamingResponse:
        async def stream() -> AsyncIterator[bytes]:
            yield sse({"kind": "hello", "data": {"version": __version__}})
            subscription = svc.bus.subscribe()
            while True:
                try:
                    event = await asyncio.wait_for(anext(subscription), timeout=15)
                    yield sse(event)
                except TimeoutError:
                    if await request.is_disconnected():
                        break
                    yield b": ping\n\n"

        return StreamingResponse(
            stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"}
        )

    @app.post("/chat")
    async def chat(body: routes.ChatBody) -> StreamingResponse:
        queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()

        async def emit(event: dict[str, Any]) -> None:
            await queue.put(event)

        async def run() -> None:
            try:
                await svc.chat.send(body.message, conversation=body.conversation, emit=emit)
            except ValueError as exc:
                await queue.put({"type": "error", "message": str(exc)})
            except Exception:
                log.exception("chat failed")
                await queue.put({"type": "error", "message": "Internal chat error"})
            finally:
                await queue.put(None)

        task = asyncio.create_task(run())

        async def stream() -> AsyncIterator[bytes]:
            try:
                while (event := await queue.get()) is not None:
                    yield sse(event)
            finally:
                if not task.done():
                    await task

        return StreamingResponse(
            stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"}
        )

    app.include_router(routes.build_router(svc))
    return app
