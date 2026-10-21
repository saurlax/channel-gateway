"""Typed reaction identifiers; representations are validated by the adapter."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator


class UnicodeEmoji(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["unicode"]
    value: str = Field(min_length=1, max_length=64)


class CustomEmoji(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["custom"]
    name: str | None = Field(default=None, min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_+-]+$")
    id: str | None = Field(default=None, min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_]+$")
    animated: bool = False

    @model_validator(mode="after")
    def identity(self):
        if self.name is None and self.id is None:
            raise ValueError("Custom emoji requires a name or ID")
        return self


class PlatformEmoji(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["platform"]
    value: str = Field(min_length=1, max_length=128)
    type: int | None = Field(default=None, ge=1, le=2)


type Emoji = Annotated[UnicodeEmoji | CustomEmoji | PlatformEmoji, Field(discriminator="kind")]
EMOJI = TypeAdapter(Emoji)


def validate_platform_emoji(platform: str, emoji: object) -> dict:
    value = EMOJI.validate_python(emoji).model_dump(exclude_none=True)
    kind = value["kind"]
    if platform == "slack" and (kind != "custom" or not value.get("name") or value.get("id") or value.get("animated")):
        raise ValueError("Slack reactions require an emoji name, e.g. custom/name='thumbsup'")
    if platform == "lark" and (kind != "platform" or value.get("type") is not None):
        raise ValueError("Lark reactions require a platform emoji_type, e.g. platform/value='THUMBSUP'")
    if platform == "discord" and not (
        kind == "unicode" or (kind == "custom" and value.get("name") and value.get("id", "").isdecimal())
    ):
        raise ValueError("Discord reactions require Unicode or a custom name and snowflake ID")
    if platform in {"qq", "onebots"} and (
        kind != "platform" or not value["value"].isdecimal() or len(value["value"]) > 20
    ):
        raise ValueError("QQ and OneBot reactions require a numeric platform emoji value")
    return value
