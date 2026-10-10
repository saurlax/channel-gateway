"""HTTP application factory; importing this module starts no background work."""

from importlib.metadata import version
from pathlib import Path
from typing import Literal

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel


class IntegrationStatus(BaseModel):
    key: str
    name: str
    description: str
    state: Literal["planned"] = "planned"


class Status(BaseModel):
    name: str = "Channel Gateway"
    version: str
    stage: Literal["foundation"] = "foundation"
    integrations: list[IntegrationStatus]


def create_app(*, static_dir: Path | None = None) -> FastAPI:
    """Create a headless service or serve an already-built console."""
    app = FastAPI(
        title="Channel Gateway",
        version=version("channel-gateway-server"),
        docs_url="/api/docs",
        redoc_url=None,
        openapi_url="/api/openapi.json",
    )

    @app.get("/healthz", include_in_schema=False)
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/v1/status", response_model=Status)
    async def status() -> Status:
        return Status(
            version=app.version,
            integrations=[
                IntegrationStatus(
                    key="onebots", name="OneBots", description="Receive and send messages through a channel adapter."
                ),
                IntegrationStatus(
                    key="a13n", name="a13n", description="Route channel events to Agent Foundation Thread inboxes."
                ),
                IntegrationStatus(
                    key="mcp",
                    name="MCP",
                    description="Let agents read conversations and send messages across channels.",
                ),
            ],
        )

    assets = static_dir if static_dir is not None else Path(__file__).parent / "static"
    index = assets / "index.html"
    if index.is_file():
        asset_dir = assets / "assets"
        if asset_dir.is_dir():
            app.mount("/assets", StaticFiles(directory=asset_dir), name="assets")

        @app.get("/", include_in_schema=False)
        async def console() -> FileResponse:
            return FileResponse(index)

    return app
