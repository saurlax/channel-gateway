"""Validated management contracts; secrets never appear in public views."""

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator

type Identifier = Annotated[str, Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.:@-]+$")]
type UserIds = Annotated[list[Identifier], Field(min_length=1, max_length=256)]


@dataclass(frozen=True)
class Settings:
    admin_token: str | None = None
    data_dir: Path = Path(".channel-gateway")
    worker_enabled: bool = True
    poll_interval: float = 2.0

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            admin_token=os.getenv("CHANNEL_GATEWAY_ADMIN_TOKEN"),
            data_dir=Path(os.getenv("CHANNEL_GATEWAY_DATA_DIR", ".channel-gateway")),
        )


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ConnectionInput(Contract):
    name: str = Field(min_length=1, max_length=80)
    kind: Literal["onebots", "a13n"]
    api_url: str = Field(max_length=2048)
    token: SecretStr = Field(min_length=1, max_length=4096)
    self_id: Identifier | None = None
    webhook_secret: SecretStr | None = Field(default=None, min_length=16, max_length=4096)

    @field_validator("api_url")
    @classmethod
    def valid_url(cls, value: str) -> str:
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("Use an HTTP(S) API URL without embedded credentials")
        if parsed.query or parsed.fragment:
            raise ValueError("API URLs cannot contain a query or fragment")
        return value.rstrip("/")

    @model_validator(mode="after")
    def onebot_identity(self) -> "ConnectionInput":
        if self.kind == "onebots" and (not self.self_id or not self.webhook_secret):
            raise ValueError("OneBots requires a self ID and webhook signing secret")
        if self.kind == "a13n" and (self.self_id or self.webhook_secret):
            raise ValueError("a13n does not accept OneBot identity fields")
        return self

    def stored(self) -> dict:
        value = self.model_dump(mode="json")
        value["token"] = self.token.get_secret_value()
        value["webhook_secret"] = self.webhook_secret.get_secret_value() if self.webhook_secret else None
        return value


class RouteInput(Contract):
    name: str = Field(min_length=1, max_length=80)
    channel_id: Identifier
    host_id: Identifier
    conversation_kind: Literal["private", "group"]
    conversation_id: Identifier
    allowed_user_ids: UserIds
    agent_id: Identifier
    enabled: bool = True
    auto_reply: bool = False
    reply_source: Literal["output", "mcp"] = "output"
    mcp_connection_id: Identifier | None = None

    @model_validator(mode="after")
    def reply_policy(self) -> "RouteInput":
        if self.conversation_kind == "group" and self.auto_reply:
            raise ValueError("Group replies require administrator approval")
        if self.conversation_kind == "private" and self.allowed_user_ids != [self.conversation_id]:
            raise ValueError("A private route authorizes only its conversation user")
        return self


class RouteUpdate(Contract):
    enabled: bool | None = None
    allowed_user_ids: UserIds | None = None
    reply_source: Literal["output", "mcp"] | None = None
    mcp_connection_id: Identifier | None = None


class ConnectionSecrets(Contract):
    token: SecretStr | None = Field(default=None, min_length=1, max_length=4096)
    webhook_secret: SecretStr | None = Field(default=None, min_length=16, max_length=4096)


class ReplyInput(Contract):
    text: str = Field(min_length=1, max_length=16000)


def public_connection(row: dict) -> dict:
    return {key: value for key, value in row.items() if key not in {"token", "webhook_secret"}}
