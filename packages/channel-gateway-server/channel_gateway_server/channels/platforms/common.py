"""Bounded async platform I/O and explicit capability declarations.

No client retries writes. A timeout after publication is an unknown outcome in
our durable ledger, even when a provider offers an idempotency parameter.
"""

import asyncio
import json
from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx

from ...adapters import AdapterError
from ...media import IMAGE_TYPES, MAX_IMAGE_BYTES, MAX_MEDIA_BYTES, image_type
from ..common import OperationRejected

PLATFORMS = frozenset({"slack", "lark", "discord"})
CHANNEL_KINDS = frozenset({"onebots", "qq", "weixin", *PLATFORMS})
CONVERSATION_KINDS = frozenset({"private", "group", "guild", "guild_dm", "channel", "thread"})
# Every field that can change account identity or authentication invalidates ingress.
CONNECTION_KEYS = (
    "kind",
    "transport",
    "api_url",
    "token",
    "webhook_secret",
    "app_id",
    "app_secret",
    "self_id",
    "qq_intents",
    "discord_intents",
    "encrypt_key",
)


def same_connection(current: dict | None, expected: dict) -> bool:
    # QQ discovers self_id at runtime; its immutable principal is AppID. Other
    # adapters explicitly bind self_id as part of the configured installation.
    return current is not None and all(
        current.get(key) == expected.get(key)
        for key in CONNECTION_KEYS
        if not (expected["kind"] == "qq" and key == "self_id")
    )


@dataclass(frozen=True)
class PlatformProfile:
    conversations: tuple[str, ...]
    max_text: int = 16000
    typing: bool = False
    max_media: int = MAX_MEDIA_BYTES

    def capabilities(self) -> dict:
        return {
            "receive": True,
            "send": True,
            "conversations": list(self.conversations),
            "content": ["text", "image", "audio", "video", "file", "quote"],
            "image_read_types": sorted(IMAGE_TYPES),
            "image_send_types": sorted(IMAGE_TYPES),
            "message_reactions": "native",
            "resource_operations": False,
            "platform_history": True,
            "platform_search": False,
            "max_text_characters": self.max_text,
            "max_media_bytes": self.max_media,
            "typing": self.typing,
            "features": {
                name: {"support": support, "availability": "unknown"}
                for name, support in {
                    "messages": "native",
                    "history": "native",
                    "media": "native",
                    "reaction": "native",
                    "recall": "native",
                    "typing": "native" if self.typing else "unsupported",
                    "search": "unsupported",
                    "stream": "unsupported",
                    "thread_inheritance": "unsupported",
                }.items()
            },
        }


PROFILES = {
    "slack": PlatformProfile(("private", "channel", "thread")),
    "lark": PlatformProfile(("private", "group", "thread")),
    "discord": PlatformProfile(
        ("private", "channel", "thread"), max_text=2000, typing=True, max_media=10 * 1024 * 1024
    ),
}


def remote_url(value: str, hosts: set[str], *, prefix: str = "/") -> str:
    """Credential forwarding destinations are exact provider hosts, no redirects."""
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or parsed.hostname not in hosts
        or parsed.port not in {None, 443}
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
        or not parsed.path.startswith(prefix)
        or "\\" in value
        or len(value) > 4096
        or any(ord(c) < 33 for c in value)
    ):
        raise ValueError("Unsupported platform media destination")
    return value


async def response_data(response: httpx.Response, *, limit: int = 4 * 1024 * 1024) -> bytes:
    if response.headers.get("content-encoding", "identity").lower() != "identity":
        raise AdapterError("Compressed platform responses are not supported")
    length = response.headers.get("content-length")
    if length is not None and (not length.isdecimal() or int(length) > limit):
        raise AdapterError("Platform response exceeds the size limit")
    content = bytearray()
    async for chunk in response.aiter_bytes(chunk_size=65536):
        if len(content) + len(chunk) > limit:
            raise AdapterError("Platform response exceeds the size limit")
        content.extend(chunk)
    return bytes(content)


class PlatformClient:
    base: str
    provider: str

    def __init__(self, connection: dict, http: httpx.AsyncClient, media_http: httpx.AsyncClient):
        self.connection, self.http, self.media_http = connection, http, media_http
        self.identity_verified = False
        self.identity_lock = asyncio.Lock()

    async def headers(self) -> dict:
        return {"Authorization": "Bearer " + self.connection["token"]}

    async def request(self, method: str, path: str, *, body=None, params=None, files=None, data=None):
        async with asyncio.timeout(30):
            async with self.http.stream(
                method,
                self.base + path,
                json=body,
                params=params,
                files=files,
                data=data,
                headers={**await self.headers(), "Accept-Encoding": "identity"},
                follow_redirects=False,
                timeout=20,
            ) as response:
                if 400 <= response.status_code < 500 and response.status_code != 408:
                    raise OperationRejected(f"{self.provider} rejected the operation (HTTP {response.status_code})")
                if not response.is_success:
                    raise AdapterError(f"{self.provider} operation was not confirmed")
                raw = await response_data(response)
                value = json.loads(raw) if raw else {}
        if not isinstance(value, (dict, list)):
            raise AdapterError("Platform returned an invalid receipt")
        return value

    async def download(self, url: str, *, headers: dict | None = None, image: bool = False) -> tuple[bytes, str]:
        async with asyncio.timeout(30):
            # Never inherit API/client cookies or ambient auth on a media request.
            request = self.media_http.build_request("GET", url, headers={"Accept-Encoding": "identity"})
            request.headers.pop("authorization", None)
            request.headers.pop("cookie", None)
            request.headers.update(headers or {})
            response = await self.media_http.send(request, auth=None, follow_redirects=False, stream=True)
            try:
                if response.status_code != 200:
                    raise ValueError("Platform attachment is unavailable or expired")
                data = await response_data(response, limit=MAX_IMAGE_BYTES if image else MAX_MEDIA_BYTES)
                mime = response.headers.get("content-type", "application/octet-stream").partition(";")[0].lower()
            finally:
                await response.aclose()
        if image:
            detected = image_type(data)
            if mime in IMAGE_TYPES and mime != detected:
                raise ValueError("Image media type does not match its signature")
            return data, detected
        return data, mime
