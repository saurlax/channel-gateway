"""GitHub operations under the five existing MCP entry points."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from .channels.github import authorized_context


class InlineComment(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str = Field(min_length=1, max_length=1024)
    line: int = Field(ge=1)
    side: Literal["LEFT", "RIGHT"]
    body: str = Field(min_length=1, max_length=16000)


class CodeFile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str = Field(min_length=1, max_length=1024)
    content: str = Field(max_length=100000)


class GitHubMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: Literal["comment", "review", "reply"] = "comment"
    head_sha: str | None = Field(default=None, pattern=r"^[0-9a-f]{40}$")
    comments: list[InlineComment] = Field(default_factory=list, max_length=50)
    comment_id: int | None = Field(default=None, ge=1)


def install_github_operations(operations, store, bridge, authorized_route):
    async def scope(route_id, action):
        route = await authorized_route(route_id)
        connection = await store.connection(route["channel_id"])
        if not connection or connection["kind"] != "github" or not bridge.channels:
            raise ValueError("Choose a GitHub route")
        context = await authorized_context(store, connection, route, action)
        return route, connection, context

    @operations.register
    async def read_github_pull_request(route_id: str | None = None):
        route, connection, context = await scope(route_id, "read")
        value = await bridge.channels.github(connection).snapshot(context)
        if await scope(route_id, "read") != (route, connection, context):
            raise ValueError("GitHub read authority changed")
        return {"source": "platform", **value}

    @operations.register
    async def send_github_message(
        text: Annotated[str, Field(min_length=1, max_length=16000)], options: GitHubMessage, route_id: str | None = None
    ):
        action = "review" if options.mode == "review" else "comment"
        route, _connection, context = await scope(route_id, action)
        if options.mode == "review" and not options.head_sha:
            raise ValueError("Review requires the head_sha returned by the PR read")
        if options.mode != "review" and (options.head_sha or options.comments):
            raise ValueError("Only reviews accept a head SHA and inline comments")
        if (options.mode == "reply") != (options.comment_id is not None):
            raise ValueError("Review replies require comment_id")
        reply = await store.queue_reply(
            route,
            text,
            payload={"kind": "github", "context": context, "options": options.model_dump(exclude_none=True)},
        )
        bridge.wake.set()
        return {"reply_id": reply["id"], "state": reply["state"]}

    @operations.register
    async def create_github_pull_request(
        head: Annotated[str, Field(min_length=1, max_length=200)],
        base: Annotated[str, Field(min_length=1, max_length=200)],
        head_sha: Annotated[str, Field(pattern=r"^[0-9a-f]{40}$")],
        title: Annotated[str, Field(min_length=1, max_length=256)],
        body: Annotated[str, Field(max_length=16000)] = "",
        files: Annotated[list[CodeFile], Field(max_length=20)] | None = None,
        route_id: str | None = None,
    ):
        route, _connection, context = await scope(route_id, "create_pr")
        value = await store.queue_channel_action(
            route,
            "github.create_pr",
            {
                "head": head,
                "base": base,
                "head_sha": head_sha,
                "title": title,
                "body": body,
                "context": context,
                "files": [file.model_dump() for file in files or []],
            },
            "message.send",
        )
        bridge.wake.set()
        return {"action_id": value["action_id"], "state": value["state"]}
