"""Validated management contracts; secrets never appear in public views."""

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator

from .media import MAX_IMAGE_BASE64, decode_image

type Identifier = Annotated[str, Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.:@-]+$")]
type SenderIds = Annotated[list[Identifier], Field(max_length=256)]
type ReadChannels = Annotated[list[Identifier | Literal["*"]], Field(max_length=100)]
type ThreadPolicy = Literal["conversation", "sender", "fixed", "message"]
type BusyPolicy = Literal["steer", "queue"]
type ConversationKind = Literal["private", "group", "guild", "guild_dm", "channel", "thread"]
type ActionGrant = Literal[
    "history.read",
    "history.search",
    "resource.read",
    "message.react",
    "message.recall",
    "message.recall_members",
    "message.proactive",
    "group.manage",
    "guild.manage",
    "content.manage",
    "bot.manage",
]
type ActionGrants = Annotated[list[ActionGrant], Field(max_length=11)]


@dataclass(frozen=True)
class Settings:
    admin_username: str | None = None
    admin_password: str | None = field(default=None, repr=False)
    data_dir: Path = Path(".channel-gateway")
    worker_enabled: bool = True
    poll_interval: float = 2.0

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            admin_username=os.getenv("CGW_ADMIN_USERNAME") or None,
            admin_password=os.getenv("CGW_ADMIN_PASSWORD") or None,
            data_dir=Path(os.getenv("CGW_DATA_DIR", ".channel-gateway")),
        )


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AdministratorLogin(Contract):
    username: str = Field(min_length=1, max_length=128)
    password: SecretStr = Field(min_length=1, max_length=4096)


class ConnectionInput(Contract):
    name: str = Field(min_length=1, max_length=80)
    kind: Literal["onebots", "qq", "weixin", "slack", "lark", "discord", "a13n", "mcp"]
    transport: Literal["http", "http_reverse", "ws", "ws_reverse", "websocket", "webhook", "polling"] | None = None
    api_url: str | None = Field(default=None, max_length=2048)
    token: SecretStr | None = Field(default=None, min_length=1, max_length=4096)
    self_id: Identifier | None = None
    webhook_secret: SecretStr | None = Field(default=None, min_length=16, max_length=4096)
    qq_intents: int | None = Field(default=None, ge=1, le=2147483647)
    app_id: Identifier | None = None
    app_secret: SecretStr | None = Field(default=None, min_length=1, max_length=4096)

    encrypt_key: SecretStr | None = Field(default=None, min_length=1, max_length=4096)
    discord_intents: int | None = Field(default=None, ge=1, le=2147483647)

    @field_validator("api_url")
    @classmethod
    def valid_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        parsed = urlsplit(value)
        if (
            parsed.scheme not in {"http", "https", "ws", "wss"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
        ):
            raise ValueError("Use an HTTP(S) or WS(S) URL without embedded credentials")
        if parsed.query or parsed.fragment:
            raise ValueError("API URLs cannot contain a query or fragment")
        return value.rstrip("/")

    @model_validator(mode="after")
    def connection_contract(self) -> "ConnectionInput":
        defaults = {
            "onebots": "http_reverse",
            "qq": "websocket",
            "weixin": "polling",
            "slack": "webhook",
            "lark": "webhook",
            "discord": "websocket",
            "a13n": None,
            "mcp": None,
        }
        self.transport = self.transport or defaults[self.kind]
        if self.kind == "onebots":
            if self.transport not in {"http", "http_reverse", "ws", "ws_reverse"} or not self.token or not self.self_id:
                raise ValueError("OneBot requires a transport, access token, and bot self ID")
            if self.transport == "http_reverse" and not self.webhook_secret:
                raise ValueError("OneBots requires a self ID and webhook signing secret")
            if self.transport != "http_reverse" and self.webhook_secret:
                raise ValueError("Only reverse HTTP uses a signing secret")
            if self.transport == "ws_reverse":
                if self.api_url:
                    raise ValueError("Reverse WebSocket uses the gateway-generated URL")
            else:
                schemes = {"ws", "wss"} if self.transport == "ws" else {"http", "https"}
                if not self.api_url or urlsplit(self.api_url).scheme not in schemes:
                    raise ValueError("The URL scheme must match the OneBot transport")
        elif self.kind == "qq":
            if self.transport not in {"websocket", "webhook"} or not self.app_id or not self.app_secret:
                raise ValueError("QQ requires an AppID, AppSecret, and receive mode")
            if not self.app_secret.get_secret_value().isascii():
                raise ValueError("QQ AppSecret must be ASCII")
            self.api_url = self.api_url or "https://api.sgroup.qq.com"
            if self.api_url != "https://api.sgroup.qq.com" or self.token or self.self_id or self.webhook_secret:
                raise ValueError("QQ uses official endpoints and its application credentials")
        elif self.kind == "slack":
            if (
                self.transport != "webhook"
                or not self.token
                or not self.self_id
                or not self.app_id
                or not self.webhook_secret
            ):
                raise ValueError("Slack requires a bot token, bot user ID, workspace ID, and signing secret")
            if not self.token.get_secret_value().startswith("xoxb-") or self.api_url or self.app_secret:
                raise ValueError("Slack uses bot credentials and official API endpoints")
        elif self.kind == "lark":
            if (
                self.transport != "webhook"
                or not self.app_id
                or not self.app_secret
                or not self.self_id
                or not self.webhook_secret
                or not self.encrypt_key
                or self.token
            ):
                raise ValueError(
                    "Lark requires application credentials, bot open ID, verification token, and encrypt key"
                )
            self.api_url = self.api_url or "https://open.feishu.cn"
            if self.api_url not in {"https://open.feishu.cn", "https://open.larksuite.com"}:
                raise ValueError("Choose the official Feishu or Lark API region")
        elif self.kind == "discord":
            if (
                self.transport != "websocket"
                or not self.token
                or not self.self_id
                or self.api_url
                or self.webhook_secret
                or self.app_id
                or self.app_secret
            ):
                raise ValueError("Discord requires a bot token and bot user ID with Gateway transport")
        elif self.kind == "weixin":
            if self.transport != "polling" or bool(self.token) != bool(self.self_id) or self.webhook_secret:
                raise ValueError("Weixin uses polling; imported credentials require a bot ID and token")
            self.api_url = weixin_url(self.api_url or "https://ilinkai.weixin.qq.com")
        elif self.kind == "mcp":
            if self.transport or self.api_url or self.token or self.self_id or self.webhook_secret:
                raise ValueError("MCP hosts use a gateway-issued key and no upstream credentials")
        elif self.transport or self.self_id or self.webhook_secret or not self.token or not self.api_url:
            raise ValueError("a13n requires an HTTP API URL and workspace API key")
        elif urlsplit(self.api_url).scheme not in {"http", "https"}:
            raise ValueError("a13n requires an HTTP API URL")
        if self.kind not in {"qq", "slack", "lark"} and (self.app_id or self.app_secret):
            raise ValueError("Application credentials do not match this channel")
        if self.kind != "qq" and self.qq_intents is not None:
            raise ValueError("Only QQ uses QQ intents")
        if self.kind != "discord" and self.discord_intents is not None:
            raise ValueError("Only Discord uses Discord intents")
        if self.kind != "lark" and self.encrypt_key:
            raise ValueError("Only Lark uses an encrypt key")
        return self

    def stored(self) -> dict:
        value = self.model_dump(mode="json")
        for name in ("token", "webhook_secret", "app_secret", "encrypt_key"):
            secret = getattr(self, name)
            value[name] = secret.get_secret_value() if secret else None
        return value


class RouteSource(Contract):
    channel_id: Identifier | Literal["*"]
    conversation_kind: ConversationKind
    conversation_id: Identifier | Literal["*"]


class RouteInput(Contract):
    name: str = Field(min_length=1, max_length=80)
    channel_id: Identifier | Literal["*"] | None = None
    host_id: Identifier
    conversation_kind: ConversationKind | None = None
    conversation_id: Identifier | Literal["*"] | None = None
    sources: Annotated[list[RouteSource], Field(min_length=1, max_length=100)] | None = None
    read_channel_ids: ReadChannels | None = None
    action_grants: ActionGrants = []
    allowed_user_ids: SenderIds = []
    sender_policy: Literal["allowlist", "any"] = "allowlist"
    agent_id: Identifier | None = None
    thread_policy: ThreadPolicy = "conversation"
    busy_policy: BusyPolicy = "steer"
    fixed_thread_id: Identifier | None = None
    enabled: bool = True
    reply_source: Literal["output", "mcp"] = "output"
    mcp_connection_id: Identifier | None = None

    @model_validator(mode="after")
    def reply_policy(self) -> "RouteInput":
        if len(set(self.action_grants)) != len(self.action_grants):
            raise ValueError("Choose distinct action grants")
        if self.fixed_thread_id and self.thread_policy != "fixed":
            raise ValueError("A fixed thread requires the fixed thread policy")
        if self.read_channel_ids is not None and (
            len(set(self.read_channel_ids)) != len(self.read_channel_ids)
            or ("*" in self.read_channel_ids and self.read_channel_ids != ["*"])
        ):
            raise ValueError("Choose distinct readable channels or all channels")
        if self.sources is not None:
            if len({(s.channel_id, s.conversation_kind, s.conversation_id) for s in self.sources}) != len(self.sources):
                raise ValueError("Duplicate route sources are not allowed")
            if (self.channel_id, self.conversation_kind, self.conversation_id) not in {
                (None, None, None),
                ("*", "private", "*"),
            }:
                raise ValueError("Use sources instead of a legacy conversation destination")
            self.channel_id, self.conversation_kind, self.conversation_id = "*", "private", "*"
            if self.mcp_connection_id and (
                len(self.sources) != 1 or self.sources[0].channel_id == "*" or self.sources[0].conversation_id == "*"
            ):
                raise ValueError("A dedicated MCP connection requires one specific source")
        elif not self.channel_id or self.channel_id == "*" or not self.conversation_kind or not self.conversation_id:
            raise ValueError("Choose a channel and conversation or provide sources")
        if self.sender_policy == "allowlist" and not self.allowed_user_ids:
            raise ValueError("Allowlist routes require at least one sender ID")
        if self.sender_policy == "any" and self.allowed_user_ids:
            raise ValueError("Any-sender routes do not use an allowlist")
        if not self.sources and self.conversation_id == "*" and self.mcp_connection_id:
            raise ValueError("All-conversation routes cannot use a dedicated a13n MCP connection")
        return self


def route_allows_sender(route: dict, sender: str) -> bool:
    return bool(
        route["enabled"] and (route.get("sender_policy", "allowlist") == "any" or sender in route["allowed_user_ids"])
    )


class RouteUpdate(Contract):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    sources: Annotated[list[RouteSource], Field(min_length=1, max_length=100)] | None = None
    read_channel_ids: ReadChannels | None = None
    action_grants: ActionGrants | None = None
    host_id: Identifier | None = None
    agent_id: Identifier | None = None
    thread_policy: ThreadPolicy | None = None
    busy_policy: BusyPolicy | None = None
    fixed_thread_id: Identifier | None = None
    sender_policy: Literal["allowlist", "any"] | None = None
    enabled: bool | None = None
    allowed_user_ids: SenderIds | None = None
    reply_source: Literal["output", "mcp"] | None = None
    mcp_connection_id: Identifier | None = None


class ConnectionSecrets(Contract):
    encrypt_key: SecretStr | None = Field(default=None, min_length=1, max_length=4096)
    discord_intents: int | None = Field(default=None, ge=1, le=2147483647)
    qq_intents: int | None = Field(default=None, ge=1, le=2147483647)
    token: SecretStr | None = Field(default=None, min_length=1, max_length=4096)
    webhook_secret: SecretStr | None = Field(default=None, min_length=16, max_length=4096)
    app_secret: SecretStr | None = Field(default=None, min_length=1, max_length=4096)


class LoginPoll(Contract):
    login_id: Identifier
    verify_code: str | None = Field(default=None, min_length=1, max_length=16, pattern=r"^[0-9]+$")


def weixin_url(value: str) -> str:
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or not parsed.hostname.endswith(".weixin.qq.com")
        or parsed.port not in {None, 443}
        or parsed.username
        or parsed.password
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("Weixin requires an official HTTPS API host")
    return value.rstrip("/")


class ReplyInput(Contract):
    text: str = Field(min_length=1, max_length=16000)


class ImageReplyInput(Contract):
    image_base64: str = Field(min_length=1, max_length=MAX_IMAGE_BASE64)
    media_type: Literal["image/png", "image/jpeg", "image/gif", "image/webp"]
    caption: str = Field(default="", max_length=16000)

    @model_validator(mode="after")
    def valid_image(self) -> "ImageReplyInput":
        decode_image(self.image_base64, self.media_type)
        return self


class ReactionInput(Contract):
    source_message_id: str = Field(min_length=1, max_length=1024)
    emoji_id: str = Field(min_length=1, max_length=20, pattern=r"^[0-9]+$")
    add: bool = True


def public_connection(row: dict) -> dict:
    return {
        key: value for key, value in row.items() if key not in {"token", "webhook_secret", "app_secret", "encrypt_key"}
    }
