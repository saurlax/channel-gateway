from pathlib import Path

from channel_gateway_server.app import create_app
from fastapi.testclient import TestClient


def test_headless_service_exposes_health_status_and_openapi(tmp_path: Path) -> None:
    with TestClient(create_app(static_dir=tmp_path)) as client:
        assert client.get("/healthz").json() == {"status": "ok"}
        status = client.get("/api/v1/status").json()
        assert status["stage"] == "foundation"
        assert {item["key"] for item in status["integrations"]} == {"onebots", "a13n", "mcp"}
        assert all(item["state"] == "planned" for item in status["integrations"])
        assert client.get("/api/openapi.json").status_code == 200
        assert client.get("/").status_code == 404


def test_console_does_not_swallow_unknown_api_routes(tmp_path: Path) -> None:
    (tmp_path / "index.html").write_text("<html>Channel Gateway</html>")
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "app.js").write_text("console.log('Channel Gateway');")
    with TestClient(create_app(static_dir=tmp_path)) as client:
        assert client.get("/").text == "<html>Channel Gateway</html>"
        assert client.get("/assets/app.js").status_code == 200
        missing = client.get("/api/v1/missing")
        assert missing.status_code == 404
        assert missing.headers["content-type"] == "application/json"
