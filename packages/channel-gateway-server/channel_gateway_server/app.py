"""HTTP management, signed event ingestion, and process lifecycle."""

import asyncio
import hmac
import json
import sqlite3
from contextlib import asynccontextmanager, suppress
from importlib.metadata import version
from pathlib import Path

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .adapters import HttpAdapters, event_key, onebot_message, verified_signature
from .bridge import Bridge
from .mcp_tools import RouteAuthorization, create_mcp
from .models import ConnectionInput, ConnectionSecrets, ReplyInput, RouteInput, RouteUpdate, Settings, public_connection
from .storage import Store

MAX_WEBHOOK_BYTES = 1024 * 1024


def create_app(
    *, static_dir: Path | None = None, settings: Settings | None = None, client: httpx.AsyncClient | None = None
) -> FastAPI:
    settings = settings or Settings.from_env()
    if settings.admin_token and len(settings.admin_token) < 32:
        raise ValueError("CHANNEL_GATEWAY_ADMIN_TOKEN must contain at least 32 characters")
    store = Store(settings.data_dir / "gateway.sqlite3")
    http = client or httpx.AsyncClient(timeout=15.0, follow_redirects=False, trust_env=False)
    bridge = Bridge(store, HttpAdapters(http), settings.poll_interval)
    mcp = create_mcp(store, bridge)
    mcp_app = mcp.streamable_http_app()

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        task = None
        try:
            await store.open()
            async with mcp.session_manager.run():
                if settings.worker_enabled:
                    task = asyncio.create_task(bridge.run())
                yield
        finally:
            if task:
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
            if client is None:
                await http.aclose()
            await store.close()

    app = FastAPI(
        title="Channel Gateway",
        version=version("channel-gateway-server"),
        lifespan=lifespan,
        docs_url="/api/docs",
        redoc_url=None,
        openapi_url="/api/openapi.json",
    )
    app.state.store = store
    app.state.bridge = bridge

    async def admin(request: Request) -> None:
        if not settings.admin_token:
            raise HTTPException(503, "Set CHANNEL_GATEWAY_ADMIN_TOKEN to enable management")
        auth = request.headers.get("authorization", "")
        expected = "Bearer " + settings.admin_token
        if not hmac.compare_digest(auth.encode(), expected.encode()):
            raise HTTPException(401, "Administrator authentication required", headers={"WWW-Authenticate": "Bearer"})
        origin = request.headers.get("origin")
        if origin and origin.rstrip("/") != str(request.base_url).rstrip("/"):
            raise HTTPException(403, "Cross-origin management requests are not accepted")

    @app.exception_handler(RequestValidationError)
    async def validation_error(_request: Request, exc: RequestValidationError) -> JSONResponse:
        # FastAPI's default includes submitted values, including credential fields.
        return JSONResponse(
            status_code=422,
            content={
                "detail": [{"loc": error["loc"], "msg": error["msg"], "type": error["type"]} for error in exc.errors()]
            },
        )

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        if request.url.path == "/":
            response.headers["Content-Security-Policy"] = (
                "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'"
            )
        return response

    @app.get("/healthz", include_in_schema=False)
    async def health() -> dict:
        return {"status": "ok"}

    @app.get("/api/v1/status")
    async def status() -> dict:
        return {
            "name": "Channel Gateway",
            "version": app.version,
            "stage": "bridge",
            "management_configured": bool(settings.admin_token),
            "integrations": [
                {
                    "key": "onebots",
                    "name": "OneBots",
                    "state": "available",
                    "description": "Signed OneBot v11 HTTP events and text replies.",
                },
                {
                    "key": "a13n",
                    "name": "a13n",
                    "state": "available",
                    "description": "Isolated threads and durable inbox delivery.",
                },
                {
                    "key": "mcp",
                    "name": "MCP",
                    "state": "available",
                    "description": "Conversation-scoped reads and reply requests.",
                },
            ],
        }

    @app.get("/api/v1/connections")
    async def connections(_admin: None = Depends(admin)) -> list[dict]:
        return [public_connection(row) for row in await store.connections()]

    @app.post("/api/v1/connections", status_code=201)
    async def add_connection(body: ConnectionInput, _admin: None = Depends(admin)) -> dict:
        row = await store.add_connection(body.stored())
        return public_connection(row)

    @app.patch("/api/v1/connections/{connection_id}")
    async def change_credentials(connection_id: str, body: ConnectionSecrets, _admin: None = Depends(admin)) -> dict:
        row = await store.connection(connection_id)
        if row is None:
            raise HTTPException(404, "Connection not found")
        expected = row.copy()
        values = body.model_dump(exclude_none=True)
        if "webhook_secret" in values and row["kind"] != "onebots":
            raise HTTPException(422, "Only OneBots connections use a webhook secret")
        for key, value in values.items():
            row[key] = value.get_secret_value()
        if not await store.update_connection(connection_id, row, expected):
            raise HTTPException(409, "Connection changed; refresh before editing")
        return public_connection(row)

    @app.get("/api/v1/routes")
    async def routes(_admin: None = Depends(admin)) -> list[dict]:
        return await store.routes()

    @app.post("/api/v1/routes", status_code=201)
    async def add_route(body: RouteInput, _admin: None = Depends(admin)) -> dict:
        if body.reply_source == "mcp" and not body.mcp_connection_id:
            raise HTTPException(422, "MCP replies require an a13n MCP connection ID")
        channel = await store.connection(body.channel_id)
        host = await store.connection(body.host_id)
        if not channel or channel["kind"] != "onebots" or not host or host["kind"] != "a13n":
            raise HTTPException(422, "Choose a OneBots channel and an a13n host")
        if body.mcp_connection_id and any(
            route["host_id"] == body.host_id and route.get("mcp_connection_id") == body.mcp_connection_id
            for route in await store.routes()
        ):
            raise HTTPException(409, "Use a dedicated a13n MCP connection for each route")
        try:
            route, token = await store.add_route(body.model_dump())
        except sqlite3.IntegrityError:
            raise HTTPException(409, "This conversation or MCP connection already has a route") from None
        return {**route, "agent_token": token}

    @app.patch("/api/v1/routes/{route_id}")
    async def change_route(route_id: str, body: RouteUpdate, _admin: None = Depends(admin)) -> dict:
        row = await store.route(route_id)
        if row is None:
            raise HTTPException(404, "Route not found")
        updates = body.model_dump(exclude_unset=True)
        if any(key in updates and updates[key] != row.get(key) for key in ("reply_source", "mcp_connection_id")):
            active = await store.rows(
                "SELECT id FROM events WHERE route_id=? AND state IN ('queued','submitted')", (route_id,)
            )
            if active:
                raise HTTPException(409, "Wait for the current input to finish before changing the MCP binding")
        value = {key: item for key, item in row.items() if key not in {"id", "thread_id"}}
        try:
            validated = RouteInput.model_validate({**value, **updates})
        except ValueError:
            raise HTTPException(422, "Invalid route access or reply policy") from None
        if validated.reply_source == "mcp" and not validated.mcp_connection_id:
            raise HTTPException(422, "MCP replies require an a13n MCP connection ID")
        if validated.mcp_connection_id and any(
            other["id"] != route_id
            and other["host_id"] == validated.host_id
            and other.get("mcp_connection_id") == validated.mcp_connection_id
            for other in await store.routes()
        ):
            raise HTTPException(409, "Use a dedicated a13n MCP connection for each route")
        stored = {**validated.model_dump(), "id": route_id}
        try:
            updated = await store.update_route(route_id, stored, row)
        except sqlite3.IntegrityError:
            raise HTTPException(409, "This MCP connection is already bound to another route") from None
        if not updated:
            raise HTTPException(409, "Route changed; refresh before editing")
        bridge.wake.set()
        return {**stored, "thread_id": row["thread_id"]}

    @app.post("/api/v1/routes/{route_id}/token")
    async def rotate_token(route_id: str, _admin: None = Depends(admin)) -> dict:
        if await store.route(route_id) is None:
            raise HTTPException(404, "Route not found")
        return {"agent_token": await store.rotate_token(route_id)}

    @app.get("/api/v1/events")
    async def events(_admin: None = Depends(admin)) -> list[dict]:
        return await store.events()

    @app.get("/api/v1/replies")
    async def replies(_admin: None = Depends(admin)) -> list[dict]:
        return await store.replies()

    @app.post("/api/v1/replies/{reply_id}/approve")
    async def approve(reply_id: str, body: ReplyInput, _admin: None = Depends(admin)) -> dict:
        if not await store.decide(reply_id, approve=True, text=body.text):
            raise HTTPException(409, "Only an existing draft can be approved")
        bridge.wake.set()
        return {"state": "approved"}

    @app.post("/api/v1/replies/{reply_id}/reject")
    async def reject(reply_id: str, _admin: None = Depends(admin)) -> dict:
        if not await store.decide(reply_id, approve=False):
            raise HTTPException(409, "Only an existing draft can be rejected")
        return {"state": "rejected"}

    @app.post("/api/v1/events/{event_id}/retry")
    async def retry(event_id: str, _admin: None = Depends(admin)) -> dict:
        rows = await store.rows("SELECT * FROM events WHERE id=?", (event_id,))
        if not rows or rows[0]["state"] != "failed":
            raise HTTPException(409, "Only a failed event can be retried")
        state = "submitted" if rows[0]["entry_id"] else "queued"
        await store.update_event(event_id, state=state, attempts=0, error=None)
        bridge.wake.set()
        return {"state": state}

    @app.post("/api/v1/hooks/onebots/{connection_id}")
    async def receive(connection_id: str, request: Request) -> dict:
        connection = await store.connection(connection_id)
        if not connection or connection["kind"] != "onebots":
            raise HTTPException(404, "Channel connection not found")
        raw = bytearray()
        async for chunk in request.stream():
            raw.extend(chunk)
            if len(raw) > MAX_WEBHOOK_BYTES:
                raise HTTPException(413, "Event exceeds the size limit")
        if not verified_signature(bytes(raw), request.headers.get("x-signature", ""), connection["webhook_secret"]):
            raise HTTPException(401, "Invalid event signature")
        try:
            event = json.loads(raw)
            if not isinstance(event, dict):
                raise ValueError("Expected an event object")
            message = onebot_message(event, connection)
        except (ValueError, TypeError, KeyError, AttributeError, OverflowError):
            raise HTTPException(422, "Invalid OneBot text event") from None
        if message is None:
            return {"state": "ignored"}
        kind, conversation_id = message.address.conversation_id.split(":", 1)
        route = next(
            (
                route
                for route in await store.routes()
                if route["channel_id"] == connection_id
                and route["conversation_kind"] == kind
                and route["conversation_id"] == conversation_id
            ),
            None,
        )
        if not route or not route["enabled"] or message.sender_id not in route["allowed_user_ids"]:
            return {"state": "blocked"}
        identifier = event_key(message)
        inserted = await store.ingest(identifier, route["id"], message.model_dump(mode="json"))
        bridge.wake.set()
        return {"state": "accepted" if inserted else "duplicate", "event_id": identifier}

    app.mount("/mcp", RouteAuthorization(mcp_app, store))
    assets = static_dir if static_dir is not None else Path(__file__).parent / "static"
    index = assets / "index.html"
    if index.is_file():
        if (assets / "assets").is_dir():
            app.mount("/assets", StaticFiles(directory=assets / "assets"), name="assets")

        @app.get("/", include_in_schema=False)
        async def console() -> FileResponse:
            return FileResponse(index)

    return app
