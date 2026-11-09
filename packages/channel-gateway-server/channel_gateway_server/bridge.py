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
from .host_context import MemoryPolicyError
from .host_failures import failure_notice
from .models import route_allows_sender
from .routing import select_route
from .storage import Store
from .wake_decisions import DecisionError, decision_key, should_process

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
        self._wake_jobs: dict[str, tuple[dict, asyncio.Task[tuple[bool, str | None]]]] = {}

    async def close(self) -> None:
        jobs = [task for _, task in self._wake_jobs.values()]
        for task in jobs:
            task.cancel()
        await asyncio.gather(*jobs, return_exceptions=True)
        self._wake_jobs.clear()

    async def _classify(
        self, connection: dict, route: dict, event_id: str, message: dict, history: list[dict], active: bool
    ) -> tuple[bool, str | None]:
        try:
            return await should_process(
                self.store, self.adapters, connection, route, event_id, message, history, active=active
            ), None
        except DecisionError as failure:
            # Classification is an optimization, never a message-loss boundary.
            return True, str(failure)

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
        for identifier in list(self._wake_jobs):
            current = await self.store.event(identifier)
            if not current or current["state"] != "queued":
                _, job = self._wake_jobs.pop(identifier)
                job.cancel()
                await asyncio.gather(job, return_exceptions=True)
        for event in await self.store.pending():
            try:
                await self.advance(event)
            except (AdapterError, httpx.HTTPError, ValueError, KeyError, TypeError) as failure:
                attempts = event["attempts"] + 1
                error = (
                    str(failure)
                    if isinstance(failure, MemoryPolicyError)
                    else "Host request failed; check connection and Agent configuration"
                )
                if attempts >= 5:
                    await self.store.fail_event(event["id"], error, failure_notice(str(failure)), attempts=attempts)
                else:
                    await self.store.update_event(event["id"], attempts=attempts, error=error)
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
        channel = await self.store.connection(route["channel_id"])
        if channel and channel["kind"] == "github":
            from .channels.github import authorize

            try:
                github = message["structured"]["github"]
                authorize(channel, message["sender_id"], github["repository_id"], "invoke")
                if github.get("requested_action") == "review":
                    authorize(channel, message["sender_id"], github["repository_id"], "review")
            except (SendBlocked, KeyError):
                await self.store.update_event(event["id"], state="blocked", error="GitHub invocation authority revoked")
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
            pending_decision = self._wake_jobs.get(event["id"])
            if (
                pending_decision
                and pending_decision[0] == {"route": route, "host": connection}
                and not pending_decision[1].done()
            ):
                return
            active_thread = None
            active = False
            previous = None
            if not target and route.get("thread_policy") != "message":
                from .host_context import active_work

                previous = await self.store.environment_thread(route, message)
                if previous and route.get("session_idle_minutes") is None and not route.get("wake_agent_id"):
                    active_thread = previous
                elif previous:
                    thread = await self.adapters.host(connection, "GET", resource_path("threads", previous))
                    active = await active_work(
                        self.adapters,
                        connection,
                        thread,
                    )
                    if await self.store.retained_session(
                        previous, active, route.get("session_idle_minutes"), route["host_id"]
                    ):
                        active_thread = previous
            if not target and route.get("wake_agent_id"):
                binding = decision_key(connection, route, event["id"])
                decision = await self.store.wake_decision(event["id"], binding)
                if decision is None:
                    job = self._wake_jobs.get(event["id"])
                    if job and job[0] != {"route": route, "host": connection}:
                        job[1].cancel()
                        await asyncio.gather(job[1], return_exceptions=True)
                        self._wake_jobs.pop(event["id"])
                        job = None
                    if job is None:
                        if len(self._wake_jobs) >= 8:
                            return
                        task = asyncio.create_task(
                            self._classify(
                                connection,
                                route,
                                event["id"],
                                message,
                                await self.store.delivery_history(event, {**route, "history_limit": 8}),
                                active,
                            )
                        )
                        task.add_done_callback(lambda _: self.wake.set())
                        self._wake_jobs[event["id"]] = ({"route": route, "host": connection}, task)
                        return
                    self._wake_jobs.pop(event["id"])
                    process, error = job[1].result()
                    if await self.store.route(route["id"]) != route or not same_connection(
                        await self.store.connection(connection["id"]), connection
                    ):
                        return
                    await self.store.save_wake_decision(event["id"], route["wake_agent_id"], process, error, binding)
                    decision = await self.store.wake_decision(event["id"], binding)
                if decision and not decision["process"]:
                    await self.store.update_event(event["id"], state="filtered", error=None)
                    return
            if target:
                body = json.loads(event["body"])
            else:
                history = await self.store.delivery_history(event, route, active_thread)
                import secrets

                handle = secrets.token_urlsafe(32)
                body = host_body(
                    message,
                    route,
                    history,
                    origin_context=handle,
                    history_complete=not (active_thread and len(history) == 100),
                )
                if route.get("conversation_memory"):
                    from .host_context import memory_safe_revision

                    body["agent_revision_id"] = await memory_safe_revision(self.adapters, connection, route["agent_id"])
                body["_context_handle"] = handle
            if "connection_tools" in body.get("options", {}).get("overrides", {}):
                # A frozen command may already have reached the Host. Retire it
                # rather than replay a removed policy or change an idempotent body.
                await self.store.update_event(
                    event["id"], state="blocked", error="Legacy tool override retired; send a new message"
                )
                return
            if not target and not active_thread:
                origin, provisioning = await self.store.environment_origin(route)
                if provisioning:
                    # The first shared-environment creation may already have reached the Host.
                    # Its original event must reconcile before another Session can use it.
                    return
                if origin:
                    body["environments"] = await self.adapters.thread_environments(connection, origin)
            if not target and not active_thread and route.get("conversation_memory"):
                # An existing inbox retains its settled session selection.
                from .host_context import memory_space

                space_id = await memory_space(self.store, self.adapters, connection, route, message)
                body["session"] = {
                    "memory": {
                        "enabled": True,
                        "spaces": [
                            {"name": "conversation", "space_id": space_id, "access": "write"},
                            *[
                                {"name": "global-" + str(index + 1), "space_id": identifier, "access": "read"}
                                for index, identifier in enumerate(route.get("global_memory_space_ids", []))
                            ],
                        ],
                    }
                }
                # Standalone defaults were inspected; empty initial mounts do not suppress them.
                body["memories"] = []
            # Recheck authorization after all external reads, before freezing a submission.
            if not target and await self.store.route(route["id"]) != route:
                return
            if not target:
                target = await self.store.prepare_delivery(event, route, body, active_thread_id=active_thread)
            receipt = await self.adapters.host(
                connection, "POST", target, body=body, key=event["delivery_key"] or event["id"]
            )
            await self.store.submitted(event["id"], route["id"], receipt)
            self.wake.set()
            return
        run_id = event["run_id"]
        if not run_id:
            path = resource_path("threads", event["thread_id"]) + "/inbox/" + event["entry_id"]
            entry = await self.adapters.host(connection, "GET", path)
            if entry["status"] not in {"pending", "assigned", "consumed"}:
                await self.store.fail_event(event["id"], "a13n did not accept the inbox entry", failure_notice())
                return
            run_id = entry.get("assigned_run_id")
            if not run_id:
                return
            await self.store.update_event(event["id"], run_id=run_id)
        run = await self.adapters.host(connection, "GET", resource_path("runs", run_id))
        if run["status"] in {"accepted", "running", "waiting"}:
            await self.store.retained_session(
                event["thread_id"], True, route.get("session_idle_minutes"), route["host_id"]
            )
            await self.store.update_event(event["id"], attempts=0, error=None)
            return
        if run["status"] != "completed":
            notice = failure_notice(run.get("failure"), status=run["status"])
            await self.store.fail_event(event["id"], notice, notice)
            return
        active = False
        if route.get("session_idle_minutes") is not None:
            from .host_context import active_work

            thread = await self.adapters.host(connection, "GET", resource_path("threads", event["thread_id"]))
            active = await active_work(self.adapters, connection, thread)
        await self.store.retained_session(
            event["thread_id"], active, route.get("session_idle_minutes"), route["host_id"]
        )
        await self.store.update_event(event["id"], state="completed", error=None)
        self.wake.set()

    async def retry(self, event_id: str) -> str:
        """Reconcile acceptance before deciding whether a new execution is safe."""
        rows = await self.store.rows("SELECT * FROM events WHERE id=?", (event_id,))
        if not rows or rows[0]["state"] != "failed":
            raise ValueError("Only a failed event can be retried")
        event = rows[0]
        if not event["entry_id"]:
            return await self.store.retry_event(event, restart=False)
        route = await self.store.route(event["route_id"])
        message = json.loads(event["message"])
        if not route or not route_allows_sender(route, message["sender_id"]):
            raise ValueError("Route permission revoked")
        connection = await self.store.connection(route["host_id"])
        if not connection or connection["kind"] != "a13n":
            raise ValueError("Host connection is unavailable")
        run_id = event["run_id"]
        restart = False
        if not run_id:
            path = resource_path("threads", event["thread_id"]) + "/inbox/" + event["entry_id"]
            entry = await self.adapters.host(connection, "GET", path)
            run_id = entry.get("assigned_run_id")
            restart = entry["status"] in {"cancelled", "rejected"} and not run_id
        if run_id:
            run = await self.adapters.host(connection, "GET", resource_path("runs", run_id))
            restart = run["status"] in {"failed", "cancelled"}
        return await self.store.retry_event(
            event, restart=restart, route=route, connection=connection, prior_run_id=run_id
        )

    async def send(self, reply: dict) -> None:
        route = await self.store.route(reply["route_id"])
        if not route or not route["enabled"]:
            await self.store.finish_reply(reply["id"], "blocked", error="Route is disabled")
            return
        provenance = await self.store.rows("SELECT origin FROM message_provenance WHERE reply_id=?", (reply["id"],))
        if provenance:
            origin = json.loads(provenance[0]["origin"])
            source_route = await self.store.route(origin["route_id"])
            if not source_route or not route_allows_sender(source_route, origin["sender_id"]):
                await self.store.finish_reply(reply["id"], "blocked", error="Origin permission revoked")
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
        if (payload or reply["event_id"]) and (
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
