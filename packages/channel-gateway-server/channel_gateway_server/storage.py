"""Single-process SQLite persistence with short, serialized storage operations."""

import asyncio
import hashlib
import json
import secrets
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from urllib.parse import quote, unquote

import aiosqlite

from .channels.platforms.common import CHANNEL_KINDS, CONVERSATION_KINDS, same_connection
from .media import public_message
from .models import route_allows_sender
from .routing import RouteConflict, check_source_conflicts, route_sources, select_route
from .thread_policy import thread_binding_key

SCHEMA = """
CREATE TABLE IF NOT EXISTS operation_effects (
 id INTEGER PRIMARY KEY AUTOINCREMENT, operation_id TEXT NOT NULL,
 stage TEXT NOT NULL, reference TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE INDEX IF NOT EXISTS operation_effect_parent ON operation_effects(operation_id);
CREATE TABLE IF NOT EXISTS connections (id TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS host_keys (host_id TEXT PRIMARY KEY, token_hash TEXT NOT NULL UNIQUE);
CREATE TABLE IF NOT EXISTS thread_bindings (
 binding_key TEXT PRIMARY KEY, route_id TEXT NOT NULL, thread_id TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS event_thread_bindings (
 event_id TEXT PRIMARY KEY, binding_key TEXT, route_id TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS routes (
 id TEXT PRIMARY KEY, channel_id TEXT NOT NULL, kind TEXT NOT NULL, conversation_id TEXT NOT NULL,
 value TEXT NOT NULL, token_hash TEXT NOT NULL, thread_id TEXT,
 UNIQUE(channel_id, kind, conversation_id)
);
CREATE TABLE IF NOT EXISTS archived_routes (
 id TEXT PRIMARY KEY, channel_id TEXT NOT NULL, kind TEXT NOT NULL, conversation_id TEXT NOT NULL,
 value TEXT NOT NULL, thread_id TEXT
);
CREATE TABLE IF NOT EXISTS events (
 id TEXT PRIMARY KEY, route_id TEXT NOT NULL, message TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'queued',
 target TEXT, body TEXT, thread_id TEXT, entry_id TEXT, run_id TEXT,
 attempts INTEGER NOT NULL DEFAULT 0, error TEXT, created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE TABLE IF NOT EXISTS replies (
 id TEXT PRIMARY KEY, route_id TEXT NOT NULL, event_id TEXT UNIQUE, text TEXT NOT NULL,
 state TEXT NOT NULL DEFAULT 'queued', message_id TEXT, error TEXT,
 created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE TABLE IF NOT EXISTS reply_payloads (
 reply_id TEXT PRIMARY KEY, payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS qq_send_attempts (
 id INTEGER PRIMARY KEY, account_id TEXT NOT NULL, conversation_key TEXT NOT NULL, created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS qq_send_budget ON qq_send_attempts(account_id,created_at);
CREATE TABLE IF NOT EXISTS channel_actions (
 id TEXT PRIMARY KEY, route_id TEXT NOT NULL, operation TEXT NOT NULL, arguments TEXT NOT NULL,
 required_grant TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'queued', result TEXT, error TEXT,
 created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE TABLE IF NOT EXISTS event_reply_contexts (event_id TEXT PRIMARY KEY, context TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS channel_notifications (
 id TEXT PRIMARY KEY, channel_id TEXT NOT NULL, event_type TEXT NOT NULL, scope_kind TEXT NOT NULL,
 scope_id TEXT NOT NULL, data TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE TABLE IF NOT EXISTS stream_sessions (
 id TEXT PRIMARY KEY, route_id TEXT NOT NULL, next_index INTEGER NOT NULL DEFAULT 0,
 sent_index INTEGER NOT NULL DEFAULT -1, provider_id TEXT, state TEXT NOT NULL DEFAULT 'open',
 source_message_id TEXT, sender_id TEXT, input_closed INTEGER NOT NULL DEFAULT 0, reply_context TEXT
);
CREATE TABLE IF NOT EXISTS observations (
 id TEXT PRIMARY KEY, message TEXT NOT NULL, state TEXT NOT NULL, error TEXT NOT NULL,
 created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE INDEX IF NOT EXISTS event_route ON events(route_id, created_at);
CREATE INDEX IF NOT EXISTS event_run ON events(thread_id, run_id);
CREATE INDEX IF NOT EXISTS observation_conversation ON observations(
 json_extract(message,'$.address.account_id'), json_extract(message,'$.address.conversation_id'), created_at, id
);
CREATE TABLE IF NOT EXISTS channel_events (
 id TEXT PRIMARY KEY, channel_id TEXT NOT NULL, transport TEXT NOT NULL, payload BLOB NOT NULL,
 event_type TEXT, state TEXT NOT NULL DEFAULT 'received', message_event_id TEXT, error TEXT,
 created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE INDEX IF NOT EXISTS channel_event_account ON channel_events(channel_id, created_at);
CREATE TABLE IF NOT EXISTS channel_state (
 connection_id TEXT NOT NULL, name TEXT NOT NULL, value TEXT NOT NULL,
 PRIMARY KEY(connection_id, name)
);
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
        async with self.db.execute("PRAGMA table_info(stream_sessions)") as cursor:
            stream_columns = {row["name"] for row in await cursor.fetchall()}
        if "reply_context" not in stream_columns:
            await self.db.execute("ALTER TABLE stream_sessions ADD COLUMN reply_context TEXT")
        # Retire approval settings without sending historical unreviewed content.
        async with self.transaction() as db:
            await db.execute("UPDATE routes SET value=json_remove(value,'$.auto_reply','$.access_mode')")
            await db.execute("UPDATE replies SET state='queued' WHERE state='approved'")
            await db.execute(
                "UPDATE replies SET state='blocked',error='Automatic output delivery retired' WHERE event_id IS NOT NULL AND state='queued'"
            )
            for table in ("routes", "archived_routes"):
                async with db.execute(f"SELECT id,value FROM {table}") as cursor:
                    stored = await cursor.fetchall()
                for row in stored:
                    value = json.loads(row[1])
                    previous = value.pop("reply_source", None)
                    # Preserve existing OneBot reaction authority without granting it to other routes.
                    if previous == "mcp":
                        grants = value.setdefault("action_grants", [])
                        if "message.react" not in grants and "message.react.onebot" not in grants:
                            grants.append("message.react.onebot")
                    if previous is not None:
                        await db.execute(f"UPDATE {table} SET value=? WHERE id=?", (json.dumps(value), row[0]))
            await db.execute(
                "UPDATE channel_actions SET required_grant='message.react.onebot' WHERE required_grant='legacy.react'"
            )
            await db.execute(
                "UPDATE replies SET state='blocked',error='Legacy draft retired; send a new message' WHERE state='draft'"
            )
        await self.db.execute(
            "UPDATE channel_actions SET state='unknown',error='Action interrupted; verify in QQ' WHERE state='sending'"
        )
        await self.db.execute(
            "UPDATE stream_sessions SET state='unknown' WHERE id IN (SELECT json_extract(arguments,'$.stream_handle') FROM channel_actions WHERE state='unknown' AND operation='stream_message')"
        )
        # Upgrade legacy default conversation routes without losing environment origins.
        # Explicit sender/fixed/message policies and frozen requests stay intact.
        async with self.transaction() as db:
            async with db.execute("SELECT id,value FROM routes") as cursor:
                legacy = await cursor.fetchall()
            for row in legacy:
                value = json.loads(row["value"])
                if "history_limit" in value:
                    continue
                value["history_limit"] = 30
                async with db.execute("SELECT value FROM connections WHERE id=?", (value["host_id"],)) as cursor:
                    host = await cursor.fetchone()
                if (
                    host
                    and json.loads(host[0])["kind"] == "a13n"
                    and value.get("thread_policy", "conversation") == "conversation"
                ):
                    value["thread_policy"] = "window"
                await db.execute("UPDATE routes SET value=? WHERE id=?", (json.dumps(value), row["id"]))
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

    async def prune_history_cache(self, connection_id: str) -> None:
        async with self.transaction() as db:
            await db.execute(
                "DELETE FROM channel_state WHERE connection_id=? AND name LIKE 'history_%' AND json_extract(value,'$.expires') < ?",
                (connection_id, time.time()),
            )
            await db.execute(
                "DELETE FROM channel_state WHERE rowid IN (SELECT rowid FROM channel_state WHERE connection_id=? AND name LIKE 'history_%' ORDER BY rowid DESC LIMIT -1 OFFSET 5000)",
                (connection_id,),
            )

    async def record_operation_effect(self, operation_id: str, stage: str, reference: str) -> None:
        async with self.transaction() as db:
            await db.execute(
                "INSERT INTO operation_effects(operation_id,stage,reference) VALUES(?,?,?)",
                (operation_id, stage, reference),
            )

    async def operation_effects(self, operation_id: str) -> list[dict]:
        return await self.rows(
            "SELECT stage,reference,created_at FROM operation_effects WHERE operation_id=? ORDER BY id", (operation_id,)
        )

    async def connection(self, identifier: str) -> dict | None:
        rows = await self.rows("SELECT value FROM connections WHERE id=?", (identifier,))
        return json.loads(rows[0]["value"]) if rows else None

    async def channel_state(self, connection_id: str, name: str) -> dict:
        rows = await self.rows(
            "SELECT value FROM channel_state WHERE connection_id=? AND name=?", (connection_id, name)
        )
        return json.loads(rows[0]["value"]) if rows else {}

    async def set_channel_state(self, connection_id: str, name: str, value: dict) -> None:
        async with self.transaction() as db:
            await db.execute(
                "INSERT INTO channel_state VALUES (?,?,?) ON CONFLICT(connection_id,name) DO UPDATE SET value=excluded.value",
                (connection_id, name, json.dumps(value)),
            )

    async def clear_channel_state(self, connection_id: str) -> None:
        async with self.transaction() as db:
            await db.execute("DELETE FROM channel_state WHERE connection_id=?", (connection_id,))

    async def archive_channel_event(self, connection: dict, payload: bytes) -> str | None:
        """Retain each authenticated arrival before projection or route selection."""
        identifier = new_id("recv")
        mode = (
            connection.get("transport")
            or {
                "qq": "websocket",
                "weixin": "polling",
                "onebots": "http_reverse",
                "slack": "webhook",
                "lark": "webhook",
                "discord": "websocket",
            }[connection["kind"]]
        )
        async with self.transaction() as db:
            async with db.execute("SELECT value FROM connections WHERE id=?", (connection["id"],)) as cursor:
                row = await cursor.fetchone()
            current = json.loads(row[0]) if row else None
            if not same_connection(current, connection):
                return None
            await db.execute(
                "INSERT INTO channel_events(id,channel_id,transport,payload) VALUES (?,?,?,?)",
                (identifier, connection["id"], mode, payload),
            )
        return identifier

    async def finish_channel_event(
        self,
        identifier: str,
        state: str,
        *,
        event_type: str | None = None,
        message_event_id: str | None = None,
        error: str | None = None,
    ) -> None:
        async with self.transaction() as db:
            await db.execute(
                "UPDATE channel_events SET state=?,event_type=?,message_event_id=?,error=? WHERE id=?",
                (state, event_type, message_event_id, error, identifier),
            )

    async def add_connection(self, value: dict) -> dict:
        value = {**value, "id": new_id("conn")}
        async with self.transaction() as db:
            await db.execute("INSERT INTO connections VALUES (?,?)", (value["id"], json.dumps(value)))
        return value

    async def rotate_host_key(self, host_id: str) -> str:
        token = secrets.token_urlsafe(32)
        async with self.transaction() as db:
            await db.execute(
                "INSERT INTO host_keys VALUES (?,?) ON CONFLICT(host_id) DO UPDATE SET token_hash=excluded.token_hash",
                (host_id, token_hash(token)),
            )
        return token

    async def host_for_token(self, token: str) -> dict | None:
        rows = await self.rows("SELECT host_id FROM host_keys WHERE token_hash=?", (token_hash(token),))
        host = await self.connection(rows[0]["host_id"]) if rows else None
        return host if host and host["kind"] in {"a13n", "mcp"} else None

    @staticmethod
    def route_view(row: dict) -> dict:
        value = {**json.loads(row["value"]), "thread_id": row["thread_id"]}
        value.setdefault("sender_policy", "allowlist")
        value.pop("access_mode", None)
        value.pop("reply_source", None)
        value.setdefault("parent_route_id", None)
        value.setdefault("read_channel_ids", None)
        value.setdefault("action_grants", [])
        value.setdefault("thread_policy", "conversation")
        value.setdefault("history_limit", 30)
        value.setdefault("busy_policy", "steer")
        value.setdefault("fixed_thread_id", None)
        return value

    async def routes(self) -> list[dict]:
        rows = await self.rows(
            "SELECT r.*,(json_type(r.value,'$.sources')='array' OR EXISTS(SELECT 1 FROM connections c WHERE c.id=r.channel_id)) "
            "AND EXISTS(SELECT 1 FROM connections h WHERE h.id=json_extract(r.value,'$.host_id')) AS channel_present "
            "FROM routes r ORDER BY r.id"
        )
        routes = [self.route_view(row) for row in rows]
        for route, row in zip(routes, rows, strict=True):
            route["enabled"] = route["enabled"] and bool(row["channel_present"])
        parents = {row["id"]: row for row in routes}
        for row in routes:
            if row["parent_route_id"]:
                parent = parents.get(row["parent_route_id"])
                if parent:
                    for key in (
                        "sender_policy",
                        "allowed_user_ids",
                        "mcp_connection_id",
                        "read_channel_ids",
                        "action_grants",
                        "thread_policy",
                        "history_limit",
                        "busy_policy",
                        "fixed_thread_id",
                    ):
                        row[key] = parent[key]
                    selected = select_route(routes, row["channel_id"], row["conversation_kind"], row["conversation_id"])
                    row["enabled"] = (
                        row["enabled"] and parent["enabled"] and bool(selected and selected["id"] == parent["id"])
                    )
                else:
                    row["enabled"] = False
        return routes

    async def route(self, identifier: str) -> dict | None:
        return next((row for row in await self.routes() if row["id"] == identifier), None)

    async def route_for_token(self, token: str) -> dict | None:
        rows = await self.rows("SELECT * FROM routes WHERE token_hash=?", (token_hash(token),))
        return await self.route(rows[0]["id"]) if rows else None

    async def _check_route_connections(self, db: aiosqlite.Connection, value: dict) -> bool:
        async with db.execute("SELECT value FROM connections") as cursor:
            connections = {item["id"]: item for row in await cursor.fetchall() if (item := json.loads(row[0]))}
        host = connections.get(value["host_id"])
        if not host or host["kind"] not in {"a13n", "mcp"}:
            return False
        return all(
            source["channel_id"] == "*"
            or (source["channel_id"] in connections and connections[source["channel_id"]]["kind"] in CHANNEL_KINDS)
            for source in route_sources(value)
        ) and all(
            channel_id == "*" or (channel_id in connections and connections[channel_id]["kind"] in CHANNEL_KINDS)
            for channel_id in value.get("read_channel_ids") or []
        )

    async def _stored_routes(self, db: aiosqlite.Connection) -> list[dict]:
        async with db.execute("SELECT * FROM routes") as cursor:
            return [self.route_view(dict(row)) for row in await cursor.fetchall()]

    async def _retire_route(self, db: aiosqlite.Connection, identifier: str) -> None:
        await db.execute(
            "INSERT INTO archived_routes SELECT id,channel_id,kind,conversation_id,value,thread_id FROM routes WHERE id=?",
            (identifier,),
        )
        await db.execute(
            "UPDATE events SET state='blocked',error='Route source or agent changed' WHERE route_id=? AND state IN ('queued','submitted')",
            (identifier,),
        )
        await db.execute(
            "UPDATE replies SET state='blocked',error='Route source or agent changed' WHERE route_id=? AND state='queued'",
            (identifier,),
        )
        await db.execute(
            "UPDATE channel_actions SET state='blocked',error='Route source or agent changed' WHERE route_id=? AND state='queued'",
            (identifier,),
        )
        await db.execute(
            "DELETE FROM channel_state WHERE EXISTS(SELECT 1 FROM routes r WHERE r.id=? "
            "AND r.channel_id=channel_state.connection_id AND channel_state.name='context:'||r.kind||':'||r.conversation_id)",
            (identifier,),
        )
        await db.execute(
            "DELETE FROM event_reply_contexts WHERE event_id IN (SELECT id FROM events WHERE route_id=?)", (identifier,)
        )
        await db.execute("DELETE FROM routes WHERE id=?", (identifier,))

    async def _reconcile_children(self, db: aiosqlite.Connection) -> None:
        routes = await self._stored_routes(db)
        for child in routes:
            if not child.get("parent_route_id"):
                continue
            selected = select_route(routes, child["channel_id"], child["conversation_kind"], child["conversation_id"])
            if (
                not selected
                or selected["id"] != child["parent_route_id"]
                or any(
                    selected.get(key) != child.get(key)
                    for key in ("host_id", "agent_id", "thread_policy", "fixed_thread_id")
                )
            ):
                await self._retire_route(db, child["id"])

    async def add_route(self, value: dict) -> tuple[dict, str]:
        value = {**value, "id": new_id("route")}
        token = secrets.token_urlsafe(32)
        async with self.transaction() as db:
            if not await self._check_route_connections(db, value):
                raise ValueError("Route connections no longer exist")
            check_source_conflicts(value, await self._stored_routes(db))
            await db.execute(
                "INSERT INTO routes (id,channel_id,kind,conversation_id,value,token_hash) VALUES (?,?,?,?,?,?)",
                (
                    value["id"],
                    value["id"] if value.get("sources") else value["channel_id"],
                    "source" if value.get("sources") else value["conversation_kind"],
                    value["conversation_id"],
                    json.dumps(value),
                    token_hash(token),
                ),
            )
            await self._reconcile_children(db)
        return {**value, "thread_id": None}, token

    async def conversation_route(
        self, parent: dict, conversation_id: str, channel_id: str | None = None, kind: str | None = None
    ) -> dict:
        channel_id = channel_id or parent["channel_id"]
        kind = kind or parent["conversation_kind"]
        async with self.transaction() as db:
            routes = await self._stored_routes(db)
            current = next((row for row in routes if row["id"] == parent["id"]), None)
            selected = select_route(routes, channel_id, kind, conversation_id)
            if (
                not current
                or current["conversation_id"] != "*"
                or not current["enabled"]
                or not selected
                or selected["id"] != current["id"]
            ):
                raise ValueError("A current matching all-conversation route is required")
            value = {
                **current,
                "id": new_id("route"),
                "channel_id": channel_id,
                "conversation_kind": kind,
                "conversation_id": conversation_id,
                "parent_route_id": current["id"],
                "mcp_connection_id": None,
            }
            value.pop("thread_id", None)
            value.pop("sources", None)
            if not await self._check_route_connections(db, value):
                raise ValueError("Route connections no longer exist")
            await db.execute(
                "INSERT OR IGNORE INTO routes (id,channel_id,kind,conversation_id,value,token_hash) VALUES (?,?,?,?,?,?)",
                (
                    value["id"],
                    channel_id,
                    kind,
                    conversation_id,
                    json.dumps(value),
                    token_hash(secrets.token_urlsafe(32)),
                ),
            )
        return next(
            row
            for row in await self.routes()
            if row["channel_id"] == channel_id
            and row["conversation_kind"] == kind
            and row["conversation_id"] == conversation_id
        )

    async def observe(self, identifier: str, message: dict, *, state: str, error: str) -> None:
        async with self.transaction() as db:
            await db.execute(
                "INSERT OR IGNORE INTO observations(id,message,state,error) SELECT ?,?,?,? WHERE NOT EXISTS (SELECT 1 FROM events WHERE id=?)",
                (identifier, json.dumps(message), state, error, identifier),
            )

    async def materialize_observed_routes(self, parent: dict) -> None:
        """Give previously received authorized conversations concrete MCP handles."""
        if parent.get("sources"):
            observations = await self.rows("SELECT message FROM observations")
            routes = await self.routes()
            seen = set()
            for observation in observations:
                message = json.loads(observation["message"])
                address = message["address"]
                channel = address["account_id"]
                kind, conversation = address["conversation_id"].split(":", 1)
                selected = select_route(routes, channel, kind, conversation)
                identity = (channel, kind, conversation)
                if (
                    identity in seen
                    or not selected
                    or selected["id"] != parent["id"]
                    or not route_allows_sender(selected, message["sender_id"])
                ):
                    continue
                seen.add(identity)
                if await self.connection(channel):
                    await self.conversation_route(parent, conversation, channel, kind)
            # Specific configured sources are usable by MCP before receiving a message.
            for source in parent["sources"]:
                if source["channel_id"] != "*" and source["conversation_id"] != "*":
                    selected = select_route(
                        routes, source["channel_id"], source["conversation_kind"], source["conversation_id"]
                    )
                    if selected and selected["id"] == parent["id"] and await self.connection(source["channel_id"]):
                        await self.conversation_route(
                            parent, source["conversation_id"], source["channel_id"], source["conversation_kind"]
                        )
            return
        sender_filter = ""
        params: tuple = (parent["channel_id"], parent["conversation_kind"])
        if parent.get("sender_policy", "allowlist") != "any":
            sender_filter = " AND json_extract(o.message,'$.sender_id') IN (SELECT value FROM json_each(?))"
            params += (json.dumps(parent["allowed_user_ids"]),)
        rows = await self.rows(
            """SELECT DISTINCT substr(json_extract(o.message,'$.address.conversation_id'),instr(json_extract(o.message,'$.address.conversation_id'),':')+1) AS conversation_id
            FROM observations o WHERE json_extract(o.message,'$.address.account_id')=?
            AND substr(json_extract(o.message,'$.address.conversation_id'),1,instr(json_extract(o.message,'$.address.conversation_id'),':')-1)=?
            AND NOT EXISTS(SELECT 1 FROM routes r WHERE r.channel_id=json_extract(o.message,'$.address.account_id')
                AND r.kind||':'||r.conversation_id=json_extract(o.message,'$.address.conversation_id'))"""
            + sender_filter,
            params,
        )
        routes = await self.routes()
        for row in rows:
            selected = select_route(routes, parent["channel_id"], parent["conversation_kind"], row["conversation_id"])
            if selected and selected["id"] == parent["id"]:
                await self.conversation_route(parent, row["conversation_id"])

    async def update_route(self, identifier: str, value: dict, expected: dict) -> bool:
        expected = {key: item for key, item in expected.items() if key != "thread_id"}
        async with self.transaction() as db:
            if not await self._check_route_connections(db, value):
                return False
            async with db.execute("SELECT * FROM routes WHERE id=?", (identifier,)) as cursor:
                row = await cursor.fetchone()
            if (
                not row
                or {key: item for key, item in self.route_view(dict(row)).items() if key != "thread_id"} != expected
            ):
                return False
            check_source_conflicts(value, await self._stored_routes(db))
            old = self.route_view(dict(row))
            if any(
                value.get(key) != old.get(key)
                for key in (
                    "host_id",
                    "agent_id",
                    "thread_policy",
                    "fixed_thread_id",
                    "mcp_connection_id",
                )
            ):
                async with db.execute(
                    "SELECT 1 FROM events e JOIN routes r ON r.id=e.route_id "
                    "WHERE (r.id=? OR json_extract(r.value,'$.parent_route_id')=?) "
                    "AND e.state IN ('queued','submitted') LIMIT 1",
                    (identifier, identifier),
                ) as cursor:
                    if await cursor.fetchone():
                        raise RouteConflict("Wait for the current input to finish before changing the delivery policy")
            if value.get("sources") and not old.get("sources") and old["conversation_id"] != "*":
                # Keep the existing conversation and thread under a concrete child.
                # Remap history atomically; the public rule ID and token stay stable.
                child = {**old, "id": new_id("route"), "parent_route_id": identifier, "mcp_connection_id": None}
                child.pop("thread_id", None)
                await db.execute(
                    "UPDATE routes SET channel_id=?,kind='source',conversation_id='*',value=?,thread_id=NULL WHERE id=?",
                    (identifier, json.dumps(value), identifier),
                )
                await db.execute(
                    "INSERT INTO routes(id,channel_id,kind,conversation_id,value,token_hash,thread_id) VALUES(?,?,?,?,?,?,?)",
                    (
                        child["id"],
                        child["channel_id"],
                        child["conversation_kind"],
                        child["conversation_id"],
                        json.dumps(child),
                        token_hash(secrets.token_urlsafe(32)),
                        row["thread_id"],
                    ),
                )
                await db.execute("UPDATE events SET route_id=? WHERE route_id=?", (child["id"], identifier))
                await db.execute("UPDATE replies SET route_id=? WHERE route_id=?", (child["id"], identifier))
                await db.execute("UPDATE channel_actions SET route_id=? WHERE route_id=?", (child["id"], identifier))
            elif value.get("sources"):
                await db.execute(
                    "UPDATE routes SET channel_id=?,kind='source',conversation_id='*',value=?,thread_id=NULL WHERE id=?",
                    (identifier, json.dumps(value), identifier),
                )
            else:
                await db.execute("UPDATE routes SET value=? WHERE id=?", (json.dumps(value), identifier))
            await self._reconcile_children(db)
            return True

    async def update_connection(self, identifier: str, value: dict, expected: dict) -> bool:
        async with self.transaction() as db:
            cursor = await db.execute(
                "UPDATE connections SET value=? WHERE id=? AND value=?",
                (json.dumps(value), identifier, json.dumps(expected)),
            )
            return cursor.rowcount == 1

    async def delete_connection(self, identifier: str, expected: dict) -> bool:
        async with self.transaction() as db:
            cursor = await db.execute(
                "DELETE FROM connections WHERE id=? AND value=?",
                (identifier, json.dumps(expected)),
            )
            if not cursor.rowcount:
                return False
            for route in await self._stored_routes(db):
                readable = route.get("read_channel_ids")
                if readable is not None and identifier in readable:
                    route["read_channel_ids"] = [channel for channel in readable if channel != identifier]
                    route.pop("thread_id", None)
                    await db.execute("UPDATE routes SET value=? WHERE id=?", (json.dumps(route), route["id"]))
            await db.execute(
                "UPDATE routes SET value=json_set(value,'$.enabled',json('false')) "
                "WHERE channel_id=? OR json_extract(value,'$.host_id')=?",
                (identifier, identifier),
            )
            await db.execute(
                "UPDATE events SET state='blocked',error='Connection deleted' WHERE route_id IN "
                "(SELECT id FROM routes WHERE channel_id=? OR json_extract(value,'$.host_id')=?) "
                "AND state IN ('queued','submitted')",
                (identifier, identifier),
            )
            await db.execute(
                "UPDATE replies SET state='blocked',error='Connection deleted' WHERE route_id IN "
                "(SELECT id FROM routes WHERE channel_id=? OR json_extract(value,'$.host_id')=?) AND state='queued'",
                (identifier, identifier),
            )
            await db.execute(
                "UPDATE channel_actions SET state='blocked',error='Connection deleted' WHERE state='queued' AND route_id IN (SELECT id FROM routes WHERE channel_id=? OR json_extract(value,'$.host_id')=?)",
                (identifier, identifier),
            )
            await db.execute(
                "DELETE FROM event_reply_contexts WHERE event_id IN (SELECT e.id FROM events e JOIN routes r ON r.id=e.route_id WHERE r.channel_id=? OR json_extract(r.value,'$.host_id')=?)",
                (identifier, identifier),
            )
            await db.execute("DELETE FROM channel_state WHERE connection_id=?", (identifier,))
            await db.execute("DELETE FROM host_keys WHERE host_id=?", (identifier,))
            return True

    async def delete_route(self, identifier: str) -> bool:
        async with self.transaction() as db:
            async with db.execute("SELECT id FROM routes WHERE id=?", (identifier,)) as cursor:
                if await cursor.fetchone() is None:
                    return False
            selection = "id=? OR json_extract(value,'$.parent_route_id')=?"
            params = (identifier, identifier)
            await db.execute(
                "INSERT INTO archived_routes SELECT id,channel_id,kind,conversation_id,value,thread_id "
                "FROM routes WHERE " + selection,
                params,
            )
            await db.execute(
                "UPDATE events SET state='blocked',error='Route deleted' WHERE state IN ('queued','submitted') "
                "AND route_id IN (SELECT id FROM routes WHERE " + selection + ")",
                params,
            )
            await db.execute(
                "UPDATE replies SET state='blocked',error='Route deleted' WHERE state='queued' "
                "AND route_id IN (SELECT id FROM routes WHERE " + selection + ")",
                params,
            )
            await db.execute(
                "DELETE FROM channel_state WHERE EXISTS(SELECT 1 FROM routes r WHERE "
                "(r.id=? OR json_extract(r.value,'$.parent_route_id')=?) "
                "AND r.channel_id=channel_state.connection_id AND channel_state.name='context:'||r.kind||':'||r.conversation_id)",
                params,
            )
            await db.execute(
                "UPDATE channel_actions SET state='blocked',error='Route deleted' WHERE state='queued' AND route_id IN (SELECT id FROM routes WHERE "
                + selection
                + ")",
                params,
            )
            await db.execute(
                "DELETE FROM event_reply_contexts WHERE event_id IN (SELECT e.id FROM events e JOIN routes r ON e.route_id=r.id WHERE "
                + selection.replace("id=", "r.id=").replace("json_extract(value,", "json_extract(r.value,")
                + ")",
                params,
            )
            await db.execute("DELETE FROM routes WHERE " + selection, params)
            return True

    async def rotate_token(self, identifier: str) -> str:
        token = secrets.token_urlsafe(32)
        async with self.transaction() as db:
            await db.execute("UPDATE routes SET token_hash=? WHERE id=?", (token_hash(token), identifier))
        return token

    async def ingest(self, identifier: str, route_id: str, message: dict, context: dict | None = None) -> bool:
        async with self.transaction() as db:
            cursor = await db.execute(
                "INSERT OR IGNORE INTO events(id,route_id,message) SELECT ?,?,? "
                "WHERE NOT EXISTS (SELECT 1 FROM observations WHERE id=?) "
                "AND EXISTS (SELECT 1 FROM connections WHERE id=?) "
                "AND EXISTS (SELECT 1 FROM routes WHERE id=? AND json_extract(value,'$.enabled')=1)",
                (identifier, route_id, json.dumps(message), identifier, message["address"]["account_id"], route_id),
            )
            if context and cursor.rowcount == 1:
                await db.execute(
                    "INSERT OR IGNORE INTO event_reply_contexts VALUES (?,?)", (identifier, json.dumps(context))
                )
                await db.execute(
                    """INSERT INTO channel_state VALUES (?,?,?) ON CONFLICT(connection_id,name)
                    DO UPDATE SET value=excluded.value WHERE
                    json_extract(channel_state.value,'$.source_time')<=json_extract(excluded.value,'$.source_time')""",
                    (
                        message["address"]["account_id"],
                        "context:" + message["address"]["conversation_id"],
                        json.dumps(context),
                    ),
                )
            return cursor.rowcount == 1

    async def next_channel_sequence(self, connection_id: str, message_id: str) -> int:
        key = "qq_seq:" + message_id
        async with self.transaction() as db:
            await db.execute(
                """INSERT INTO channel_state VALUES (?,?, '{"sequence":1}') ON CONFLICT(connection_id,name)
                DO UPDATE SET value=json_set(value,'$.sequence',json_extract(value,'$.sequence')+1)""",
                (connection_id, key),
            )
            async with db.execute(
                "SELECT value FROM channel_state WHERE connection_id=? AND name=?", (connection_id, key)
            ) as cursor:
                row = await cursor.fetchone()
                assert row is not None
                return json.loads(row[0])["sequence"]

    async def event(self, identifier: str) -> dict | None:
        rows = await self.rows("SELECT * FROM events WHERE id=?", (identifier,))
        if not rows:
            return None
        rows[0]["message"] = json.loads(rows[0]["message"])
        return rows[0]

    async def events(self, *, route_id: str | None = None, limit: int = 100) -> list[dict]:
        where = " WHERE e.route_id=?" if route_id else ""
        params = (route_id, limit) if route_id else (limit,)
        rows = await self.rows(
            "SELECT e.*,COALESCE(r.channel_id,a.channel_id) AS source_channel,"
            "COALESCE(r.kind,a.kind) AS source_kind,COALESCE(r.conversation_id,a.conversation_id) AS source_conversation,"
            "json_extract(COALESCE(r.value,a.value),'$.name') AS route_name FROM events e "
            "LEFT JOIN routes r ON r.id=e.route_id LEFT JOIN archived_routes a ON a.id=e.route_id"
            + where
            + " ORDER BY e.rowid DESC LIMIT ?",
            params,
        )
        for row in rows:
            row["message"] = public_message(json.loads(row["message"]))
            channel = row.pop("source_channel")
            kind = row.pop("source_kind")
            conversation = row.pop("source_conversation")
            if channel and conversation != "*":
                row["message"].setdefault(
                    "address", {"account_id": channel, "conversation_id": f"{kind}:{conversation}"}
                )
            row.pop("body")
            row.pop("target")
        if route_id is None:
            observations = await self.rows("SELECT * FROM observations ORDER BY rowid DESC LIMIT ?", (limit,))
            for row in observations:
                row["message"] = public_message(json.loads(row["message"]))
                row.update(route_id=None, thread_id=None, run_id=None)
            rows = sorted(rows + observations, key=lambda row: row["created_at"], reverse=True)[:limit]
        return rows

    async def pending(self) -> list[dict]:
        """Admit FIFO submissions independently of polling already accepted runs."""
        routes = {route["id"]: route for route in await self.routes()}
        bindings = {row["binding_key"]: row["thread_id"] for row in await self.rows("SELECT * FROM thread_bindings")}
        work = await self.rows("""SELECT rowid AS sequence,id,route_id,state,thread_id,target,
            json_extract(message,'$.sender_id') AS sender_id,
            body IS NOT NULL AS frozen,json_extract(body,'$.delivery') AS delivery
            FROM events WHERE state IN ('queued','submitted') ORDER BY rowid""")
        scopes: dict[tuple, list[dict]] = {}
        for event in work:
            route = routes.get(event["route_id"])
            if not route:
                continue
            key = thread_binding_key(route, {"sender_id": event["sender_id"]})
            target = event["target"] or ""
            pinned = (
                unquote(target[len("/threads/") : -len("/inbox")])
                if target.startswith("/threads/") and target.endswith("/inbox")
                else None
            )
            thread = event["thread_id"] or pinned
            if not thread:
                thread = bindings.get(key) if key else None
                if route.get("thread_policy", "conversation") == "fixed":
                    thread = route.get("fixed_thread_id") or thread
                elif route.get("thread_policy", "conversation") == "conversation":
                    thread = thread or route.get("thread_id")
            scope = (
                ("binding", key)
                if route.get("thread_policy") == "window"
                else ("thread", route["host_id"], thread)
                if thread
                else ("binding", key or event["id"])
            )
            scopes.setdefault(scope, []).append(event)
        queued, submitted = [], []
        for events in scopes.values():
            submitted.extend(event for event in events if event["state"] == "submitted")
            head = next((event for event in events if event["state"] == "queued"), None)
            if head is None:
                continue
            route = routes[head["route_id"]]
            policy = (
                ("queue" if head["delivery"] != "steer" else "steer")
                if head["frozen"]
                else route.get("busy_policy", "steer")
            )
            if route.get("thread_policy") == "window":
                policy = "steer"
            if policy == "steer" or not any(
                event["state"] == "submitted" and event["sequence"] < head["sequence"] for event in events
            ):
                queued.append(head)
        queued.sort(key=lambda event: event["sequence"])
        submitted.sort(key=lambda event: event["sequence"])
        # Reserve polling capacity even under a continuous stream of new input.
        count = min(len(queued), max(50, 100 - len(submitted)))
        selected = queued[:count] + submitted[: 100 - count]
        if not selected:
            return []
        ids = [event["id"] for event in selected]
        rows = await self.rows("SELECT * FROM events WHERE id IN (" + ",".join("?" for _ in ids) + ")", tuple(ids))
        values = {row["id"]: row for row in rows}
        return [values[identifier] for identifier in ids]

    async def received_messages(
        self,
        route: dict,
        *,
        limit: int = 20,
        source_message_id: str | None = None,
        history_metadata: bool = False,
        before: tuple[str, str] | None = None,
    ) -> list[dict]:
        """Read source history under current access, independently of past delivery."""
        params: tuple = (route["id"], route["channel_id"], f"{route['conversation_kind']}:{route['conversation_id']}")
        sender_filter = ""
        if route.get("sender_policy", "allowlist") != "any":
            sender_filter = " AND json_extract(message,'$.sender_id') IN (SELECT value FROM json_each(?))"
            params += (json.dumps(route["allowed_user_ids"]),)
        if source_message_id is not None:
            sender_filter += " AND json_extract(message,'$.source_message_id')=?"
            params += (source_message_id,)
        if before is not None:
            sender_filter += " AND (created_at,id)<(?,?)"
            params += before
        rows = await self.rows(
            """WITH received AS (
                SELECT e.id,e.message,e.created_at,
                    COALESCE(json_extract(e.message,'$.address.account_id'),r.channel_id) AS channel_id,
                    COALESCE(json_extract(e.message,'$.address.conversation_id'),r.kind||':'||r.conversation_id) AS conversation_id
                FROM events e JOIN routes r ON r.id=e.route_id WHERE e.route_id=?
                UNION ALL
                SELECT id,message,created_at,json_extract(message,'$.address.account_id'),
                    json_extract(message,'$.address.conversation_id') FROM observations
            ) SELECT id,created_at,message,channel_id,conversation_id FROM received WHERE channel_id=? AND conversation_id=?"""
            + sender_filter
            + " ORDER BY created_at DESC,id DESC LIMIT ?",
            (*params, max(1, min(limit, 100))),
        )
        messages = []
        for row in reversed(rows):
            message = json.loads(row["message"])
            message.setdefault("address", {"account_id": row["channel_id"], "conversation_id": row["conversation_id"]})
            if history_metadata:
                message.update(history_id=row["id"], created_at=row["created_at"], direction="incoming")
            messages.append(message)
        return messages

    @staticmethod
    def received_history_sql() -> str:
        return """WITH destinations AS (
            SELECT id,channel_id,kind,conversation_id FROM routes
            UNION ALL SELECT id,channel_id,kind,conversation_id FROM archived_routes
        ), received AS (
            SELECT e.id,e.message,e.created_at,
                COALESCE(json_extract(e.message,'$.address.account_id'),r.channel_id) AS channel_id,
                COALESCE(json_extract(e.message,'$.address.conversation_id'),r.kind||':'||r.conversation_id) AS conversation_id
            FROM events e LEFT JOIN destinations r ON r.id=e.route_id
            UNION ALL SELECT id,message,created_at,json_extract(message,'$.address.account_id'),
                json_extract(message,'$.address.conversation_id') FROM observations
        ) """

    async def readable_conversations(self, channel_ids: list[str]) -> list[dict]:
        """List observed identities under explicit channel read grants without creating routes."""
        rows = await self.rows(
            self.received_history_sql() + """, observed AS (
                SELECT channel_id,conversation_id FROM received
                UNION
                SELECT r.channel_id,r.kind||':'||r.conversation_id
                FROM replies p JOIN destinations r ON r.id=p.route_id
                LEFT JOIN reply_payloads q ON q.reply_id=p.id
                WHERE p.state='sent' AND COALESCE(json_extract(q.payload,'$.kind'),'message')!='reaction'
            ) SELECT DISTINCT channel_id,conversation_id FROM observed """
            "JOIN connections c ON c.id=observed.channel_id "
            "WHERE json_extract(c.value,'$.kind') IN ('onebots','qq','weixin','slack','lark','discord') "
            "AND (? OR channel_id IN (SELECT value FROM json_each(?))) ORDER BY channel_id,conversation_id",
            ("*" in channel_ids, json.dumps(channel_ids)),
        )
        return [
            {"channel_id": row["channel_id"], "conversation_kind": kind, "conversation_id": identity}
            for row in rows
            if (kind := row["conversation_id"].split(":", 1)[0]) in CONVERSATION_KINDS
            and (identity := row["conversation_id"].split(":", 1)[1])
        ]

    async def received_channel_messages(
        self,
        conversation: dict,
        *,
        limit: int = 20,
        source_message_id: str | None = None,
        history_metadata: bool = False,
        before: tuple[str, str] | None = None,
    ) -> list[dict]:
        """Read projected incoming text for an explicitly authorized account identity."""
        rows = await self.rows(
            self.received_history_sql() + "SELECT id,created_at,message,channel_id,conversation_id FROM received "
            "WHERE channel_id=? AND conversation_id=? "
            "AND (? IS NULL OR json_extract(message,'$.source_message_id')=?) "
            "AND (? IS NULL OR (created_at,id)<(?,?)) "
            "AND EXISTS(SELECT 1 FROM connections c WHERE c.id=received.channel_id "
            "AND json_extract(c.value,'$.kind') IN ('onebots','qq','weixin','slack','lark','discord')) "
            "ORDER BY created_at DESC,id DESC LIMIT ?",
            (
                conversation["channel_id"],
                f"{conversation['conversation_kind']}:{conversation['conversation_id']}",
                source_message_id,
                source_message_id,
                before[0] if before else None,
                before[0] if before else None,
                before[1] if before else None,
                max(1, min(limit, 100)),
            ),
        )
        messages = []
        for row in reversed(rows):
            message = json.loads(row["message"])
            message.setdefault("address", {"account_id": row["channel_id"], "conversation_id": row["conversation_id"]})
            if history_metadata:
                message.update(history_id=row["id"], created_at=row["created_at"], direction="incoming")
            messages.append(message)
        return messages

    async def sent_messages(self, route: dict, *, limit: int = 20, before: tuple[str, str] | None = None) -> list[dict]:
        """Read confirmed bot messages under the selected conversation's current read scope."""
        params: tuple = (route["channel_id"], route["conversation_kind"], route["conversation_id"])
        where = "r.channel_id=? AND r.kind=? AND r.conversation_id=?"
        if not route["channel_read"]:
            where += " AND p.route_id=?"
            params += (route["id"],)
            if route.get("sender_policy", "allowlist") != "any":
                where += " AND (p.event_id IS NULL OR json_extract(e.message,'$.sender_id') IN (SELECT value FROM json_each(?)))"
                params += (json.dumps(route["allowed_user_ids"]),)
        if route.get("history_sender_id"):
            where += " AND json_extract(e.message,'$.sender_id')=?"
            params += (route["history_sender_id"],)
        if before is not None:
            where += " AND (p.created_at,p.id)<(?,?)"
            params += before
        rows = await self.rows(
            """WITH destinations AS (
                SELECT id,channel_id,kind,conversation_id FROM routes
                UNION ALL SELECT id,channel_id,kind,conversation_id FROM archived_routes
            ) SELECT p.id,p.text,p.message_id,p.created_at,q.payload,json_extract(c.value,'$.kind') AS channel
              FROM replies p JOIN destinations r ON r.id=p.route_id
              JOIN connections c ON c.id=r.channel_id
              LEFT JOIN events e ON e.id=p.event_id
              LEFT JOIN reply_payloads q ON q.reply_id=p.id
              WHERE p.state='sent' AND COALESCE(json_extract(q.payload,'$.kind'),'message')!='reaction' AND """
            + where
            + " ORDER BY p.created_at DESC,p.id DESC LIMIT ?",
            (*params, max(1, min(limit, 100))),
        )
        messages = []
        for row in reversed(rows):
            payload = json.loads(row["payload"]) if row["payload"] else {}
            media = []
            if payload.get("kind") in {"image", "media"}:
                media.append(
                    {
                        "kind": "image" if payload["kind"] == "image" else payload["media_kind"],
                        "media_type": payload.get("media_type"),
                        "filename": payload.get("filename"),
                    }
                )
            messages.append(
                {
                    "history_id": row["id"],
                    "created_at": row["created_at"],
                    "direction": "outgoing",
                    "state": "sent",
                    "source_message_id": row["message_id"],
                    "sender_id": None,
                    "sender_kind": "bot",
                    "address": {
                        "channel": row["channel"],
                        "account_id": route["channel_id"],
                        "conversation_id": f"{route['conversation_kind']}:{route['conversation_id']}",
                    },
                    "text": row["text"],
                    "images": [],
                    "attachments": [],
                    "media": media,
                }
            )
        return messages

    async def conversation_messages(
        self, route: dict, *, limit: int = 20, before: tuple[str, str] | None = None
    ) -> list[dict]:
        limit = max(1, min(limit, 100))
        incoming = (
            await self.received_channel_messages(route, limit=limit, history_metadata=True, before=before)
            if route["channel_read"]
            else await self.received_messages(route, limit=limit, history_metadata=True, before=before)
        )
        outgoing = await self.sent_messages(route, limit=limit, before=before)
        return sorted(incoming + outgoing, key=lambda message: (message["created_at"], message["history_id"]))[-limit:]

    async def environment_thread(self, route: dict, message: dict) -> str | None:
        """Last accepted thread in this conversation, solely for reusing its mounts."""
        key = thread_binding_key(route, message)
        rows = await self.rows(
            "SELECT e.thread_id FROM event_thread_bindings b JOIN events e ON e.id=b.event_id "
            "WHERE b.binding_key=? AND e.thread_id IS NOT NULL ORDER BY e.rowid DESC LIMIT 1",
            (key,),
        )
        return rows[0]["thread_id"] if rows else route.get("thread_id")

    async def delivery_history(self, event: dict, route: dict) -> list[dict]:
        """Snapshot prior chat content within the triggering conversation's read scope."""
        limit = route.get("history_limit", 30)
        if not limit:
            return []
        grants = route.get("read_channel_ids")
        if grants is not None and "*" not in grants and route["channel_id"] not in grants:
            return []
        scope = {**route, "channel_read": grants is not None}
        if route.get("thread_policy") == "sender":
            sender = json.loads(event["message"])["sender_id"]
            scope.update(
                channel_read=False, sender_policy="allowlist", allowed_user_ids=[sender], history_sender_id=sender
            )
        messages = await self.conversation_messages(scope, limit=limit, before=(event["created_at"], event["id"]))
        # Project only chat content and public media handles, never private payloads.
        return [
            {
                "direction": item["direction"],
                "source_message_id": item["source_message_id"],
                "sender_id": item["sender_id"],
                "created_at": item["created_at"],
                "text": item["text"],
                "image_count": len(item.get("images", [])),
                "attachment_count": len(item.get("attachments", [])),
            }
            for item in messages
        ]

    async def conversation_history(
        self,
        channel_id: str,
        conversation_id: str,
        *,
        limit: int = 100,
        before_created_at: str | None = None,
        before_id: str | None = None,
    ) -> dict:
        where = "WHERE channel_id=? AND conversation_id=?"
        params: tuple = (channel_id, conversation_id)
        if before_created_at is not None and before_id is not None:
            where += " AND (created_at,id)<(?,?)"
            params += (before_created_at, before_id)
        rows = await self.rows(
            """WITH destinations AS (
                SELECT id,channel_id,kind,conversation_id,thread_id FROM routes
                UNION ALL SELECT id,channel_id,kind,conversation_id,thread_id FROM archived_routes
            ), history AS (
                SELECT e.id,e.route_id,'incoming' AS direction,json_extract(e.message,'$.text') AS text,
                    json_extract(e.message,'$.sender_id') AS sender_id,e.state,e.error,e.created_at,e.thread_id,e.run_id,
                    COALESCE(json_extract(e.message,'$.address.account_id'),r.channel_id) AS channel_id,
                    COALESCE(json_extract(e.message,'$.address.conversation_id'),r.kind||':'||r.conversation_id) AS conversation_id
                FROM events e LEFT JOIN destinations r ON r.id=e.route_id
                UNION ALL
                SELECT id,NULL,'incoming',json_extract(message,'$.text'),json_extract(message,'$.sender_id'),
                    state,error,created_at,NULL,NULL,json_extract(message,'$.address.account_id'),
                    json_extract(message,'$.address.conversation_id') FROM observations
                UNION ALL
                SELECT a.id,a.route_id,'action','['||a.operation||']',NULL,a.state,a.error,a.created_at,
                    NULL,NULL,r.channel_id,r.kind||':'||r.conversation_id
                FROM channel_actions a JOIN destinations r ON r.id=a.route_id
                UNION ALL
                SELECT p.id,p.route_id,'outgoing',p.text,NULL,p.state,p.error,p.created_at,
                    COALESCE((SELECT thread_id FROM events WHERE id=p.event_id),r.thread_id),NULL,
                    r.channel_id,r.kind||':'||r.conversation_id FROM replies p JOIN destinations r ON r.id=p.route_id
            ) SELECT id,route_id,direction,text,sender_id,state,error,created_at,thread_id,run_id FROM history """
            + where
            + " ORDER BY created_at DESC,id DESC LIMIT ?",
            (*params, limit + 1),
        )
        return {"messages": list(reversed(rows[:limit])), "has_more": len(rows) > limit}

    async def update_event(self, identifier: str, **fields) -> None:
        allowed = {"state", "target", "body", "thread_id", "entry_id", "run_id", "attempts", "error"}
        if not fields or not fields.keys() <= allowed:
            raise ValueError("Invalid event update")
        async with self.transaction() as db:
            await db.execute(
                "UPDATE events SET " + ",".join(key + "=?" for key in fields) + " WHERE id=?",
                (*fields.values(), identifier),
            )

    async def prepare_delivery(
        self, event: dict, route: dict, body: dict, *, active_thread_id: str | None = None
    ) -> str:
        """Freeze a target, payload and binding scope before any external request."""
        key = thread_binding_key(route, json.loads(event["message"]))
        owner = route.get("parent_route_id") or route["id"]
        async with self.transaction() as db:
            async with db.execute("SELECT target FROM events WHERE id=?", (event["id"],)) as cursor:
                saved = await cursor.fetchone()
            if saved and saved[0]:
                return saved[0]
            thread_id = None
            if key:
                async with db.execute("SELECT thread_id FROM thread_bindings WHERE binding_key=?", (key,)) as cursor:
                    binding = await cursor.fetchone()
                thread_id = binding[0] if binding else None
            policy = route.get("thread_policy", "conversation")
            if policy == "fixed":
                thread_id = route.get("fixed_thread_id") or thread_id
            elif policy == "conversation":
                # A manual reset may have cleared the binding after the worker
                # read this route. Consult the current binding inside the lock.
                async with db.execute("SELECT thread_id FROM routes WHERE id=?", (route["id"],)) as cursor:
                    current = await cursor.fetchone()
                thread_id = thread_id or (current[0] if current else None)
            if policy == "window":
                thread_id = active_thread_id
            target = "/threads/" + quote(thread_id, safe="") + "/inbox" if thread_id else "/threads"
            await db.execute(
                "INSERT INTO event_thread_bindings(event_id,binding_key,route_id) VALUES (?,?,?)",
                (event["id"], key, owner),
            )
            await db.execute("UPDATE events SET target=?,body=? WHERE id=?", (target, json.dumps(body), event["id"]))
        return target

    async def reset_thread(self, route_id: str) -> dict | None:
        """Detach a route's threads without replaying or deleting history."""
        async with self.transaction() as db:
            async with db.execute("SELECT * FROM routes WHERE id=?", (route_id,)) as cursor:
                row = await cursor.fetchone()
            if row is None:
                return None
            route = self.route_view(dict(row))
            async with db.execute("SELECT value FROM connections WHERE id=?", (route["host_id"],)) as cursor:
                host = await cursor.fetchone()
            if not host or json.loads(host[0])["kind"] != "a13n":
                raise ValueError("Only a13n conversations have thread bindings")
            if route["thread_policy"] == "fixed" and route["fixed_thread_id"]:
                raise ValueError("Choose a new fixed thread in route settings")
            if route["thread_policy"] == "message":
                raise ValueError("This route already creates a new thread for each message")
            async with db.execute(
                "SELECT id FROM routes WHERE id=? OR json_extract(value,'$.parent_route_id')=?", (route_id, route_id)
            ) as cursor:
                scope = tuple(item[0] for item in await cursor.fetchall())
            placeholders = ",".join("?" for _ in scope)
            async with db.execute(
                f"SELECT 1 FROM events WHERE route_id IN ({placeholders}) AND state IN ('queued','submitted') LIMIT 1",
                scope,
            ) as cursor:
                if await cursor.fetchone():
                    raise RouteConflict("Wait for this route's pending messages to finish before resetting its threads")
            await db.execute(
                f"DELETE FROM thread_bindings WHERE route_id IN ({placeholders}) OR binding_key IN "
                "(SELECT b.binding_key FROM event_thread_bindings b JOIN events e ON e.id=b.event_id "
                f"WHERE e.route_id IN ({placeholders}))",
                (*scope, *scope),
            )
            # Retried historical requests retain their frozen target/receipt but
            # must never restore a binding that an administrator detached.
            await db.execute(
                "DELETE FROM event_thread_bindings WHERE event_id IN "
                f"(SELECT id FROM events WHERE route_id IN ({placeholders}))",
                scope,
            )
            await db.execute(f"UPDATE routes SET thread_id=NULL WHERE id IN ({placeholders})", scope)
        return {"id": route_id, "thread_id": None}

    async def submitted(self, event_id: str, route_id: str, receipt: dict) -> None:
        thread_id = receipt["thread"]["id"]
        entry_id = receipt["entry"]["id"]
        run_id = (receipt.get("run") or {}).get("id")
        async with self.transaction() as db:
            await db.execute(
                "UPDATE routes SET thread_id=COALESCE(thread_id,?) WHERE id=? "
                "AND COALESCE(json_extract(value,'$.thread_policy'),'conversation')='conversation' "
                "AND EXISTS(SELECT 1 FROM event_thread_bindings WHERE event_id=?)",
                (thread_id, route_id, event_id),
            )
            await db.execute(
                "INSERT OR IGNORE INTO thread_bindings(binding_key,route_id,thread_id) "
                "SELECT b.binding_key,b.route_id,? FROM event_thread_bindings b "
                "JOIN routes r ON r.id=b.route_id WHERE b.event_id=? AND b.binding_key IS NOT NULL",
                (thread_id, event_id),
            )
            await db.execute(
                "UPDATE events SET state='submitted',thread_id=?,entry_id=?,run_id=?,attempts=0,error=NULL WHERE id=?",
                (thread_id, entry_id, run_id, event_id),
            )

    async def queue_reply(self, route: dict, text: str, *, payload: dict | None = None) -> dict:
        row = {"id": new_id("reply"), "route_id": route["id"], "event_id": None, "text": text, "state": "queued"}
        async with self.transaction() as db:
            cursor = await db.execute(
                "INSERT OR IGNORE INTO replies(id,route_id,event_id,text,state) VALUES (?,?,?,?,?)",
                tuple(row[key] for key in ["id", "route_id", "event_id", "text", "state"]),
            )
            if payload is not None and cursor.rowcount == 1:
                await db.execute("INSERT INTO reply_payloads VALUES (?,?)", (row["id"], json.dumps(payload)))
        return row

    async def reply_payload(self, identifier: str) -> dict | None:
        rows = await self.rows("SELECT payload FROM reply_payloads WHERE reply_id=?", (identifier,))
        return json.loads(rows[0]["payload"]) if rows else None

    async def event_reply_context(self, identifier: str) -> dict:
        rows = await self.rows("SELECT context FROM event_reply_contexts WHERE event_id=?", (identifier,))
        return json.loads(rows[0]["context"]) if rows else {}

    async def replies(self) -> list[dict]:
        rows = await self.rows("""WITH destinations AS (
            SELECT id,channel_id,kind,conversation_id,value FROM routes
            UNION ALL SELECT id,channel_id,kind,conversation_id,value FROM archived_routes
        ) SELECT p.*,r.channel_id,r.kind,r.conversation_id,json_extract(r.value,'$.name') AS route_name
        FROM replies p LEFT JOIN destinations r ON r.id=p.route_id ORDER BY p.rowid DESC LIMIT 100""")
        for row in rows:
            channel = row.pop("channel_id")
            kind = row.pop("kind")
            conversation = row.pop("conversation_id")
            if channel and conversation != "*":
                row["address"] = {"account_id": channel, "conversation_id": f"{kind}:{conversation}"}
        return rows

    async def claim_reply(self, identifier: str) -> bool:
        async with self.transaction() as db:
            cursor = await db.execute("UPDATE replies SET state='sending' WHERE id=? AND state='queued'", (identifier,))
            return cursor.rowcount == 1

    async def pending_replies(self) -> list[dict]:
        # A throttled destination must not hide another conversation or passive replies.
        return await self.rows("""WITH waiting AS (
            SELECT p.*,p.rowid AS queue_position,ROW_NUMBER() OVER (
                PARTITION BY r.channel_id,r.kind,r.conversation_id,
                COALESCE(json_extract(q.payload,'$.delivery'),'passive')
                ORDER BY p.rowid
            ) AS position
            FROM replies p LEFT JOIN routes r ON r.id=p.route_id
            LEFT JOIN reply_payloads q ON q.reply_id=p.id WHERE p.state='queued'
        ) SELECT id,route_id,event_id,text,state,message_id,error,created_at
          FROM waiting ORDER BY position,queue_position LIMIT 100""")

    async def claim_qq_push(self, identifier: str, account_id: str, conversation_key: str) -> bool:
        """Reserve conservative proactive attempt budgets and claim without waiting on I/O."""
        now = time.time()
        # QQ's calendar-day quotas use China Standard Time. Keep the rolling minute across midnight.
        day_start = ((now + 8 * 3600) // 86400) * 86400 - 8 * 3600
        cutoff = min(day_start, now - 60)
        async with self.transaction() as db:
            await db.execute("DELETE FROM qq_send_attempts WHERE created_at<?", (cutoff,))
            async with db.execute(
                """SELECT
                    COUNT(*) FILTER (WHERE created_at>?) AS second_count,
                    COUNT(*) FILTER (WHERE created_at>?) AS minute_count,
                    COUNT(*) FILTER (WHERE conversation_key=? AND created_at>?) AS conversation_minute,
                    COUNT(*) FILTER (WHERE conversation_key=? AND created_at>=?) AS conversation_day
                   FROM qq_send_attempts WHERE account_id=?""",
                (now - 1, now - 60, conversation_key, now - 60, conversation_key, day_start, account_id),
            ) as cursor:
                row = await cursor.fetchone()
            assert row is not None
            if (
                row["second_count"] >= 5
                or row["minute_count"] >= 30
                or row["conversation_minute"] >= 20
                or row["conversation_day"] >= 1000
            ):
                return False
            cursor = await db.execute("UPDATE replies SET state='sending' WHERE id=? AND state='queued'", (identifier,))
            if cursor.rowcount != 1:
                return False
            await db.execute(
                "INSERT INTO qq_send_attempts(account_id,conversation_key,created_at) VALUES(?,?,?)",
                (account_id, conversation_key, now),
            )
            return True

    async def finish_reply(
        self, identifier: str, state: str, *, error: str | None = None, message_id: str | None = None
    ) -> None:
        async with self.transaction() as db:
            await db.execute(
                "UPDATE replies SET state=?,error=?,message_id=? WHERE id=?", (state, error, message_id, identifier)
            )

    async def queue_channel_action(self, route: dict, operation: str, arguments: dict, grant: str) -> dict:
        identifier = new_id("action")
        async with self.transaction() as db:
            await db.execute(
                "INSERT INTO channel_actions(id,route_id,operation,arguments,required_grant) VALUES(?,?,?,?,?)",
                (identifier, route["id"], operation, json.dumps(arguments), grant),
            )
        return {"action_id": identifier, "state": "queued"}

    async def pending_channel_actions(self) -> list[dict]:
        return await self.rows("SELECT * FROM channel_actions WHERE state='queued' ORDER BY rowid LIMIT 100")

    async def public_channel_actions(self) -> list[dict]:
        rows = await self.rows("""WITH destinations AS (
            SELECT id,channel_id,kind,conversation_id,value FROM routes
            UNION ALL SELECT id,channel_id,kind,conversation_id,value FROM archived_routes
        ) SELECT a.id,a.route_id,a.operation,a.state,a.error,a.created_at,
            r.channel_id,r.kind,r.conversation_id,json_extract(r.value,'$.name') AS route_name
        FROM channel_actions a LEFT JOIN destinations r ON r.id=a.route_id ORDER BY a.rowid DESC LIMIT 100""")
        for row in rows:
            row["text"] = "[" + row["operation"] + "]"
            channel, kind, conversation = row.pop("channel_id"), row.pop("kind"), row.pop("conversation_id")
            if channel and conversation != "*":
                row["address"] = {"account_id": channel, "conversation_id": f"{kind}:{conversation}"}
        return rows

    async def channel_action(self, identifier: str) -> dict | None:
        rows = await self.rows("SELECT * FROM channel_actions WHERE id=?", (identifier,))
        return rows[0] if rows else None

    async def claim_channel_action(self, identifier: str) -> bool:
        async with self.transaction() as db:
            cursor = await db.execute(
                "UPDATE channel_actions SET state='sending' WHERE id=? AND state='queued'", (identifier,)
            )
            return cursor.rowcount == 1

    async def finish_channel_action(self, identifier: str, state: str, result=None, error: str | None = None) -> None:
        async with self.transaction() as db:
            await db.execute(
                "UPDATE channel_actions SET state=?,result=?,error=? WHERE id=?",
                (state, json.dumps(result) if result is not None else None, error, identifier),
            )

    async def record_notification(
        self, identifier: str, connection: dict, event_type: str, scope_kind: str, scope_id: str, data: dict
    ) -> bool:
        async with self.transaction() as db:
            async with db.execute("SELECT value FROM connections WHERE id=?", (connection["id"],)) as cursor:
                current = await cursor.fetchone()
            if not current or any(
                json.loads(current[0]).get(key) != connection.get(key)
                for key in ("kind", "app_id", "app_secret", "transport", "qq_intents")
            ):
                return False
            cursor = await db.execute(
                "INSERT OR IGNORE INTO channel_notifications(id,channel_id,event_type,scope_kind,scope_id,data) VALUES(?,?,?,?,?,?)",
                (identifier, connection["id"], event_type, scope_kind, scope_id, json.dumps(data)),
            )
            return cursor.rowcount == 1

    async def queue_stream_action(self, route: dict, handle: str | None, arguments: dict, context: dict) -> dict:
        index = arguments["index"]
        action_id = new_id("action")
        async with self.transaction() as db:
            if handle is None:
                if index != 0:
                    raise ValueError("A stream starts at index zero")
                handle = new_id("stream")
                await db.execute(
                    "INSERT INTO stream_sessions(id,route_id,next_index,source_message_id,sender_id,input_closed,reply_context) VALUES(?,?,1,?,?,?,?)",
                    (
                        handle,
                        route["id"],
                        context["message_id"],
                        context["sender_id"],
                        arguments["finished"],
                        json.dumps(context),
                    ),
                )
            else:
                cursor = await db.execute(
                    "UPDATE stream_sessions SET next_index=next_index+1,input_closed=? WHERE id=? AND route_id=? AND next_index=? AND state='open' AND input_closed=0",
                    (arguments["finished"], handle, route["id"], index),
                )
                if cursor.rowcount != 1:
                    raise ValueError("Stream handle or fragment index is invalid")
            await db.execute(
                "INSERT INTO channel_actions(id,route_id,operation,arguments,required_grant) VALUES(?,?,'stream_message',?,'message.send')",
                (action_id, route["id"], json.dumps({**arguments, "stream_handle": handle})),
            )
        return {"action_id": action_id, "state": "queued", "stream_handle": handle}
