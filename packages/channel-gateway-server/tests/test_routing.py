import pytest
from channel_gateway_server.routing import select_route


@pytest.mark.parametrize("kind", ["private", "group", "guild", "guild_dm", "channel", "thread"])
def test_all_conversations_matches_every_supported_kind_but_specific_denial_wins(kind):
    broad = {"id": "all", "sources": [{"channel_id": "*", "conversation_kind": "*", "conversation_id": "*"}]}
    specific = {
        "id": "specific",
        "enabled": False,
        "sources": [{"channel_id": "*", "conversation_kind": kind, "conversation_id": "*"}],
    }
    assert select_route([broad], "account", kind, "123") == broad
    assert select_route([broad, specific], "account", kind, "123") == specific
    assert select_route([specific, broad], "account", kind, "123") == specific
