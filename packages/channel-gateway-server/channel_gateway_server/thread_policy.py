"""Stable delivery scopes, independent of channel authorization and Host I/O."""

import hashlib
import json


def thread_binding_key(route: dict, message: dict) -> str | None:
    policy = route.get("thread_policy", "conversation")
    if policy == "message":
        return None
    owner = route.get("parent_route_id") or route["id"]
    identity = [owner, route["host_id"], route["agent_id"], policy, route.get("fixed_thread_id")]
    if policy != "fixed":
        identity.append(route["id"])
    if policy == "sender":
        identity.append(message["sender_id"])
    return hashlib.sha256(json.dumps(identity).encode()).hexdigest()
