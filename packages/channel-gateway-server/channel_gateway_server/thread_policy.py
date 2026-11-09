"""Stable delivery scopes, independent of channel authorization and Host I/O."""

import hashlib
import json


def thread_binding_key(route: dict, message: dict) -> str | None:
    policy = route.get("thread_policy", "conversation")
    if policy == "message":
        return None
    owner = route.get("parent_route_id") or route["id"]
    identity = [owner, route["host_id"], route["agent_id"], policy, route.get("fixed_thread_id")]
    if route.get("conversation_memory"):
        identity.append("conversation-memory")
    settings = [
        route.get("environment_scope", "session"),
        route.get("session_idle_minutes"),
        route.get("global_memory_space_ids", []),
        route.get("shared_memory", False),
    ]
    if settings != ["session", None, [], False]:
        identity.extend(settings)
    if policy != "fixed":
        identity.append(route["id"])
    if policy == "sender":
        identity.append(message["sender_id"])
    return hashlib.sha256(json.dumps(identity).encode()).hexdigest()


def environment_binding_key(route: dict) -> str | None:
    scope = route.get("environment_scope", "session")
    if scope == "session":
        return None
    identity = [route["host_id"], route["agent_id"], scope]
    if scope == "conversation":
        identity.extend([route["channel_id"], route["conversation_kind"], route["conversation_id"]])
    return hashlib.sha256(json.dumps(identity).encode()).hexdigest()
