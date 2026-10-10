from datetime import UTC, datetime

import pytest
from channel_gateway_core import ChannelAddress, Message
from pydantic import ValidationError


def test_source_identity_and_time_survive_json_round_trip() -> None:
    message = Message(
        address=ChannelAddress(channel="onebot", account_id="bot-1", conversation_id="group-42"),
        source_message_id="msg-7",
        sender_id="user-9",
        text="Please check the other channel.",
        occurred_at=datetime(2026, 10, 10, tzinfo=UTC),
    )
    assert Message.model_validate_json(message.model_dump_json()) == message


def test_ambiguous_event_time_is_rejected() -> None:
    with pytest.raises(ValidationError, match="timezone"):
        Message(
            address=ChannelAddress(channel="onebot", account_id="bot-1", conversation_id="group-42"),
            source_message_id="msg-7",
            sender_id="user-9",
            text="Hello",
            occurred_at=datetime(2026, 10, 10),
        )


def test_incomplete_channel_address_is_rejected() -> None:
    with pytest.raises(ValidationError):
        ChannelAddress(channel="onebot", account_id="", conversation_id="group-42")
