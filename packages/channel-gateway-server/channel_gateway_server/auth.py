"""Single administrator login and process-local, revocable management sessions."""

import asyncio
import hashlib
import hmac
import secrets
from collections import deque
from dataclasses import dataclass
from time import monotonic

from fastapi import HTTPException, Request, Response

from .models import AdministratorLogin, Settings
from .storage import token_hash

COOKIE_NAME = "channel_gateway_session"
COOKIE_PATH = "/api/v1"
SESSION_SECONDS = 12 * 60 * 60


@dataclass(frozen=True)
class Session:
    csrf_token: str
    expires_at: float


class AdministratorAuth:
    def __init__(self, settings: Settings):
        self.username = settings.admin_username
        self.configured = bool(settings.admin_username and settings.admin_password)
        if bool(settings.admin_username) != bool(settings.admin_password):
            raise ValueError("Set both CGW_ADMIN_USERNAME and CGW_ADMIN_PASSWORD")
        if self.configured and (
            not 1 <= len(self.username or "") <= 128 or not 8 <= len(settings.admin_password or "") <= 4096
        ):
            raise ValueError("Administrator username must be 1-128 characters and password 8-4096 characters")
        self.salt = secrets.token_bytes(16)
        self.password_hash = self.hash_password(settings.admin_password) if self.configured else None
        self.sessions: dict[str, Session] = {}
        self.attempts: dict[str, deque[float]] = {}

    def hash_password(self, password: str | None) -> bytes:
        return hashlib.scrypt((password or "").encode(), salt=self.salt, n=16384, r=8, p=1)

    def check_request(self, request: Request) -> None:
        if not self.configured:
            raise HTTPException(503, "Set CGW_ADMIN_USERNAME and CGW_ADMIN_PASSWORD to enable management")
        origin = request.headers.get("origin")
        if origin and origin.rstrip("/") != str(request.base_url).rstrip("/"):
            raise HTTPException(403, "Cross-origin management requests are not accepted")

    def prune(self) -> None:
        now = monotonic()
        self.sessions = {key: value for key, value in self.sessions.items() if value.expires_at > now}
        self.attempts = {key: value for key, value in self.attempts.items() if value and value[-1] > now - 60}

    def require(self, request: Request) -> Session:
        self.check_request(request)
        self.prune()
        credential = request.cookies.get(COOKIE_NAME, "")
        session = self.sessions.get(token_hash(credential)) if credential else None
        if session is None:
            raise HTTPException(401, "Administrator sign-in required")
        if request.method not in {"GET", "HEAD", "OPTIONS"} and not hmac.compare_digest(
            request.headers.get("x-csrf-token", "").encode(), session.csrf_token.encode()
        ):
            raise HTTPException(403, "Refresh the page before making changes")
        return session

    def view(self, session: Session) -> dict:
        return {"username": self.username, "csrf_token": session.csrf_token}

    async def login(self, request: Request, response: Response, body: AdministratorLogin) -> dict:
        self.check_request(request)
        if request.headers.get("x-channel-gateway-request") != "1":
            raise HTTPException(403, "Use the management login API")
        self.prune()
        peer = request.client.host if request.client else "unknown"
        if peer not in self.attempts and len(self.attempts) >= 1024:
            raise HTTPException(429, "Too many sign-in attempts; try again in a minute", headers={"Retry-After": "60"})
        attempts = self.attempts.setdefault(peer, deque())
        now = monotonic()
        while attempts and attempts[0] <= now - 60:
            attempts.popleft()
        if len(attempts) >= 5:
            raise HTTPException(429, "Too many sign-in attempts; try again in a minute", headers={"Retry-After": "60"})
        attempts.append(now)
        digest = await asyncio.to_thread(self.hash_password, body.password.get_secret_value())
        assert self.password_hash is not None
        username_matches = hmac.compare_digest(body.username.encode(), (self.username or "").encode())
        password_matches = hmac.compare_digest(digest, self.password_hash)
        if not username_matches or not password_matches:
            raise HTTPException(401, "Incorrect username or password")
        # Replace the current browser session rather than retaining its authority.
        old = request.cookies.get(COOKIE_NAME)
        if old:
            self.sessions.pop(token_hash(old), None)
        if len(self.sessions) >= 256:
            self.sessions.pop(next(iter(self.sessions)))
        credential = secrets.token_urlsafe(32)
        session = Session(csrf_token=secrets.token_urlsafe(32), expires_at=monotonic() + SESSION_SECONDS)
        self.sessions[token_hash(credential)] = session
        response.set_cookie(
            COOKIE_NAME,
            credential,
            max_age=SESSION_SECONDS,
            path=COOKIE_PATH,
            httponly=True,
            secure=request.url.scheme == "https",
            samesite="strict",
        )
        return self.view(session)

    def logout(self, request: Request, response: Response) -> None:
        self.require(request)
        self.sessions.pop(token_hash(request.cookies[COOKIE_NAME]), None)
        response.delete_cookie(
            COOKIE_NAME, path=COOKIE_PATH, httponly=True, secure=request.url.scheme == "https", samesite="strict"
        )
