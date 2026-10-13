"""HTTP management, signed event ingestion, and process lifecycle."""

import asyncio
import hmac
import sqlite3
from contextlib import asynccontextmanager, suppress
from importlib.metadata import version
from pathlib import Path
from typing import Literal

import httpx
from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .adapters import AdapterError, HttpAdapters, onebot_message, verified_signature
from .auth import AdministratorAuth
from .bridge import Bridge
from .channels.common import event_object
from .channels.ingress import ProjectionError, begin_event, event_type, ingest_event
from .channels.manager import ChannelManager, capabilities, transport
from .channels.qq import qq_message, validation_response, verify_callback
from .mcp_tools import RouteAuthorization, create_mcp
from .models import (
    AdministratorLogin,
    ConnectionInput,
    ConnectionSecrets,
    Identifier,
    LoginPoll,
    RouteInput,
    RouteUpdate,
    Settings,
    public_connection,
)
from .storage import Store

MAX_WEBHOOK_BYTES = 1024 * 1024


def create_app(
    *, static_dir: Path | None = None, settings: Settings | None = None, client: httpx.AsyncClient | None = None
) -> FastAPI:
    settings = settings or Settings.from_env()
    authentication = AdministratorAuth(settings)
    store = Store(settings.data_dir / "gateway.sqlite3")
    http = client or httpx.AsyncClient(timeout=15.0, follow_redirects=False, trust_env=False)
    channels = ChannelManager(store, http)
    bridge = Bridge(store, HttpAdapters(http), settings.poll_interval, channels)
    mcp = create_mcp(store, bridge)
    mcp_app = mcp.streamable_http_app()

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        task = None
        try:
            await store.open()
            await channels.start(bridge.ingest, enabled=settings.worker_enabled)
            async with mcp.session_manager.run():
                if settings.worker_enabled:
                    task = asyncio.create_task(bridge.run())
                yield
        finally:
            if task:
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
            await channels.close()
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
    app.state.channels = channels

    def connection_view(row: dict) -> dict:
        value = public_connection(row)
        if row["kind"] not in {"a13n", "mcp"}:
            value.update(channels.status(row))
        return value

    async def channel_connection(identifier: str, kind: str) -> dict:
        row = await store.connection(identifier)
        if not row or row["kind"] != kind:
            raise HTTPException(404, "Channel connection not found")
        return row

    async def event_bytes(request: Request) -> bytes:
        raw = bytearray()
        async for chunk in request.stream():
            raw.extend(chunk)
            if len(raw) > MAX_WEBHOOK_BYTES:
                raise HTTPException(413, "Event exceeds the size limit")
        return bytes(raw)

    async def admin(request: Request) -> None:
        authentication.require(request)

    @app.post("/api/v1/auth/login")
    async def login(request: Request, response: Response, body: AdministratorLogin) -> dict:
        return await authentication.login(request, response, body)

    @app.get("/api/v1/auth/session")
    async def session(request: Request) -> dict:
        return authentication.view(authentication.require(request))

    @app.post("/api/v1/auth/logout", status_code=204)
    async def logout(request: Request) -> Response:
        response = Response(status_code=204)
        authentication.logout(request, response)
        return response

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
            "management_configured": authentication.configured,
            "integrations": [
                {
                    "key": "onebots",
                    "name": "OneBots",
                    "state": "available",
                    "description": "OneBot v11 HTTP and WebSocket transports.",
                },
                {
                    "key": "qq",
                    "name": "QQ official bot",
                    "state": "available",
                    "description": "Official QQ Gateway and signed HTTP callbacks.",
                },
                {
                    "key": "weixin",
                    "name": "Weixin iLink",
                    "state": "available",
                    "description": "QR pairing, long polling, and private text replies.",
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
        return [connection_view(row) for row in await store.connections()]

    @app.post("/api/v1/connections", status_code=201)
    async def add_connection(body: ConnectionInput, _admin: None = Depends(admin)) -> dict:
        row = await store.add_connection(body.stored())
        await channels.restart(row)
        value = connection_view(row)
        if row["kind"] in {"a13n", "mcp"}:
            value["mcp_key"] = await store.rotate_host_key(row["id"])
        return value

    @app.post("/api/v1/connections/{connection_id}/mcp-key")
    async def rotate_host_key(connection_id: str, _admin: None = Depends(admin)) -> dict:
        row = await store.connection(connection_id)
        if not row or row["kind"] not in {"a13n", "mcp"}:
            raise HTTPException(404, "Agent host not found")
        return {"id": connection_id, "name": row["name"], "mcp_key": await store.rotate_host_key(connection_id)}

    @app.patch("/api/v1/connections/{connection_id}")
    async def change_credentials(connection_id: str, body: ConnectionSecrets, _admin: None = Depends(admin)) -> dict:
        row = await store.connection(connection_id)
        if row is None:
            raise HTTPException(404, "Connection not found")
        expected = row.copy()
        values = body.model_dump(exclude_none=True)
        for key, value in values.items():
            row[key] = value.get_secret_value()
        try:
            ConnectionInput.model_validate({key: value for key, value in row.items() if key != "id"})
        except ValueError:
            raise HTTPException(422, "Credentials do not match the channel type") from None
        if not values:
            return connection_view(row)
        async with channels.login_locks.setdefault(connection_id, asyncio.Lock()):
            await channels.stop(connection_id)
            if not await store.update_connection(connection_id, row, expected):
                current = await store.connection(connection_id)
                if current:
                    await channels.restart(current)
                raise HTTPException(409, "Connection changed; refresh before editing")
            channels.logins.pop(connection_id, None)
            if row["kind"] == "qq":
                await store.set_channel_state(connection_id, "qq_gateway", {})
            await channels.restart(row)
        return connection_view(row)

    @app.delete("/api/v1/connections/{connection_id}")
    async def delete_connection(connection_id: str, _admin: None = Depends(admin)) -> dict:
        async with channels.login_locks.setdefault(connection_id, asyncio.Lock()):
            row = await store.connection(connection_id)
            if row is None:
                raise HTTPException(404, "Connection not found")
            await channels.stop(connection_id)
            if not await store.delete_connection(connection_id, row):
                current = await store.connection(connection_id)
                if current:
                    await channels.restart(current)
                raise HTTPException(409, "Connection changed; refresh before editing")
            channels.logins.pop(connection_id, None)
            channels.states.pop(connection_id, None)
        bridge.wake.set()
        return {"id": connection_id}

    def validate_host_route(host: dict, body: RouteInput) -> None:
        if host["kind"] == "mcp":
            if body.agent_id or body.mcp_connection_id or body.reply_source != "mcp":
                raise HTTPException(422, "MCP hosts require MCP replies without a13n bindings")
        elif not body.agent_id:
            raise HTTPException(422, "a13n routes require an agent ID")
        elif body.reply_source == "mcp" and not body.mcp_connection_id:
            raise HTTPException(422, "MCP replies require an a13n MCP connection ID")

    @app.get("/api/v1/routes")
    async def routes(_admin: None = Depends(admin)) -> list[dict]:
        return await store.routes()

    @app.post("/api/v1/routes", status_code=201)
    async def add_route(body: RouteInput, _admin: None = Depends(admin)) -> dict:
        channel = await store.connection(body.channel_id)
        host = await store.connection(body.host_id)
        if (
            not channel
            or channel["kind"] not in {"onebots", "qq", "weixin"}
            or not host
            or host["kind"] not in {"a13n", "mcp"}
        ):
            raise HTTPException(422, "Choose a channel and an agent host")
        validate_host_route(host, body)
        if body.conversation_kind not in capabilities(channel)["conversations"]:
            raise HTTPException(422, "This channel does not support that conversation type")
        if body.mcp_connection_id and any(
            route["host_id"] == body.host_id and route.get("mcp_connection_id") == body.mcp_connection_id
            for route in await store.routes()
        ):
            raise HTTPException(409, "Use a dedicated a13n MCP connection for each route")
        try:
            route, token = await store.add_route(body.model_dump())
        except sqlite3.IntegrityError:
            raise HTTPException(409, "This conversation or MCP connection already has a route") from None
        except ValueError:
            raise HTTPException(409, "Connection changed; refresh before editing") from None
        return {**route, "agent_token": token}

    @app.delete("/api/v1/routes/{route_id}")
    async def delete_route(route_id: str, _admin: None = Depends(admin)) -> dict:
        if not await store.delete_route(route_id):
            raise HTTPException(404, "Route not found")
        bridge.wake.set()
        return {"id": route_id}

    @app.patch("/api/v1/routes/{route_id}")
    async def change_route(route_id: str, body: RouteUpdate, _admin: None = Depends(admin)) -> dict:
        row = await store.route(route_id)
        if row is None:
            raise HTTPException(404, "Route not found")
        if not await store.connection(row["channel_id"]):
            raise HTTPException(409, "This route's channel has been deleted")
        updates = body.model_dump(exclude_unset=True)
        if any(
            key in updates and updates[key] != row.get(key)
            for key in ("reply_source", "mcp_connection_id", "access_mode")
        ):
            active = await store.rows(
                "SELECT e.id FROM events e JOIN routes r ON r.id=e.route_id WHERE (r.id=? OR json_extract(r.value,'$.parent_route_id')=?) AND e.state IN ('queued','submitted')",
                (route_id, route_id),
            )
            if active:
                raise HTTPException(409, "Wait for the current input to finish before changing the MCP binding")
        if row.get("parent_route_id"):
            raise HTTPException(422, "Edit the parent route to change this conversation's access")
        value = {key: item for key, item in row.items() if key not in {"id", "thread_id", "parent_route_id"}}
        try:
            validated = RouteInput.model_validate({**value, **updates})
        except ValueError:
            raise HTTPException(422, "Invalid route access or reply policy") from None
        host = await store.connection(validated.host_id)
        if not host:
            raise HTTPException(422, "Agent host not found")
        validate_host_route(host, validated)
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

    @app.get("/api/v1/history")
    async def conversation_history(
        channel_id: Identifier,
        conversation_kind: Literal["private", "group"],
        conversation_id: Identifier,
        limit: int = Query(default=100, ge=1, le=100),
        before_created_at: str | None = Query(default=None, max_length=64),
        before_id: Identifier | None = None,
        _admin: None = Depends(admin),
    ) -> dict:
        if (before_created_at is None) != (before_id is None):
            raise HTTPException(422, "Provide both history cursor fields")
        return await store.conversation_history(
            channel_id,
            f"{conversation_kind}:{conversation_id}",
            limit=limit,
            before_created_at=before_created_at,
            before_id=before_id,
        )

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
        connection = await channel_connection(connection_id, "onebots")
        if transport(connection) != "http_reverse":
            raise HTTPException(409, "This channel does not receive HTTP callbacks")
        raw = await event_bytes(request)
        if not verified_signature(raw, request.headers.get("x-signature", ""), connection["webhook_secret"]):
            raise HTTPException(401, "Invalid event signature")
        try:
            return await ingest_event(
                store, connection, raw, bridge.ingest, lambda event: (onebot_message(event, connection), None)
            )
        except ProjectionError:
            raise HTTPException(422, "Invalid OneBot text event") from None

    @app.post("/api/v1/hooks/qq/{connection_id}")
    async def receive_qq(connection_id: str, request: Request) -> dict:
        connection = await channel_connection(connection_id, "qq")
        if transport(connection) != "webhook":
            raise HTTPException(409, "This channel does not receive HTTP callbacks")
        raw = await event_bytes(request)
        try:
            # Validation challenges have no signature; only well-formed challenges
            # are retained, independently of authenticated message events.
            try:
                event = event_object(raw)
            except (ValueError, TypeError):
                event = {}
            if event.get("op") == 13:
                response = validation_response(event, connection["app_secret"])
                identifier, _ = await begin_event(store, connection, raw)
                if identifier:
                    await store.finish_channel_event(identifier, "validation", event_type=event_type(event))
                return response
            if not verify_callback(
                raw,
                request.headers.get("x-signature-timestamp", ""),
                request.headers.get("x-signature-ed25519", ""),
                connection["app_secret"],
            ):
                raise HTTPException(401, "Invalid event signature")
            await ingest_event(
                store, connection, raw, bridge.ingest, lambda event: (qq_message(event, connection), None)
            )
        except (ValueError, TypeError, KeyError, AttributeError, OverflowError):
            raise HTTPException(422, "Invalid QQ text event") from None
        # Confirm durable ingestion before acknowledging; Agent execution is asynchronous.
        return {"op": 12, "d": 0}

    @app.websocket("/api/v1/ws/onebots/{connection_id}")
    async def reverse_ws(connection_id: str, socket: WebSocket) -> None:
        connection = await store.connection(connection_id)
        if not connection or connection["kind"] != "onebots" or transport(connection) != "ws_reverse":
            await socket.close(code=1008)
            return
        if (
            socket.headers.get("origin") is not None
            or not hmac.compare_digest(
                socket.headers.get("authorization", "").encode(), ("Bearer " + connection["token"]).encode()
            )
            or socket.headers.get("x-self-id") != connection["self_id"]
            or socket.headers.get("x-client-role") != "Universal"
            or connection_id in channels.sessions
        ):
            await socket.close(code=1008)
            return
        await socket.accept()

        async def incoming():
            while True:
                packet = await socket.receive()
                if packet["type"] == "websocket.disconnect":
                    return
                raw = packet.get("text") or packet.get("bytes")
                if raw is None or len(raw.encode("utf-8") if isinstance(raw, str) else raw) > MAX_WEBHOOK_BYTES:
                    await socket.close(code=1009)
                    return
                yield raw

        try:
            await channels.serve_onebot(connection, socket.send_text, socket.close, incoming())
        except (WebSocketDisconnect, RuntimeError, AdapterError):
            pass

    @app.post("/api/v1/connections/{connection_id}/login")
    async def begin_weixin_login(connection_id: str, _admin: None = Depends(admin)) -> dict:
        connection = await channel_connection(connection_id, "weixin")
        try:
            return await channels.begin_login(connection)
        except (AdapterError, httpx.HTTPError, ValueError):
            raise HTTPException(502, "Weixin login could not start; check the network and try again") from None

    @app.post("/api/v1/connections/{connection_id}/login/status")
    async def poll_weixin_login(connection_id: str, body: LoginPoll, _admin: None = Depends(admin)) -> dict:
        connection = await channel_connection(connection_id, "weixin")
        try:
            return await channels.poll_login(connection, body.login_id, body.verify_code)
        except (AdapterError, httpx.HTTPError, ValueError, TypeError, KeyError):
            raise HTTPException(502, "Weixin pairing failed; verify the account and start a new QR code") from None

    @app.post("/api/v1/connections/{connection_id}/login/cancel")
    async def cancel_weixin_login(connection_id: str, body: LoginPoll, _admin: None = Depends(admin)) -> dict:
        await channel_connection(connection_id, "weixin")
        async with channels.login_locks.setdefault(connection_id, asyncio.Lock()):
            login = channels.logins.get(connection_id)
            if login and login["login_id"] == body.login_id:
                channels.logins.pop(connection_id, None)
        return {"status": "cancelled"}

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
