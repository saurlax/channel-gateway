"""Incoming text/image envelopes shared by channel and Host adapters."""

from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator


class Attachment(BaseModel):
    """Private source for received audio, video, and files."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    kind: Literal["audio", "video", "file"]
    url: str = Field(min_length=1, max_length=4096)
    media_type: str = Field(default="application/octet-stream", max_length=128)
    filename: str = Field(default="attachment", min_length=1, max_length=255)


class ImageAttachment(BaseModel):
    """A private channel image reference, resolved by the owning adapter."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    url: str = Field(min_length=1, max_length=4096)


class ChannelAddress(BaseModel):
    """A conversation identified within one channel account."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    channel: str = Field(min_length=1)
    account_id: str = Field(min_length=1)
    conversation_id: str = Field(min_length=1)


class Message(BaseModel):
    """An incoming message preserving the channel's identity and event time.

    ``source_message_id`` is scoped to ``address``. It is not a globally unique
    identifier or evidence that a message has been delivered to an Agent Host.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    address: ChannelAddress
    source_message_id: str = Field(min_length=1)
    sender_id: str = Field(min_length=1)
    text: str = Field(default="", max_length=60000)
    images: tuple[ImageAttachment, ...] = Field(default=(), max_length=4)
    attachments: tuple[Attachment, ...] = Field(default=(), max_length=16)
    structured: dict = Field(default_factory=dict)
    guild_id: str | None = Field(default=None, max_length=128)
    native_reference_id: str | None = Field(default=None, max_length=1024)
    bot_mentioned: bool | None = None
    reply_event_id: str | None = Field(default=None, max_length=1024, exclude=True)
    occurred_at: AwareDatetime

    @model_validator(mode="after")
    def has_content(self) -> "Message":
        if not self.text.strip() and not self.images and not self.attachments and not self.structured:
            raise ValueError("A message requires text or images")
        return self
