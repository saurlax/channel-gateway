"""Single-process SQLite persistence with short, serialized storage operations."""

import asyncio
import hashlib
import json
import secrets
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import aiosqlite

SCHEMA = """
CREATE TABLE IF NOT EXISTS connections (id TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS routes (
 id TEXT PRIMARY KEY, channel_id TEXT NOT NULL, kind TEXT NOT NULL, conversation_id TEXT NOT NULL,
 value TEXT NOT NULL, token_hash TEXT NOT NULL, thread_id TEXT,
 UNIQUE(channel_id, kind, conversation_id)
);
CREATE TABLE IF NOT EXISTS events (
 id TEXT PRIMARY KEY, route_id TEXT NOT NULL, message TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'queued',
 target TEXT, body TEXT, thread_id TEXT, entry_id TEXT, run_id TEXT,
 attempts INTEGER NOT NULL DEFAULT 0, error TEXT, created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE TABLE IF NOT EXISTS replies (
 id TEXT PRIMARY KEY, route_id TEXT NOT NULL, event_id TEXT UNIQUE, text TEXT NOT NULL,
 state TEXT NOT NULL DEFAULT 'draft', message_id TEXT, error TEXT,
 created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE INDEX IF NOT EXISTS event_route ON events(route_id, created_at);
CREATE UNIQUE INDEX IF NOT EXISTS route_mcp_connection ON routes(
 json_extract(value, '$.host_id'), json_extract(value, '$.mcp_connection_id')
) WHERE json_extract(value, '$.mcp_connection_id') IS NOT NULL;
"""


def new_id(kind: str) -> str:
    return kind + "_" + secrets.token_hex(8)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class Store:
    def __init__(self, path: Path):
        self.path = path
        self.lock = asyncio.Lock()
        self.db: aiosqlite.Connection | None = None

    async def open(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self.path.parent.stat().st_mode & 0o077:
            raise ValueError("The state directory must be private (permissions 0700)")
        self.path.touch(mode=0o600, exist_ok=True)
        self.path.chmod(0o600)
        self.db = await aiosqlite.connect(self.path, isolation_level=None)
        self.db.row_factory = aiosqlite.Row
        await self.db.execute("PRAGMA journal_mode=WAL")
        await self.db.executescript(SCHEMA)
        # A request interrupted during delivery might already have taken effect.
        await self.db.execute(
            "UPDATE replies SET state='unknown', error='Delivery interrupted; verify in the channel' WHERE state='sending'"
        )

    async def close(self) -> None:
        if self.db:
            await self.db.close()

    @asynccontextmanager
    async def transaction(self):
        async with self.lock:
            assert self.db is not None
            await self.db.execute("BEGIN IMMEDIATE")
            try:
                yield self.db
                await self.db.commit()
            except BaseException:
                await self.db.rollback()
                raise

    async def rows(self, sql: str, params: tuple = ()) -> list[dict[str, Any]]:
        async with self.lock:
            assert self.db is not None
            async with self.db.execute(sql, params) as cursor:
                return [dict(row) for row in await cursor.fetchall()]

    async def connections(self) -> list[dict]:
        return [json.loads(row["value"]) for row in await self.rows("SELECT value FROM connections ORDER BY id")]

    async def connection(self, identifier: str) -> dict | None:
        rows = await self.rows("SELECT value FROM connections WHERE id=?", (identifier,))
        return json.loads(rows[0]["value"]) if rows else None

    async def add_connection(self, value: dict) -> dict:
        value = {**value, "id": new_id("conn")}
        async with self.transaction() as db:
            await db.execute("INSERT INTO connections VALUES (?,?)", (value["id"], json.dumps(value)))
        return value

    @staticmethod
    def route_view(row: dict) -> dict:
        return {**json.loads(row["value"]), "thread_id": row["thread_id"]}

    async def routes(self) -> list[dict]:
        return [self.route_view(row) for row in await self.rows("SELECT * FROM routes ORDER BY id")]

    async def route(self, identifier: str) -> dict | None:
        rows = await self.rows("SELECT * FROM routes WHERE id=?", (identifier,))
        return self.route_view(rows[0]) if rows else None

    async def route_for_token(self, token: str) -> dict | None:
        rows = await self.rows("SELECT * FROM routes WHERE token_hash=?", (token_hash(token),))
        return self.route_view(rows[0]) if rows else None

    async def add_route(self, value: dict) -> tuple[dict, str]:
        value = {**value, "id": new_id("route")}
        token = secrets.token_urlsafe(32)
        async with self.transaction() as db:
            await db.execute(
                "INSERT INTO routes (id,channel_id,kind,conversation_id,value,token_hash) VALUES (?,?,?,?,?,?)",
                (
                    value["id"],
                    value["channel_id"],
                    value["conversation_kind"],
                    value["conversation_id"],
                    json.dumps(value),
                    token_hash(token),
                ),
            )
        return {**value, "thread_id": None}, token

    async def update_route(self, identifier: str, value: dict, expected: dict) -> bool:
        expected = {key: item for key, item in expected.items() if key != "thread_id"}
        async with self.transaction() as db:
            cursor = await db.execute(
                "UPDATE routes SET value=? WHERE id=? AND value=?",
                (json.dumps(value), identifier, json.dumps(expected)),
            )
            return cursor.rowcount == 1

    async def update_connection(self, identifier: str, value: dict, expected: dict) -> bool:
        async with self.transaction() as db:
            cursor = await db.execute(
                "UPDATE connections SET value=? WHERE id=? AND value=?",
                (json.dumps(value), identifier, json.dumps(expected)),
            )
            return cursor.rowcount == 1

    async def rotate_token(self, identifier: str) -> str:
        token = secrets.token_urlsafe(32)
        async with self.transaction() as db:
            await db.execute("UPDATE routes SET token_hash=? WHERE id=?", (token_hash(token), identifier))
        return token

    async def ingest(self, identifier: str, route_id: str, message: dict) -> bool:
        async with self.transaction() as db:
            cursor = await db.execute(
                "INSERT OR IGNORE INTO events(id,route_id,message) VALUES (?,?,?)",
                (identifier, route_id, json.dumps(message)),
            )
            return cursor.rowcount == 1

    async def event(self, identifier: str) -> dict | None:
        rows = await self.rows("SELECT * FROM events WHERE id=?", (identifier,))
        if not rows:
            return None
        rows[0]["message"] = json.loads(rows[0]["message"])
        return rows[0]

    async def events(self, *, route_id: str | None = None, limit: int = 100) -> list[dict]:
        where = " WHERE route_id=?" if route_id else ""
        params = (route_id, limit) if route_id else (limit,)
        rows = await self.rows("SELECT * FROM events" + where + " ORDER BY rowid DESC LIMIT ?", params)
        for row in rows:
            row["message"] = json.loads(row["message"])
            row.pop("body")
            row.pop("target")
        return rows

    async def pending(self) -> list[dict]:
        # Serializing each conversation keeps run output attributable to one input.
        return await self.rows("""SELECT e.* FROM events e WHERE e.state IN ('queued','submitted') AND NOT EXISTS (
            SELECT 1 FROM events older WHERE older.route_id=e.route_id AND older.state IN ('queued','submitted')
            AND older.rowid<e.rowid) ORDER BY e.rowid LIMIT 100""")

    async def update_event(self, identifier: str, **fields) -> None:
        allowed = {"state", "target", "body", "thread_id", "entry_id", "run_id", "attempts", "error"}
        if not fields or not fields.keys() <= allowed:
            raise ValueError("Invalid event update")
        async with self.transaction() as db:
            await db.execute(
                "UPDATE events SET " + ",".join(key + "=?" for key in fields) + " WHERE id=?",
                (*fields.values(), identifier),
            )

    async def submitted(self, event_id: str, route_id: str, receipt: dict) -> None:
        thread_id = receipt["thread"]["id"]
        entry_id = receipt["entry"]["id"]
        run_id = (receipt.get("run") or {}).get("id")
        async with self.transaction() as db:
            await db.execute("UPDATE routes SET thread_id=COALESCE(thread_id,?) WHERE id=?", (thread_id, route_id))
            await db.execute(
                "UPDATE events SET state='submitted',thread_id=?,entry_id=?,run_id=?,attempts=0,error=NULL WHERE id=?",
                (thread_id, entry_id, run_id, event_id),
            )

    async def draft(self, route: dict, text: str, *, event_id: str | None = None) -> dict:
        row = {"id": new_id("reply"), "route_id": route["id"], "event_id": event_id, "text": text, "state": "draft"}
        if route["auto_reply"] and route["conversation_kind"] == "private":
            row["state"] = "approved"
        async with self.transaction() as db:
            await db.execute(
                "INSERT OR IGNORE INTO replies(id,route_id,event_id,text,state) VALUES (?,?,?,?,?)",
                tuple(row[key] for key in ["id", "route_id", "event_id", "text", "state"]),
            )
            if event_id:
                await db.execute("UPDATE events SET state='completed',error=NULL WHERE id=?", (event_id,))
        return row

    async def replies(self) -> list[dict]:
        return await self.rows("SELECT * FROM replies ORDER BY rowid DESC LIMIT 100")

    async def decide(self, identifier: str, *, approve: bool, text: str | None = None) -> bool:
        async with self.transaction() as db:
            cursor = await db.execute(
                "UPDATE replies SET state=?,text=COALESCE(?,text) WHERE id=? AND state='draft'",
                ("approved" if approve else "rejected", text, identifier),
            )
            return cursor.rowcount == 1

    async def claim_reply(self, identifier: str) -> bool:
        async with self.transaction() as db:
            cursor = await db.execute(
                "UPDATE replies SET state='sending' WHERE id=? AND state='approved'", (identifier,)
            )
            return cursor.rowcount == 1

    async def finish_reply(
        self, identifier: str, state: str, *, error: str | None = None, message_id: str | None = None
    ) -> None:
        async with self.transaction() as db:
            await db.execute(
                "UPDATE replies SET state=?,error=?,message_id=? WHERE id=?", (state, error, message_id, identifier)
            )
