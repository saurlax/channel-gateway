"""OneBot v11 universal WebSocket sessions and request/receipt correlation."""

import asyncio
import json
import secrets
from collections.abc import Awaitable, Callable

from channel_gateway_core import Message

from ..adapters import AdapterError, onebot_message
from ..storage import Store
from .ingress import begin_event, event_type, ingest_parsed

type ReceiveMessage = Callable[[dict, Message | None, dict | None], Awaitable[dict]]


class OneBotSession:
    def __init__(self, connection: dict, receive: ReceiveMessage, send: Callable[[str], Awaitable[None]], store: Store):
        self.connection = connection
        self.receive = receive
        self.send = send
        self.store = store
        self.pending: dict[str, asyncio.Future] = {}

    async def dispatch(self, raw: str | bytes) -> None:
        identifier, event = await begin_event(self.store, self.connection, raw)
        if identifier is None:
            return
        echo = event.get("echo")
        if isinstance(echo, str) and echo in self.pending:
            await self.store.finish_channel_event(identifier, "receipt", event_type=event_type(event))
            future = self.pending[echo]
            if not future.done():
                future.set_result(event)
            return
        await ingest_parsed(
            self.store,
            self.connection,
            identifier,
            event,
            self.receive,
            lambda value: (onebot_message(value, self.connection), None),
        )

    async def call(self, action: str, params: dict) -> dict:
        echo = "req_" + secrets.token_hex(16)
        future = asyncio.get_running_loop().create_future()
        self.pending[echo] = future
        try:
            await self.send(json.dumps({"action": action, "params": params, "echo": echo}))
            receipt = await asyncio.wait_for(future, 15)
            if receipt.get("status") != "ok" or receipt.get("retcode") != 0:
                raise AdapterError("OneBots did not confirm successful delivery")
            return receipt
        finally:
            self.pending.pop(echo, None)
            if not future.done():
                future.cancel()

    def disconnected(self) -> None:
        for future in self.pending.values():
            if not future.done():
                future.set_exception(AdapterError("OneBot connection interrupted during a request"))
