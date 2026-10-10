"""Initial incoming text-message envelope shared by channel and Host adapters."""

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field


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
    text: str = Field(min_length=1)
    occurred_at: AwareDatetime
