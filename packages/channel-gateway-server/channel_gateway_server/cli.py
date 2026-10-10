"""Process startup and logging belong to the executable boundary."""

import click
import uvicorn


@click.group()
@click.version_option(package_name="channel-gateway-server")
def main() -> None:
    """Connect agents to communication channels."""


@main.command()
@click.option("--host", default="127.0.0.1", show_default=True, help="Address to bind.")
@click.option("--port", default=8310, type=click.IntRange(1, 65535), show_default=True)
@click.option("--reload", is_flag=True, help="Reload Python code during development.")
def serve(host: str, port: int, reload: bool) -> None:
    """Run the HTTP service and serve the console if its assets are present."""
    uvicorn.run("channel_gateway_server.app:create_app", factory=True, host=host, port=port, reload=reload)
