"""Host diagnostics expose fixed categories rather than private error messages."""

import pytest
from channel_gateway_server.host_failures import failure_notice


@pytest.mark.parametrize(
    "failure,expected",
    [
        (None, "Agent request failed."),
        ({"code": "unknown", "message": "secret token https://private.example"}, "Agent request failed."),
        ({"code": "configuration_unavailable"}, "configuration is unavailable"),
        ({"code": "model_recovery_exhausted"}, "model execution could not recover"),
        ({"message": "private prompt HTTP 429 secret"}, "HTTP 429"),
        ({"message": "HTTP 401 secret"}, "authorization failed"),
        ({"message": "HTTP 403 secret"}, "authorization failed"),
        ({"message": "HTTP 503 secret"}, "upstream service is unavailable"),
        ({"message": ["secret"]}, "Agent request failed."),
    ],
)
def test_failure_notice_is_bounded_and_safe(failure, expected):
    notice = failure_notice(failure)
    assert expected in notice
    assert "secret" not in notice and "private" not in notice
    assert len(notice) < 200


def test_cancelled_run_is_reported_without_its_private_reason():
    assert failure_notice({"message": "secret"}, status="cancelled") == "Agent request was cancelled."
