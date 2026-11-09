"""Bounded channel diagnostics, never upstream bodies or private Agent output."""

import re


def failure_notice(failure: object = None, *, status: str | None = None) -> str:
    """Translate known failure categories without publishing arbitrary Host text."""
    if status == "cancelled":
        return "Agent request was cancelled."
    code = failure.get("code") if isinstance(failure, dict) else None
    message = failure.get("message", "") if isinstance(failure, dict) else failure
    http_status = re.search(r"\bHTTP ([1-5][0-9]{2})\b", message) if isinstance(message, str) else None
    if http_status:
        number = int(http_status[1])
        if number == 429:
            return "Agent request failed: service rate limit or quota exhausted (HTTP 429). Please try again later."
        if number in {401, 403}:
            return "Agent request failed: service authorization failed. Please contact the administrator."
        if number >= 500:
            return "Agent request failed: upstream service is unavailable. Please try again later."
    if code == "configuration_unavailable":
        return "Agent request failed: Agent configuration is unavailable. Please contact the administrator."
    if code == "model_recovery_exhausted":
        return "Agent request failed: model execution could not recover. Please try again later."
    return "Agent request failed. Please try again later or contact the administrator."
