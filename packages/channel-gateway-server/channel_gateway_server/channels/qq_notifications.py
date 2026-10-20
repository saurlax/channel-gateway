"""Sanitized, scope-bound QQ notifications; raw packets remain private."""

import asyncio
import hashlib
import json

import httpx

from ..adapters import AdapterError
from .qq_operations import safe_result

NOTIFICATIONS = {
    "FRIEND_ADD",
    "FRIEND_DEL",
    "C2C_MSG_RECEIVE",
    "C2C_MSG_REJECT",
    "GROUP_ADD_ROBOT",
    "GROUP_DEL_ROBOT",
    "GROUP_JOIN_REQUEST",
    "GROUP_MSG_RECEIVE",
    "GROUP_MSG_REJECT",
    "GROUP_MEMBER_ADD",
    "GROUP_MEMBER_REMOVE",
    "INTERACTION_CREATE",
    "SUBSCRIBE_MESSAGE_STATUS",
    "GUILD_CREATE",
    "GUILD_UPDATE",
    "GUILD_DELETE",
    "CHANNEL_CREATE",
    "CHANNEL_UPDATE",
    "CHANNEL_DELETE",
    "GUILD_MEMBER_ADD",
    "GUILD_MEMBER_UPDATE",
    "GUILD_MEMBER_REMOVE",
    "MESSAGE_REACTION_ADD",
    "MESSAGE_REACTION_REMOVE",
    "MESSAGE_DELETE",
    "PUBLIC_MESSAGE_DELETE",
    "DIRECT_MESSAGE_DELETE",
    "MESSAGE_AUDIT_PASS",
    "MESSAGE_AUDIT_REJECT",
    "AUDIO_START",
    "AUDIO_FINISH",
    "AUDIO_ON_MIC",
    "AUDIO_OFF_MIC",
    "AUDIO_OR_LIVE_CHANNEL_MEMBER_ENTER",
    "AUDIO_OR_LIVE_CHANNEL_MEMBER_EXIT",
}


async def record_notification(store, connection: dict, event: dict, client) -> None:
    name = event.get("t", "")
    if event.get("op") != 0 or not (name in NOTIFICATIONS or name.startswith(("FORUM_", "OPEN_FORUM_"))):
        return
    data = event.get("d")
    if not isinstance(data, dict):
        raise ValueError("Invalid QQ notification")
    if data.get("group_openid"):
        kind, identity = "group", data["group_openid"]
    elif data.get("channel_id"):
        kind, identity = "guild", data["channel_id"]
    elif data.get("guild_id"):
        kind, identity = "guild_parent", data["guild_id"]
    elif data.get("user_openid") or data.get("openid"):
        kind, identity = "private", data.get("user_openid") or data["openid"]
    else:
        kind, identity = "account", connection["id"]
    identity = str(identity)
    if len(identity) > 128:
        raise ValueError("Invalid QQ notification scope")
    key = [connection["id"], name, event.get("id") or data]
    identifier = "notice_" + hashlib.sha256(json.dumps(key, sort_keys=True).encode()).hexdigest()
    projected = safe_result(data)
    inserted = await store.record_notification(identifier, connection, name, kind, identity, projected)
    if inserted and name == "INTERACTION_CREATE" and data.get("type") in {11, 12} and data.get("id"):
        from urllib.parse import quote

        try:
            async with asyncio.timeout(2.5):
                await client.request_value("PUT", "/interactions/" + quote(str(data["id"]), safe=""), {"code": 0})
        except (AdapterError, httpx.HTTPError, ValueError, TimeoutError):
            # A lost acknowledgement must not pin or replay an authenticated event.
            await store.set_channel_state(connection["id"], "interaction_ack:" + identifier, {"state": "unknown"})
