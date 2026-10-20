"""Independent, durable platform actions; these are not message-send requests."""

import json
from urllib.parse import quote

import httpx
from websockets.exceptions import ConnectionClosed

from ..adapters import AdapterError
from ..models import route_allows_sender
from .common import OperationRejected, SendBlocked
from .qq_operations import OPERATIONS, execute_operation, require_grant, safe_result


async def known_message(store, route: dict, message_id: str) -> tuple[str, dict | None]:
    messages = await store.received_messages(route, source_message_id=message_id, limit=1)
    if messages and route_allows_sender(route, messages[0]["sender_id"]):
        if "interaction_type" in messages[0].get("structured", {}):
            raise SendBlocked("Choose a platform message, not an interaction entry")
        return "received", messages[0]
    sent = await store.rows(
        "SELECT id FROM replies WHERE route_id=? AND message_id=? AND state='sent' LIMIT 1", (route["id"], message_id)
    )
    if sent:
        return "sent", None
    streams = await store.rows(
        "SELECT id FROM channel_actions WHERE route_id=? AND operation='stream_message' AND state='succeeded' AND json_extract(result,'$.message_id')=? LIMIT 1",
        (route["id"], message_id),
    )
    if streams:
        return "sent", None
    raise SendBlocked("Choose a known, currently authorized message in this conversation")


def reaction_authority(route: dict, connection_kind: str) -> str:
    if "message.react" in route.get("action_grants", []):
        return "message.react"
    if route["reply_source"] == "mcp" and connection_kind == "onebots":
        return "legacy.react"
    raise SendBlocked("This route does not authorize message.react")


async def action_value(manager, route: dict, connection: dict, operation: str, args: dict):
    store = manager.store
    if operation.startswith("resource:"):
        return await execute_operation(
            store, manager.qq(connection), route, OPERATIONS[operation.removeprefix("resource:")], args
        )
    if operation == "reaction":
        reaction_authority(route, connection["kind"])
        await known_message(store, route, args["source_message_id"])
        await manager.check_action_access(connection, route)
        if connection["kind"] == "onebots":
            await manager.onebot_call(
                connection,
                "set_msg_emoji_like",
                {"message_id": args["source_message_id"], "emoji_id": args["emoji_id"], "set": args["add"]},
            )
            return {}
        if connection["kind"] != "qq" or route["conversation_kind"] != "guild":
            raise SendBlocked("Message reactions are not supported in this conversation")
        path = (
            manager.qq(connection).messages_path(route)
            + "/"
            + quote(args["source_message_id"], safe="")
            + "/reactions/"
            + str(args["emoji_type"])
            + "/"
            + quote(args["emoji_id"], safe="")
        )
        return await manager.qq(connection).request_value("PUT" if args["add"] else "DELETE", path)
    if operation == "recall":
        ownership, _ = await known_message(store, route, args["message_id"])
        require_grant(route, "message.recall" if ownership == "sent" else "message.recall_members")
        await manager.check_action_access(connection, route)
        if connection["kind"] != "qq":
            raise SendBlocked("Recall is implemented for official QQ conversations")
        path = manager.qq(connection).messages_path(route) + "/" + quote(args["message_id"], safe="")
        if route["conversation_kind"] in {"guild", "guild_dm"}:
            path += "?hidetip=" + str(args.get("hide_tip", False)).lower()
        return await manager.qq(connection).request_value("DELETE", path)
    context = await store.channel_state(
        connection["id"], "context:" + route["conversation_kind"] + ":" + route["conversation_id"]
    )
    if not context.get("message_id") or not route_allows_sender(route, context.get("sender_id", "")):
        raise SendBlocked("Wait for an authorized incoming message")
    if operation == "typing":
        sequence = await store.next_channel_sequence(connection["id"], context["message_id"])
        return await manager.qq(connection).request_value(
            "POST",
            manager.qq(connection).messages_path(route),
            {
                "msg_type": 6,
                "input_notify": {"input_type": 1, "input_second": args["seconds"]},
                "msg_id": context["message_id"],
                "msg_seq": sequence,
            },
        )
    if operation == "stream_message":
        rows = await store.rows(
            "SELECT * FROM stream_sessions WHERE id=? AND route_id=?", (args["stream_handle"], route["id"])
        )
        stream = rows[0] if rows else None
        if not stream or stream["state"] != "open" or stream["sent_index"] != args["index"] - 1:
            raise SendBlocked("Stream is closed, unconfirmed, or out of sequence")
        if not await store.received_messages(route, source_message_id=stream["source_message_id"], limit=1):
            raise SendBlocked("Stream source is no longer authorized")
        context = (
            json.loads(stream["reply_context"])
            if stream.get("reply_context")
            else {"message_id": stream["source_message_id"]}
        )
        body = {
            "index": args["index"],
            "input_state": 10 if args["finished"] else 1,
            "input_mode": args["input_mode"],
            "content_type": args["message_format"],
            "content_raw": args["text"],
            "msg_id": context["message_id"],
            "msg_seq": await store.next_channel_sequence(connection["id"], context["message_id"]),
        }
        if context.get("event_id"):
            body.pop("msg_id", None)
            body["event_id"] = context["event_id"]
        if stream["provider_id"]:
            body["stream_msg_id"] = stream["provider_id"]
        response = await manager.qq(connection).request(
            "POST", f"/v2/users/{quote(route['conversation_id'], safe='')}/stream_messages", body
        )
        provider_id = response.get("id")
        if not isinstance(provider_id, str) or not provider_id or len(provider_id) > 1024:
            raise AdapterError("QQ omitted the stream receipt")
        async with store.transaction() as db:
            await db.execute(
                "UPDATE stream_sessions SET provider_id=?,sent_index=?,state=? WHERE id=?",
                (provider_id, args["index"], "closed" if args["finished"] else "open", args["stream_handle"]),
            )
        return {
            "message_id": provider_id,
            "stream_handle": args["stream_handle"],
            "index": args["index"],
            "finished": args["finished"],
        }
    raise SendBlocked("Unsupported channel action")


async def execute_action(manager, action: dict) -> None:
    store = manager.store
    route = await store.route(action["route_id"])
    connection = await store.connection(route["channel_id"]) if route else None
    if not route or not route["enabled"] or not connection:
        await store.finish_channel_action(action["id"], "blocked", error="Route or channel is unavailable")
        return
    if not manager.ready(connection) or not await store.claim_channel_action(action["id"]):
        return
    args = json.loads(action["arguments"])
    try:
        grant = action["required_grant"]
        if grant in {"message.send", "legacy.react"}:
            if route["reply_source"] != "mcp":
                raise SendBlocked("This route no longer authorizes this action")
        else:
            require_grant(route, grant)
        await manager.check_action_access(connection, route)
        result = await action_value(manager, route, connection, action["operation"], args)
    except SendBlocked as error:
        await store.finish_channel_action(action["id"], "blocked", error=str(error))
    except OperationRejected as error:
        await store.finish_channel_action(action["id"], "failed", error=str(error))
        if action["operation"] == "stream_message":
            async with store.transaction() as db:
                await db.execute("UPDATE stream_sessions SET state='failed' WHERE id=?", (args["stream_handle"],))
    except (AdapterError, httpx.HTTPError, ConnectionClosed, TimeoutError, OSError, ValueError, KeyError, TypeError):
        await store.finish_channel_action(
            action["id"], "unknown", error="Action not confirmed; verify in QQ before repeating"
        )
        if action["operation"] == "stream_message":
            async with store.transaction() as db:
                await db.execute("UPDATE stream_sessions SET state='unknown' WHERE id=?", (args["stream_handle"],))
    else:
        await store.finish_channel_action(action["id"], "succeeded", result=safe_result(result))
