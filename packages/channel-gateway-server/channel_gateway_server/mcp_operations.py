"""Validated internal operations, independent of the public MCP tool inventory."""

from collections.abc import Callable

from mcp.server.fastmcp.tools import Tool


class Operations:
    def __init__(self):
        self.handlers: dict[str, Tool] = {}

    def register(self, function: Callable):
        self.handlers[function.__name__] = Tool.from_function(function, structured_output=False)
        return function

    def schema(self, name: str) -> dict:
        schema = self.handlers[name].parameters
        return {
            **{key: value for key, value in schema.items() if key != "title"},
            "properties": {key: value for key, value in schema["properties"].items() if key != "route_id"},
            "additionalProperties": False,
        }

    async def call(self, name: str, arguments: dict | None, route_id: str | None):
        arguments = arguments or {}
        if arguments.keys() - self.schema(name)["properties"].keys():
            raise ValueError("Unsupported operation arguments; use discover(resource='operations')")
        return await self.handlers[name].run({**arguments, "route_id": route_id})
