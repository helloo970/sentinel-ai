"""Protection of the local API: session token, Host header, Origin.

Implemented as pure ASGI middleware so long streams (chat, events) run cleanly.
"""

from __future__ import annotations

import hmac
import json
import secrets

from starlette.types import ASGIApp, Receive, Scope, Send

PUBLIC_PATHS = {"/health"}


def generate_token() -> str:
    return secrets.token_urlsafe(32)


class LocalGuardMiddleware:
    """Host must be 127.0.0.1/localhost, Origin must be the own app, token must match."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        token: str,
        port: int,
        allowed_origins: list[str],
        agent_token: str | None = None,
    ) -> None:
        self.app = app
        self._token = token.encode()
        # Separate token for external agents (Hermes): only valid for /agent/...
        self._agent_token = agent_token.encode() if agent_token else None
        self._hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
        self._origins = set(allowed_origins)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return

        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope["headers"]}
        if headers.get("host", "") not in self._hosts:
            # Protection against DNS rebinding
            await _reject(send, 421, "invalid host")
            return
        origin = headers.get("origin")
        if origin is not None and origin not in self._origins:
            await _reject(send, 403, "origin not allowed")
            return
        # CORS preflight arrives without a token and is answered by the CORS middleware
        if scope.get("method") == "OPTIONS" or scope["path"] in PUBLIC_PATHS:
            await self.app(scope, receive, send)
            return

        scheme, _, value = headers.get("authorization", "").partition(" ")
        if scheme.lower() != "bearer":
            await _reject(send, 401, "unauthorized")
            return
        if not hmac.compare_digest(value.encode(), self._token):
            agent_ok = (
                self._agent_token is not None
                and scope["path"].startswith("/agent/")
                and hmac.compare_digest(value.encode(), self._agent_token)
            )
            if not agent_ok:
                await _reject(send, 401, "unauthorized")
                return
        await self.app(scope, receive, send)


async def _reject(send: Send, status: int, detail: str) -> None:
    body = json.dumps({"detail": detail}).encode()
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})
