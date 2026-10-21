"""Source selection independent of HTTP, persistence, and channel adapters."""


class RouteConflict(ValueError):
    pass


def route_sources(route: dict) -> list[dict]:
    return route.get("sources") or [{key: route[key] for key in ("channel_id", "conversation_kind", "conversation_id")}]


def source_matches(source: dict, channel: str, kind: str, conversation: str) -> bool:
    return (
        source["channel_id"] in {"*", channel}
        and source["conversation_kind"] in {"*", kind}
        and source["conversation_id"] in {"*", conversation}
    )


def specificity(source: dict) -> int:
    return (
        4 * (source["conversation_id"] != "*")
        + 2 * (source["channel_id"] != "*")
        + (source["conversation_kind"] != "*")
    )


def select_route(routes: list[dict], channel: str, kind: str, conversation: str) -> dict | None:
    candidates = [
        (specificity(source), route)
        for route in routes
        if not route.get("parent_route_id")
        for source in route_sources(route)
        if source_matches(source, channel, kind, conversation)
    ]
    # Disabled or sender-restricted rules still take precedence: never fall back
    # to a broader permission when the selected rule denies a message.
    return max(candidates, key=lambda item: item[0])[1] if candidates else None


def check_source_conflicts(value: dict, routes: list[dict]) -> None:
    for route in routes:
        if route.get("parent_route_id") or route["id"] == value.get("id"):
            continue
        for left in route_sources(value):
            for right in route_sources(route):
                if (
                    (
                        left["conversation_kind"] == right["conversation_kind"]
                        or "*" in {left["conversation_kind"], right["conversation_kind"]}
                    )
                    and (left["channel_id"] == right["channel_id"] or "*" in {left["channel_id"], right["channel_id"]})
                    and (
                        left["conversation_id"] == right["conversation_id"]
                        or "*" in {left["conversation_id"], right["conversation_id"]}
                    )
                    and specificity(left) == specificity(right)
                ):
                    raise RouteConflict("An equally specific source already belongs to another route")
