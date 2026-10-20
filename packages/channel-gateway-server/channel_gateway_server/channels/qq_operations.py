"""Finite QQ resource catalog. Routes bind destinations; agents supply no API paths."""

import json
import re
from dataclasses import dataclass
from urllib.parse import quote, urlencode

from ..models import route_allows_sender
from .common import SendBlocked

GRANTS = (
    "resource.read",
    "message.react",
    "message.recall",
    "message.recall_members",
    "message.proactive",
    "group.manage",
    "guild.manage",
    "content.manage",
    "bot.manage",
)


@dataclass(frozen=True)
class Operation:
    name: str
    method: str
    path: str
    scope: str
    grant: str
    fields: str = ""
    restriction: str = "platform permissions apply"

    @property
    def readonly(self) -> bool:
        return self.method == "GET"

    @property
    def path_fields(self) -> set[str]:
        return set(re.findall(r"{(\w+)}", self.path)) - {"target", "guild_id", "channel_id"}

    def schema(self) -> dict:
        properties = {name: {"type": "string", "minLength": 1, "maxLength": 1024} for name in self.path_fields}
        if self.scope == "channel":
            properties["target_channel_id"] = {"type": "string", "minLength": 1, "maxLength": 128}
        required = list(sorted(self.path_fields))
        for field in self.fields.split():
            name, _, kind = field.rstrip("!").partition(":")
            schema: dict = {"type": kind or "string"}
            if schema["type"] == "string":
                schema["maxLength"] = 16000
            elif schema["type"] == "integer":
                schema.update(minimum=-1, maximum=9007199254740991)
            elif schema["type"] == "array":
                schema.update(items={}, maxItems=10000)
            properties[name] = schema
            if field.endswith("!"):
                required.append(name)
        return {"type": "object", "properties": properties, "required": required, "additionalProperties": False}

    def describe(self, route: dict) -> dict:
        return {
            "name": self.name,
            "readonly": self.readonly,
            "scope": self.scope,
            "required_grant": self.grant,
            "authorized": self.grant in route.get("action_grants", []),
            "restriction": self.restriction,
            "arguments_schema": self.schema(),
        }

    def validate(self, arguments: dict) -> None:
        schema = self.schema()
        if len(json.dumps(arguments)) > 65536 or arguments.keys() - schema["properties"].keys():
            raise ValueError("Unsupported or oversized operation arguments")
        if set(schema["required"]) - arguments.keys():
            raise ValueError("Required operation arguments are missing")
        types = {"string": str, "object": dict, "array": list, "integer": int, "boolean": bool}
        for name, value in arguments.items():
            shape = schema["properties"][name]
            kind = shape["type"]
            if not isinstance(value, types[kind]) or (kind == "integer" and isinstance(value, bool)):
                raise ValueError("Invalid operation argument type: " + name)
            if kind == "integer" and not shape["minimum"] <= value <= shape["maximum"]:
                raise ValueError("Operation integer exceeds its limit")
            if isinstance(value, (str, list)) and len(value) > shape.get("maxLength", shape.get("maxItems", 10000)):
                raise ValueError("Operation argument exceeds its limit")
            if name in self.path_fields and (
                not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_.:@=-]{1,1024}", value)
            ):
                raise ValueError("Invalid resource identifier")
        check_json(arguments)


def check_json(value: object, depth: int = 0) -> None:
    if depth > 12:
        raise ValueError("Operation arguments are too deeply nested")
    if isinstance(value, dict):
        for key, child in value.items():
            if key.lower() in {"authorization", "access_token", "app_secret", "context_token", "auth_token"}:
                raise ValueError("Credentials are not operation arguments")
            check_json(child, depth + 1)
    elif isinstance(value, list):
        for child in value:
            check_json(child, depth + 1)


def op(name, method, path, scope, grant, fields="", restriction="platform permissions apply"):
    return Operation(name, method, path, scope, grant, fields, restriction)


OPERATIONS = {
    item.name: item
    for item in [
        op("get_bot", "GET", "/users/@me", "account", "resource.read"),
        op("list_guilds", "GET", "/users/@me/guilds", "account", "bot.manage", "before after limit:integer"),
        op("create_share_link", "POST", "/v2/generate_url_link", "account", "bot.manage", "callback_data"),
        op("get_menu", "GET", "/v2/menu", "account", "bot.manage"),
        op("set_menu", "PUT", "/v2/menu", "account", "bot.manage", "menu:object!"),
        op("list_panels", "GET", "/v2/panels", "account", "bot.manage", "scope! cursor limit:integer"),
        op(
            "create_panel",
            "POST",
            "/v2/panels",
            "account",
            "bot.manage",
            "scope! target_type user_openids:array group_openids:array panel:object!",
        ),
        op("get_panel", "GET", "/v2/panels/{panel_id}", "account", "bot.manage"),
        op("update_panel", "PUT", "/v2/panels/{panel_id}", "account", "bot.manage", "panel:object!"),
        op("delete_panel", "DELETE", "/v2/panels/{panel_id}", "account", "bot.manage"),
        op(
            "set_panel_targets",
            "PUT",
            "/v2/panels/{panel_id}/target",
            "account",
            "bot.manage",
            "op! user_openids:array group_openids:array",
        ),
        op("get_group", "GET", "/v2/groups/{target}/info", "group", "resource.read"),
        op("get_group_bot_state", "GET", "/v2/groups/{target}/bot_state", "group", "resource.read"),
        op(
            "list_group_members",
            "GET",
            "/v2/groups/{target}/members",
            "group",
            "resource.read",
            "cursor limit:integer",
            "invitation only",
        ),
        op(
            "get_group_member",
            "GET",
            "/v2/groups/{target}/members/{member_openid}",
            "group",
            "resource.read",
            restriction="invitation only",
        ),
        op(
            "remove_group_members",
            "POST",
            "/v2/groups/{target}/batch_remove_members",
            "group",
            "group.manage",
            "member_openids:array! add_to_member_blacklist:boolean",
            "invitation only; administrator",
        ),
        op(
            "list_group_blacklist",
            "GET",
            "/v2/groups/{target}/member_blacklist",
            "group",
            "resource.read",
            "cursor limit:integer",
            "invitation only",
        ),
        op(
            "set_group_blacklist",
            "POST",
            "/v2/groups/{target}/member_blacklist",
            "group",
            "group.manage",
            "op! member_openids:array!",
            "invitation only; administrator",
        ),
        op(
            "list_join_requests",
            "GET",
            "/v2/groups/{target}/join_request_list",
            "group",
            "resource.read",
            "cursor limit:integer",
            "administrator",
        ),
        op(
            "approve_join_request",
            "POST",
            "/v2/groups/{target}/approval_join_request/{member_openid}",
            "group",
            "group.manage",
            "op! join_request_id reject_reason add_to_member_blacklist:boolean",
            "administrator",
        ),
        op(
            "get_group_mutes",
            "GET",
            "/v2/groups/{target}/restrict_chat_setting",
            "group",
            "resource.read",
            restriction="administrator",
        ),
        op(
            "set_group_mutes",
            "POST",
            "/v2/groups/{target}/restrict_chat_setting",
            "group",
            "group.manage",
            "members:array!",
            "administrator; maximum 30 days",
        ),
        op(
            "list_join_strategies",
            "GET",
            "/v2/groups/join_approval_strategy",
            "account",
            "bot.manage",
            "cursor limit:integer",
        ),
        op(
            "create_join_strategy",
            "POST",
            "/v2/groups/join_approval_strategy",
            "account",
            "bot.manage",
            "group_openids:array group_ids:array is_enable expire_at remark",
        ),
        op(
            "update_join_strategy",
            "PATCH",
            "/v2/groups/join_approval_strategy/{strategy_id}",
            "account",
            "bot.manage",
            "is_enable expire_at group_action:object remark",
        ),
        op(
            "delete_join_strategy", "DELETE", "/v2/groups/join_approval_strategy/{strategy_id}", "account", "bot.manage"
        ),
        op(
            "execute_join_strategy",
            "POST",
            "/v2/groups/join_approval_strategy/{strategy_id}/execute",
            "account",
            "bot.manage",
        ),
        op(
            "set_join_whitelist",
            "POST",
            "/v2/groups/join_approval_strategy/{strategy_id}/whitelist_users",
            "account",
            "bot.manage",
            "op! whitelist_users:array!",
        ),
        op("get_guild", "GET", "/guilds/{guild_id}", "guild", "resource.read"),
        op("open_direct_message", "POST", "/users/@me/dms", "guild", "guild.manage", "recipient_id!"),
        op("list_subchannels", "GET", "/guilds/{guild_id}/channels", "guild", "resource.read"),
        op(
            "create_subchannel",
            "POST",
            "/guilds/{guild_id}/channels",
            "guild",
            "guild.manage",
            "name! type:integer! sub_type:integer position:integer parent_id private_type:integer private_user_ids:array speak_permission:integer application_id",
            "private-domain administrator",
        ),
        op("get_subchannel", "GET", "/channels/{channel_id}", "channel", "resource.read"),
        op(
            "update_subchannel",
            "PATCH",
            "/channels/{channel_id}",
            "channel",
            "guild.manage",
            "name position:integer parent_id private_type:integer speak_permission:integer",
            "private-domain administrator",
        ),
        op(
            "delete_subchannel",
            "DELETE",
            "/channels/{channel_id}",
            "channel",
            "guild.manage",
            restriction="private-domain administrator",
        ),
        op("get_online_members", "GET", "/channels/{channel_id}/online_nums", "channel", "resource.read"),
        op(
            "list_guild_members",
            "GET",
            "/guilds/{guild_id}/members",
            "guild",
            "resource.read",
            "after limit:integer",
            "private-domain only",
        ),
        op("get_guild_member", "GET", "/guilds/{guild_id}/members/{user_id}", "guild", "resource.read"),
        op(
            "remove_guild_member",
            "DELETE",
            "/guilds/{guild_id}/members/{user_id}",
            "guild",
            "guild.manage",
            "add_blacklist:boolean delete_history_msg_days:integer",
            "administrator",
        ),
        op("list_roles", "GET", "/guilds/{guild_id}/roles", "guild", "resource.read"),
        op(
            "list_role_members",
            "GET",
            "/guilds/{guild_id}/roles/{role_id}/members",
            "guild",
            "resource.read",
            "start_index limit:integer",
            "private-domain only",
        ),
        op(
            "create_role",
            "POST",
            "/guilds/{guild_id}/roles",
            "guild",
            "guild.manage",
            "name color:integer hoist:integer",
            "administrator",
        ),
        op(
            "update_role",
            "PATCH",
            "/guilds/{guild_id}/roles/{role_id}",
            "guild",
            "guild.manage",
            "name color:integer hoist:integer",
            "administrator",
        ),
        op(
            "delete_role",
            "DELETE",
            "/guilds/{guild_id}/roles/{role_id}",
            "guild",
            "guild.manage",
            restriction="administrator",
        ),
        op(
            "add_role_member",
            "PUT",
            "/guilds/{guild_id}/members/{user_id}/roles/{role_id}",
            "guild",
            "guild.manage",
            "channel:object",
            "administrator",
        ),
        op(
            "remove_role_member",
            "DELETE",
            "/guilds/{guild_id}/members/{user_id}/roles/{role_id}",
            "guild",
            "guild.manage",
            "channel:object",
            "administrator",
        ),
        op(
            "get_member_permissions",
            "GET",
            "/channels/{channel_id}/members/{user_id}/permissions",
            "channel",
            "resource.read",
        ),
        op(
            "set_member_permissions",
            "PUT",
            "/channels/{channel_id}/members/{user_id}/permissions",
            "channel",
            "guild.manage",
            "add! remove!",
            "administrator",
        ),
        op(
            "get_role_permissions",
            "GET",
            "/channels/{channel_id}/roles/{role_id}/permissions",
            "channel",
            "resource.read",
        ),
        op(
            "set_role_permissions",
            "PUT",
            "/channels/{channel_id}/roles/{role_id}/permissions",
            "channel",
            "guild.manage",
            "add! remove!",
            "administrator",
        ),
        op(
            "mute_guild_member",
            "PATCH",
            "/guilds/{guild_id}/members/{user_id}/mute",
            "guild",
            "guild.manage",
            "mute_end_timestamp mute_seconds",
            "administrator",
        ),
        op(
            "mute_guild",
            "PATCH",
            "/guilds/{guild_id}/mute",
            "guild",
            "guild.manage",
            "mute_end_timestamp mute_seconds user_ids:array",
            "administrator",
        ),
        op("get_message_settings", "GET", "/guilds/{guild_id}/message/setting", "guild", "resource.read"),
        op("get_api_permissions", "GET", "/guilds/{guild_id}/api_permission", "guild", "resource.read"),
        op(
            "request_api_permission",
            "POST",
            "/guilds/{guild_id}/api_permission/demand",
            "guild",
            "guild.manage",
            "channel_id! api_identify:object! desc!",
        ),
        op(
            "create_announcement",
            "POST",
            "/guilds/{guild_id}/announces",
            "guild",
            "content.manage",
            "message_id channel_id announces_type:integer recommend_channels:array",
        ),
        op("delete_announcement", "DELETE", "/guilds/{guild_id}/announces/{message_id}", "guild", "content.manage"),
        op("list_pins", "GET", "/channels/{channel_id}/pins", "channel", "resource.read"),
        op(
            "list_reaction_users",
            "GET",
            "/channels/{channel_id}/messages/{message_id}/reactions/{emoji_type}/{emoji_id}",
            "channel",
            "resource.read",
            "cookie limit:integer",
        ),
        op("add_pin", "PUT", "/channels/{channel_id}/pins/{message_id}", "channel", "content.manage"),
        op("remove_pin", "DELETE", "/channels/{channel_id}/pins/{message_id}", "channel", "content.manage"),
        op("list_schedules", "GET", "/channels/{channel_id}/schedules", "channel", "resource.read", "since"),
        op("get_schedule", "GET", "/channels/{channel_id}/schedules/{schedule_id}", "channel", "resource.read"),
        op(
            "create_schedule",
            "POST",
            "/channels/{channel_id}/schedules",
            "channel",
            "content.manage",
            "schedule:object!",
            "administrator",
        ),
        op(
            "update_schedule",
            "PATCH",
            "/channels/{channel_id}/schedules/{schedule_id}",
            "channel",
            "content.manage",
            "schedule:object!",
            "administrator",
        ),
        op(
            "delete_schedule",
            "DELETE",
            "/channels/{channel_id}/schedules/{schedule_id}",
            "channel",
            "content.manage",
            restriction="administrator",
        ),
        op(
            "list_threads",
            "GET",
            "/channels/{channel_id}/threads",
            "channel",
            "resource.read",
            restriction="private-domain only",
        ),
        op(
            "get_thread",
            "GET",
            "/channels/{channel_id}/threads/{thread_id}",
            "channel",
            "resource.read",
            restriction="private-domain only",
        ),
        op(
            "create_thread",
            "PUT",
            "/channels/{channel_id}/threads",
            "channel",
            "content.manage",
            "title! content! format:integer!",
            "private-domain only",
        ),
        op(
            "delete_thread",
            "DELETE",
            "/channels/{channel_id}/threads/{thread_id}",
            "channel",
            "content.manage",
            restriction="private-domain only",
        ),
        op(
            "control_audio",
            "POST",
            "/channels/{channel_id}/audio",
            "channel",
            "content.manage",
            "audio_url text status:integer!",
            "audio bot permission required",
        ),
        op(
            "join_microphone",
            "PUT",
            "/channels/{channel_id}/mic",
            "channel",
            "content.manage",
            restriction="audio bot permission required",
        ),
        op(
            "leave_microphone",
            "DELETE",
            "/channels/{channel_id}/mic",
            "channel",
            "content.manage",
            restriction="audio bot permission required",
        ),
    ]
}


def applicable(operation: Operation, route: dict) -> bool:
    kind = route["conversation_kind"]
    return (
        operation.scope == "account"
        or (operation.scope == "group" and kind == "group")
        or (operation.scope == "guild" and kind in {"guild", "guild_dm"})
        or (operation.scope == "channel" and kind == "guild")
    )


def require_grant(route: dict, grant: str) -> None:
    if not route["enabled"] or grant not in route.get("action_grants", []):
        raise SendBlocked("This route does not authorize " + grant)


async def execute_operation(store, client, route: dict, operation: Operation, arguments: dict):
    require_grant(route, operation.grant)
    operation.validate(arguments)
    if not applicable(operation, route):
        raise SendBlocked("Operation is not supported in this conversation type")

    async def recheck():
        current = await store.connection(route["channel_id"])
        if (
            await store.route(route["id"]) != route
            or not current
            or any(
                current.get(key) != client.connection.get(key)
                for key in ("kind", "app_id", "app_secret", "transport", "qq_intents")
            )
        ):
            raise SendBlocked("Route or channel authority changed")

    await recheck()
    context = await store.channel_state(
        route["channel_id"], "context:" + route["conversation_kind"] + ":" + route["conversation_id"]
    )
    bindings = {
        "target": route["conversation_id"],
        "channel_id": arguments.get("target_channel_id") or route["conversation_id"],
    }
    if operation.scope in {"guild", "channel"}:
        if not context.get("guild_id") or not route_allows_sender(route, context.get("sender_id", "")):
            raise SendBlocked("Wait for an authorized guild message to establish guild context")
        bindings["guild_id"] = context["guild_id"]
        # Validate every body-supplied child channel against the same verified guild.
        channel_ids = [arguments["target_channel_id"]] if arguments.get("target_channel_id") else []

        def collect(value):
            if isinstance(value, dict):
                for key, child in value.items():
                    if key in {"channel_id", "parent_id", "jump_channel_id"} and child not in {"", "0", 0}:
                        channel_ids.append(str(child))
                    else:
                        collect(child)
            elif isinstance(value, list):
                for child in value:
                    collect(child)

        collect(arguments)
        for channel_id in set(channel_ids):
            await recheck()
            info = await client.request("GET", "/channels/" + quote(channel_id, safe=""))
            if info.get("guild_id") != context["guild_id"]:
                raise SendBlocked("The selected child channel belongs to another guild")
    await recheck()
    path = operation.path.format(
        **{key: quote(str(value), safe="") for key, value in {**arguments, **bindings}.items()}
    )
    body = {
        key: value
        for key, value in arguments.items()
        if key not in operation.path_fields and key != "target_channel_id"
    }
    if operation.name == "open_direct_message":
        body["source_guild_id"] = bindings["guild_id"]
    if operation.readonly:
        path += ("?" + urlencode(body, doseq=True)) if body else ""
    return await client.request_value(operation.method, path, None if operation.readonly else body)


def safe_result(value: object):
    private = {
        "access_token",
        "app_secret",
        "auth_token",
        "context_token",
        "file_info",
        "presigned_url",
        "raw_url",
        "upload_id",
    }
    if isinstance(value, dict):
        return {
            key: safe_result(child)
            for key, child in value.items()
            if key.lower() not in private and key not in {"attachments", "message_scene"}
        }
    if isinstance(value, list):
        return [safe_result(child) for child in value]
    if isinstance(value, str) and value.startswith(("auth_token=", "context_token=", "access_token=")):
        return "[private]"
    return value
