"""Durable channel delivery and reply workflow, independent of HTTP routes."""

import asyncio
import json
import logging

import httpx
from channel_gateway_core import Message
from websockets.exceptions import ConnectionClosed

from .adapters import AdapterError, HttpAdapters, event_key, host_body, output_text, resource_path
from .channels.common import SendBlocked
from .channels.manager import ChannelManager
from .models import route_allows_sender
from .storage import Store

logger = logging.getLogger("channel-gateway.bridge")


class Bridge:
    def __init__(
        self, store: Store, adapters: HttpAdapters, poll_interval: float = 2.0, channels: ChannelManager | None = None
    ):
        self.store = store
        self.adapters = adapters
        self.poll_interval = poll_interval
        self.wake = asyncio.Event()
        self.channels = channels

    async def ingest(self, connection: dict, message: Message | None, context: dict | None = None) -> dict:
        if message is None:
            return {"state": "ignored"}
        current = await self.store.connection(connection["id"])
        keys = ("kind", "transport", "api_url", "token", "webhook_secret", "app_id", "app_secret")
        if not current or any(current.get(key) != connection.get(key) for key in keys):
            return {"state": "blocked"}
        kind, conversation_id = message.address.conversation_id.split(":", 1)
        routes = await self.store.routes()
        route = next(
            (
                route
                for route in routes
                if route["channel_id"] == connection["id"]
                and route["conversation_kind"] == kind
                and route["conversation_id"] == conversation_id
            ),
            None,
        )
        if not route:
            parent = next(
                (
                    row
                    for row in routes
                    if row["channel_id"] == connection["id"]
                    and row["conversation_kind"] == kind
                    and row["conversation_id"] == "*"
                ),
                None,
            )
            if parent and route_allows_sender(parent, message.sender_id):
                route = await self.store.conversation_route(parent, conversation_id)
            elif parent:
                route = parent
        identifier = event_key(message)
        if not route or not route_allows_sender(route, message.sender_id):
            await self.store.observe(
                identifier,
                message.model_dump(mode="json"),
                state="unmatched" if not route else "blocked",
                error="No matching route" if not route else "Sender or route is not authorized",
            )
            return {"state": "blocked"}
        reply_context = {
            **(context or {}),
            "message_id": message.source_message_id,
            "sender_id": message.sender_id,
            "source_time": message.occurred_at.timestamp(),
        }
        inserted = await self.store.ingest(identifier, route["id"], message.model_dump(mode="json"), reply_context)
        self.wake.set()
        return {"state": "accepted" if inserted else "duplicate", "event_id": identifier}

    async def run(self) -> None:
        while True:
            self.wake.clear()
            try:
                await self.tick()
            except Exception:
                # Do not log upstream bodies, credentials, or channel messages.
                logger.error("Bridge storage cycle failed")
            try:
                await asyncio.wait_for(self.wake.wait(), self.poll_interval)
            except TimeoutError:
                pass

    async def tick(self) -> None:
        for event in await self.store.pending():
            try:
                await self.advance(event)
            except (AdapterError, httpx.HTTPError, ValueError, KeyError, TypeError):
                attempts = event["attempts"] + 1
                await self.store.update_event(
                    event["id"],
                    attempts=attempts,
                    error="Host request failed; check connection and Agent configuration",
                    state="failed" if attempts >= 5 else event["state"],
                )
        for reply in await self.store.replies():
            if reply["state"] == "queued":
                await self.send(reply)

    async def advance(self, event: dict) -> None:
        route = await self.store.route(event["route_id"])
        message = json.loads(event["message"])
        if not route or not route_allows_sender(route, message["sender_id"]):
            await self.store.update_event(event["id"], state="blocked", error="Route permission revoked")
            return
        connection = await self.store.connection(route["host_id"])
        if not connection:
            raise AdapterError("Host connection is missing")
        if connection["kind"] == "mcp":
            await self.store.update_event(event["id"], state="available", error=None)
            return
        if event["state"] == "queued":
            # Freeze target and payload BEFORE I/O. A lost receipt retries the exact
            # original command even after another receipt binds the route's thread.
            target = event["target"]
            body = json.loads(event["body"]) if event["body"] else host_body(message, route)
            if not target:
                target = resource_path("threads", route["thread_id"]) + "/inbox" if route["thread_id"] else "/threads"
                await self.store.update_event(event["id"], target=target, body=json.dumps(body))
            receipt = await self.adapters.host(connection, "POST", target, body=body, key=event["id"])
            await self.store.submitted(event["id"], route["id"], receipt)
            return
        run_id = event["run_id"]
        if not run_id:
            path = resource_path("threads", event["thread_id"]) + "/inbox/" + event["entry_id"]
            entry = await self.adapters.host(connection, "GET", path)
            run_id = entry.get("assigned_run_id")
            if not run_id:
                if entry["status"] not in {"pending", "assigned"}:
                    await self.store.update_event(
                        event["id"], state="failed", error="a13n did not accept the inbox entry"
                    )
                return
            await self.store.update_event(event["id"], run_id=run_id)
        run = await self.adapters.host(connection, "GET", resource_path("runs", run_id))
        if run["status"] in {"accepted", "running", "waiting"}:
            await self.store.update_event(event["id"], attempts=0, error=None)
            return
        if run["status"] != "completed":
            await self.store.update_event(
                event["id"], state="failed", error="Agent run ended without a completed result"
            )
            return
        if route["reply_source"] == "mcp":
            await self.store.update_event(event["id"], state="completed", error=None)
            return
        text = output_text(run.get("output"))
        if text and len(text) <= 16000:
            await self.store.queue_reply(route, text, event_id=event["id"])
        else:
            await self.store.update_event(
                event["id"], state="completed", error="No supported text reply (or output exceeds 16000 characters)"
            )

    async def send(self, reply: dict) -> None:
        route = await self.store.route(reply["route_id"])
        if not route or not route["enabled"]:
            await self.store.finish_reply(reply["id"], "blocked", error="Route is disabled")
            return
        if reply["event_id"]:
            source = await self.store.event(reply["event_id"])
            if source is None or not route_allows_sender(route, source["message"]["sender_id"]):
                await self.store.finish_reply(reply["id"], "blocked", error="Source permission could not be confirmed")
                return
        connection = await self.store.connection(route["channel_id"])
        if connection and self.channels and not self.channels.ready(connection):
            return
        if not connection or not await self.store.claim_reply(reply["id"]):
            return
        try:
            message_id = (
                await self.channels.send(connection, route, reply["text"], reply)
                if self.channels
                else await self.adapters.send(connection, route, reply["text"])
            )
        except SendBlocked as error:
            await self.store.finish_reply(reply["id"], "blocked", error=str(error))
        except (
            AdapterError,
            httpx.HTTPError,
            ConnectionClosed,
            TimeoutError,
            OSError,
            ValueError,
            KeyError,
            TypeError,
        ):
            await self.store.finish_reply(
                reply["id"], "unknown", error="Delivery not confirmed; verify in the channel before resending"
            )
        else:
            await self.store.finish_reply(reply["id"], "sent", message_id=message_id)
