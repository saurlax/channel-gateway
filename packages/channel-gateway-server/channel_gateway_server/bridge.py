"""Durable channel delivery and reply workflow, independent of HTTP routes."""

import asyncio
import json
import logging

import httpx
from channel_gateway_core import Message
from websockets.exceptions import ConnectionClosed

from .adapters import AdapterError, HttpAdapters, event_key, host_body, resource_path
from .channels.actions import tool_message_allowed
from .channels.common import OperationRejected, SendBlocked
from .channels.manager import ChannelManager
from .channels.platforms.common import same_connection
from .models import route_allows_sender
from .routing import select_route
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
        if not same_connection(current, connection):
            return {"state": "blocked"}
        kind, conversation_id = message.address.conversation_id.split(":", 1)
        routes = await self.store.routes()
        route = select_route(routes, connection["id"], kind, conversation_id)
        if route and route["conversation_id"] == "*" and route_allows_sender(route, message.sender_id):
            try:
                route = await self.store.conversation_route(route, conversation_id, connection["id"], kind)
            except ValueError:
                return {"state": "blocked"}
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
            **({"guild_id": message.guild_id} if message.guild_id else {}),
            **({"event_id": message.reply_event_id} if message.reply_event_id else {}),
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
        for reply in await self.store.pending_replies():
            await self.send(reply)
        if self.channels:
            for action in await self.store.pending_channel_actions():
                await self.channels.execute_action(action)

    async def advance(self, event: dict) -> None:
        rows = await self.store.rows("SELECT * FROM events WHERE id=?", (event["id"],))
        if not rows or rows[0]["state"] not in {"queued", "submitted"}:
            return
        event = rows[0]
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
            if "connection_tools" in body.get("options", {}).get("overrides", {}):
                # A frozen command may already have reached the Host. Retire it
                # rather than replay a removed policy or change an idempotent body.
                await self.store.update_event(
                    event["id"], state="blocked", error="Legacy tool override retired; send a new message"
                )
                return
            if not target:
                target = await self.store.prepare_delivery(event, route, body)
            receipt = await self.adapters.host(connection, "POST", target, body=body, key=event["id"])
            await self.store.submitted(event["id"], route["id"], receipt)
            self.wake.set()
            return
        run_id = event["run_id"]
        if not run_id:
            path = resource_path("threads", event["thread_id"]) + "/inbox/" + event["entry_id"]
            entry = await self.adapters.host(connection, "GET", path)
            if entry["status"] not in {"pending", "assigned", "consumed"}:
                await self.store.update_event(event["id"], state="failed", error="a13n did not accept the inbox entry")
                return
            run_id = entry.get("assigned_run_id")
            if not run_id:
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
        await self.store.update_event(event["id"], state="completed", error=None)
        self.wake.set()

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
        if not connection:
            await self.store.finish_reply(reply["id"], "blocked", error="Channel is unavailable")
            return
        if connection and self.channels and not self.channels.ready(connection):
            return
        payload = await self.store.reply_payload(reply["id"])
        proactive = payload and payload.get("delivery") in {"proactive", "wakeup"}
        if payload and (
            not tool_message_allowed(route, connection)
            or (proactive and "message.proactive" not in route.get("action_grants", []))
        ):
            await self.store.finish_reply(reply["id"], "blocked", error="Message tool authority was revoked")
            return
        claimed = (
            await self.store.claim_qq_push(
                reply["id"], connection["app_id"], route["conversation_kind"] + ":" + route["conversation_id"]
            )
            if proactive and connection["kind"] == "qq"
            else await self.store.claim_reply(reply["id"])
        )
        if not claimed:
            return
        try:
            message_id = (
                await self.channels.send(connection, route, reply["text"], reply)
                if self.channels
                else await self.adapters.send(connection, route, reply["text"])
            )
        except SendBlocked as error:
            await self.store.finish_reply(reply["id"], "blocked", error=str(error))
        except OperationRejected as error:
            await self.store.finish_reply(reply["id"], "failed", error=str(error))
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
