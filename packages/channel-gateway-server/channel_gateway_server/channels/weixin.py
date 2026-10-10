"""Weixin iLink HTTP JSON protocol and QR-code pairing."""

import base64
import secrets
from datetime import UTC, datetime
from io import BytesIO

import httpx
import qrcode
import qrcode.image.svg
from channel_gateway_core import Message

from ..adapters import AdapterError
from ..models import weixin_url
from .common import identifier, response_object, text_message

WEIXIN_BASE = "https://ilinkai.weixin.qq.com"
# Wire metadata for the documented upstream 2.4.8 client baseline.
CLIENT_VERSION = "2.4.8"


def weixin_message(raw: dict, connection: dict) -> Message | None:
    if raw.get("message_type") != 1 or raw.get("group_id"):
        return None
    if identifier(raw["to_user_id"]) != connection["self_id"]:
        return None
    sender = identifier(raw["from_user_id"])
    text = "".join(item["text_item"]["text"] for item in raw.get("item_list", []) if item.get("type") == 1)
    return text_message(
        connection,
        "private",
        sender,
        sender,
        raw["message_id"],
        text,
        datetime.fromtimestamp(raw["create_time_ms"] / 1000, UTC),
    )


def qr_image(content: str) -> str:
    image = qrcode.make(content, image_factory=qrcode.image.svg.SvgPathImage)
    buffer = BytesIO()
    image.save(buffer)
    return "data:image/svg+xml;base64," + base64.b64encode(buffer.getvalue()).decode()


class WeixinClient:
    def __init__(self, connection: dict, http: httpx.AsyncClient):
        self.connection = connection
        self.http = http

    @staticmethod
    def headers(token: str | None = None, *, polling_qr: bool = False) -> dict:
        headers = {"iLink-App-Id": "bot", "iLink-App-ClientVersion": str((2 << 16) | (4 << 8) | 8)}
        if not polling_qr:
            headers.update(
                {
                    "AuthorizationType": "ilink_bot_token",
                    "X-WECHAT-UIN": base64.b64encode(str(secrets.randbits(32)).encode()).decode(),
                }
            )
        if token:
            headers["Authorization"] = "Bearer " + token
        return headers

    async def request(self, action: str, body: dict, *, timeout: float = 15) -> dict:
        value = response_object(
            await self.http.post(
                self.connection["api_url"] + "/ilink/bot/" + action,
                headers=self.headers(self.connection["token"]),
                json={**body, "base_info": {"channel_version": CLIENT_VERSION, "bot_agent": "Channel Gateway/0.0.0"}},
                timeout=timeout,
            ),
            "Weixin",
        )
        if value.get("ret", 0) != 0 or value.get("errcode", 0) != 0:
            if -14 in {value.get("ret"), value.get("errcode")}:
                raise LoginRequired("Weixin session expired; scan a new login QR code")
            raise AdapterError("Weixin rejected the operation")
        return value

    async def updates(self, cursor: str) -> dict:
        return await self.request("getupdates", {"get_updates_buf": cursor}, timeout=45)

    async def send(self, conversation: str, text: str, context_token: str, reply_id: str) -> str:
        value = await self.request(
            "sendmessage",
            {
                "msg": {
                    "from_user_id": "",
                    "to_user_id": conversation,
                    "client_id": reply_id,
                    "message_type": 2,
                    "message_state": 2,
                    "context_token": context_token,
                    "item_list": [{"type": 1, "text_item": {"text": text}}],
                }
            },
        )
        if value.get("ret") != 0:
            raise AdapterError("Weixin omitted the send acknowledgement")
        # iLink acknowledges acceptance without returning a platform message ID.
        return reply_id

    async def start_login(self) -> dict:
        value = response_object(
            await self.http.post(
                WEIXIN_BASE + "/ilink/bot/get_bot_qrcode",
                params={"bot_type": "3"},
                headers=self.headers(),
                json={"local_token_list": []},
            ),
            "Weixin",
        )
        if not isinstance(value.get("qrcode"), str) or not value["qrcode"] or len(value["qrcode"]) > 4096:
            raise AdapterError("Weixin returned an invalid login QR code")
        return value

    async def poll_login(self, login: dict, verify_code: str | None) -> dict:
        params = {"qrcode": login["qrcode"]}
        if verify_code:
            params["verify_code"] = verify_code
        value = response_object(
            await self.http.get(
                login["host"] + "/ilink/bot/get_qrcode_status",
                params=params,
                headers=self.headers(polling_qr=True),
                timeout=40,
            ),
            "Weixin",
        )
        if value.get("status") == "scaned_but_redirect" and value.get("redirect_host"):
            host = value["redirect_host"]
            login["host"] = weixin_url(host if host.startswith("https://") else "https://" + host)
        return value


class LoginRequired(AdapterError):
    """The platform requires explicit reauthentication."""
