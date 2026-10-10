"""Prepare the mounted private state directory and drop container privileges."""

import os
from pathlib import Path

state = Path(os.environ.get("CG_DATA_DIR", "/data/state"))
state.mkdir(parents=True, exist_ok=True, mode=0o700)
if os.getuid() == 0:
    state.chmod(0o700)
    os.chown(state, 10001, 10001)
    os.setgroups([])
    os.setgid(10001)
    os.setuid(10001)
os.umask(0o077)
os.execv(
    "/src/.venv/bin/uvicorn",
    [
        "uvicorn",
        "channel_gateway_server.app:create_app",
        "--factory",
        "--host",
        "0.0.0.0",
        "--port",
        "8080",
        "--proxy-headers",
        "--forwarded-allow-ips",
        "*",
    ],
)
