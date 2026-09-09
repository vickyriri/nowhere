"""Single-process Render entry point: observer + existing HTTP API + /mcp."""
from __future__ import annotations

import asyncio
import base64
from contextlib import asynccontextmanager
import hmac
import json
import logging
import os

import anyio
from starlette.applications import Starlette
from starlette.responses import JSONResponse, PlainTextResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from nowhere.persistence import R2Persistence, pending_jobs

logger = logging.getLogger(__name__)


class DurableRequests:
    """Serialize the single shared world and commit before delivering responses.

    FastMCP uses stateless JSON HTTP responses, so there is no SSE connection
    held open by this wrapper. No timers or outgoing requests run while idle.
    """
    def __init__(self, app, store, server):
        self.app, self.store, self.server = app, store, server
        self.lock = asyncio.Lock()
        self.pending = False
        self.state_dirty = False

    async def _flush(self):
        if self.state_dirty:
            await anyio.to_thread.run_sync(self.server._state.save)
            self.state_dirty = False
        await anyio.to_thread.run_sync(self.store.sync)
        self.pending = False

    async def __call__(self, scope, receive, send):
        path = scope.get("path", "")
        if (
            scope["type"] != "http"
            or path in ("/", "/health")
            or path.startswith("/static/")
            or path.startswith("/thanks")
        ):
            return await self.app(scope, receive, send)
        # Streamable HTTP permits 405 for the optional GET/SSE stream. FastMCP
        # still exposes that stream even with stateless_http + json_response;
        # reject it before acquiring the world lock or buffering a response.
        if path.rstrip("/") == "/mcp" and scope.get("method") != "POST":
            return await PlainTextResponse("Use POST for MCP requests", status_code=405,
                                           headers={"Allow": "POST"})(scope, receive, send)
        async with self.lock:
            try:
                # Retry a failed save before permitting another action, never
                # replay the action itself. This is request-driven only.
                if self.pending:
                    await self._flush()
            except Exception:
                return await self._unavailable(scope, receive, send)

            messages = []
            jobs = []
            token = pending_jobs.set(jobs)
            before = json.dumps(self.server._state.to_dict(), sort_keys=True)
            app_error = None
            save_error = None

            async def capture(message):
                messages.append(message)

            try:
                await self.app(scope, receive, capture)
            except BaseException as exc:
                app_error = exc
            finally:
                # Even an interrupted/failed request may already have changed
                # files. Finish action-triggered writes before the R2 snapshot.
                with anyio.CancelScope(shield=True):
                    try:
                        self.pending = True
                        for job in jobs:
                            try:
                                await anyio.to_thread.run_sync(job)
                            except Exception:
                                logger.exception("Optional postcard rendering failed")
                        after = json.dumps(self.server._state.to_dict(), sort_keys=True)
                        if before != after:
                            self.state_dirty = True
                        await self._flush()
                    except Exception as exc:
                        save_error = exc
                        logger.exception("R2 commit failed; response withheld")
                pending_jobs.reset(token)
            if save_error:
                return await self._unavailable(scope, receive, send)
            if app_error:
                raise app_error
            for message in messages:
                await send(message)

    async def _unavailable(self, scope, receive, send):
        await JSONResponse({
            "error": "persistence_unavailable",
            "message": "进度尚未确认保存到 R2。请勿重复刚才的动作；检查存储后先查询状态。",
        }, status_code=503)(scope, receive, send)


class OptionalAccessToken:
    """Optional bearer access for MCP/API, HTTP Basic for the observer."""
    def __init__(self, app, token):
        self.app, self.token = app, token

    async def __call__(self, scope, receive, send):
        path = scope.get("path", "")
        if (
            scope["type"] != "http"
            or path == "/health"
            or path.startswith("/thanks")
            or not self.token
        ):
            return await self.app(scope, receive, send)
        header = dict(scope.get("headers", [])).get(b"authorization", b"").decode("latin1")
        supplied = ""
        try:
            scheme, value = header.split(" ", 1)
            if scheme.lower() == "bearer":
                supplied = value
            elif scheme.lower() == "basic":
                supplied = base64.b64decode(value, validate=True).decode().split(":", 1)[1]
        except (ValueError, UnicodeError):
            pass
        if not hmac.compare_digest(supplied.encode(), self.token.encode()):
            return await PlainTextResponse("Authentication required", status_code=401,
                headers={"WWW-Authenticate": 'Basic realm="Nowhere"'})(scope, receive, send)
        # Reject cross-origin browser writes (Basic credentials are ambient).
        headers = dict(scope.get("headers", []))
        origin = headers.get(b"origin")
        if origin and scope.get("method") not in ("GET", "HEAD", "OPTIONS"):
            from urllib.parse import urlsplit
            if urlsplit(origin.decode("latin1")).netloc != headers.get(b"host", b"").decode("latin1"):
                return await PlainTextResponse("Origin rejected", status_code=403)(scope, receive, send)
        await self.app(scope, receive, send)


def create_app(store=None):
    # Restore before importing modules which bind NOWHERE_HOME at import time.
    store = store or R2Persistence.from_env()
    os.environ["NOWHERE_HOME"] = str(store.home)
    store.restore()
    from nowhere import server, web

    if server.state_mod._SAVE_DIR.resolve() != store.home:
        raise RuntimeError("Use one Nowhere home per process; restart to change NOWHERE_HOME")
    journey = store.home / "journey.json"
    if journey.exists():
        # Unlike the local convenience loader, do not silently reset bad saves.
        server._state = server.state_mod.WorldState.from_dict(json.loads(journey.read_text("utf-8")))
    else:
        server._state = server.state_mod.WorldState()
    server._postcard_counter = max(
        (c.get("id", 0) for c in server.placememory.postcards()), default=0)

    public_url = os.environ.get("NOWHERE_PUBLIC_URL") or os.environ.get("RENDER_EXTERNAL_URL")
    if public_url:
        server._web_url = public_url.rstrip("/")
        server.mcp.instructions = (server.mcp.instructions or "") + (
            f"\n网页旁观者：{server._web_url}。页面切到后台会暂停刷新。"
        )

    # Optional generated images also belong to the durable working directory.
    server.poster.OUT_DIR = store.home / "postcard_images"
    server.poster.OUT_DIR.mkdir(exist_ok=True)
    store.sync()
    mcp_app = server.mcp.http_app(path="/mcp", transport="streamable-http",
                                  stateless_http=True, json_response=True)

    @asynccontextmanager
    async def lifespan(app):
        async with mcp_app.lifespan(mcp_app):
            yield
        # Best effort only: each response already has its own durable commit.
        await anyio.to_thread.run_sync(store.sync)

    async def health(request):
        return JSONResponse({"status": "ok"})

    app = Starlette(routes=[
        Route("/health", health),
        *mcp_app.routes,
        Mount("/static/postcards", StaticFiles(directory=server.poster.OUT_DIR)),
        *web.app.routes,
    ], lifespan=lifespan)
    return OptionalAccessToken(DurableRequests(app, store, server),
                               os.environ.get("NOWHERE_ACCESS_TOKEN", ""))


def main():
    import uvicorn
    logging.basicConfig(level=logging.INFO)
    uvicorn.run("nowhere.remote:create_app", factory=True, host="0.0.0.0",
                port=int(os.environ.get("PORT", "8000")), workers=1)


if __name__ == "__main__":
    main()
