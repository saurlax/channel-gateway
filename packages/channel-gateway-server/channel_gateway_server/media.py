"""Bounded channel image references and credential-free CDN downloads."""

import asyncio
import base64
import binascii
import ipaddress
from urllib.parse import urlsplit, urlunsplit

import httpx
from channel_gateway_core import ImageAttachment

MAX_IMAGE_BYTES = 4 * 1024 * 1024
MAX_IMAGE_BASE64 = 4 * ((MAX_IMAGE_BYTES + 2) // 3)
IMAGE_TYPES = {"image/png", "image/jpeg", "image/gif", "image/webp"}
MAX_MEDIA_BYTES = 16 * 1024 * 1024


def public_media_url(value: str) -> str:
    parsed = urlsplit(value)
    host = (parsed.hostname or "").lower()
    if (
        parsed.scheme != "https"
        or not host
        or parsed.username
        or parsed.password
        or parsed.fragment
        or parsed.port not in {None, 443}
        or len(value) > 4096
        or "\\" in value
        or any(ord(c) < 33 for c in value)
    ):
        raise ValueError("Use a public HTTPS media URL without credentials")
    if host in {"localhost", "localhost.localdomain"} or host.endswith((".local", ".internal", ".localhost")):
        raise ValueError("Local media URLs are not supported")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        if "." not in host:
            raise ValueError("Use a public media hostname") from None
    else:
        if not address.is_global:
            raise ValueError("Local media URLs are not supported")
    return value


def decode_media(encoded: str) -> bytes:
    if not 0 < len(encoded) <= 4 * ((MAX_MEDIA_BYTES + 2) // 3):
        raise ValueError("Inline media must be at most 16 MiB")
    try:
        data = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error):
        raise ValueError("Use strict base64 media data") from None
    if not 0 < len(data) <= MAX_MEDIA_BYTES:
        raise ValueError("Inline media must be at most 16 MiB")
    return data


def image_url(value: object) -> str | None:
    """Accept only native QQ media CDNs, never arbitrary URLs or local paths."""
    if not isinstance(value, str) or not value or len(value) > 4096:
        return None
    try:
        parsed = urlsplit("https:" + value if value.startswith("//") else value)
        host = (parsed.hostname or "").lower()
        if (
            parsed.scheme not in {"http", "https"}
            or not (
                host == "qpic.cn"
                or host.endswith(".qpic.cn")
                or host in {"multimedia.nt.qq.com", "multimedia.nt.qq.com.cn"}
                or (".cos." in host and host.endswith(".myqcloud.com"))
            )
            or parsed.username is not None
            or parsed.password is not None
            or parsed.fragment
            or parsed.port not in {None, 443}
            or any(ord(char) < 33 for char in value)
            or "\\" in value
        ):
            return None
    except ValueError:
        return None
    return urlunsplit(("https", parsed.netloc, parsed.path, parsed.query, ""))


def image_attachments(urls: list[object]) -> tuple[ImageAttachment, ...]:
    images = tuple(ImageAttachment(url=url) for value in urls if (url := image_url(value)))
    if len(images) > 4:
        raise ValueError("Message exceeds the image count limit")
    return images


def image_type(data: bytes) -> str:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "image/webp"
    raise ValueError("Unsupported image signature")


def decode_image(encoded: str, media_type: str) -> bytes:
    if media_type not in IMAGE_TYPES or not 0 < len(encoded) <= MAX_IMAGE_BASE64:
        raise ValueError("Use a supported image of at most 4 MiB")
    try:
        data = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error):
        raise ValueError("Use strict base64 image data") from None
    if not 0 < len(data) <= MAX_IMAGE_BYTES or image_type(data) != media_type:
        raise ValueError("Image size or media type is invalid")
    return data


def public_message(message: dict) -> dict:
    """Expose image handles without delegating signed download URLs."""
    return {
        **message,
        "images": [{"index": index} for index, _ in enumerate(message.get("images", []))],
        "attachments": [
            {"index": index, **{key: item[key] for key in ("kind", "media_type", "filename")}}
            for index, item in enumerate(message.get("attachments", []))
        ],
    }


class MediaReader:
    def __init__(self, client: httpx.AsyncClient):
        # This client must be dedicated to unauthenticated media, not provider APIs.
        self.client = client

    async def read(self, url: str) -> tuple[bytes, str]:
        data, media_type = await self.read_blob(url, MAX_IMAGE_BYTES, image_only=True)
        detected_type = image_type(data)
        if media_type in IMAGE_TYPES and detected_type != media_type:
            raise ValueError("Image media type does not match its signature")
        return data, detected_type

    async def read_blob(
        self, url: str, max_bytes: int = 16 * 1024 * 1024, *, image_only: bool = False
    ) -> tuple[bytes, str]:
        safe_url = image_url(url)
        if safe_url is None:
            raise ValueError("Image download host is not supported")
        try:
            async with asyncio.timeout(15):
                request = self.client.build_request("GET", safe_url, headers={"Accept-Encoding": "identity"})
                request.headers.pop("cookie", None)
                request.headers.pop("authorization", None)
                response = await self.client.send(request, auth=None, follow_redirects=False, stream=True)
                try:
                    if response.status_code != 200:
                        raise ValueError("Image is unavailable or expired")
                    if response.headers.get("content-encoding", "identity").lower() != "identity":
                        raise ValueError("Compressed image responses are not supported")
                    media_type = response.headers.get("content-type", "").partition(";")[0].strip().lower()
                    if image_only and media_type not in IMAGE_TYPES | {
                        "",
                        "application/octet-stream",
                        "binary/octet-stream",
                    }:
                        raise ValueError("Unsupported image media type")
                    length = response.headers.get("content-length")
                    if length is not None and (not length.isdecimal() or int(length) > max_bytes):
                        raise ValueError("Image exceeds the size limit")
                    data = bytearray()
                    async for chunk in response.aiter_bytes(chunk_size=65536):
                        if len(data) + len(chunk) > max_bytes:
                            raise ValueError("Image exceeds the size limit")
                        data.extend(chunk)
                    result = bytes(data)
                    return result, media_type or "application/octet-stream"
                finally:
                    await response.aclose()
        except (httpx.HTTPError, TimeoutError):
            raise ValueError("Image download failed") from None
