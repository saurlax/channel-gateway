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
CREATE TABLE IF NOT EXISTS host_keys (host_id TEXT PRIMARY KEY, token_hash TEXT NOT NULL UNIQUE);
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
CREATE TABLE IF NOT EXISTS observations (
 id TEXT PRIMARY KEY, message TEXT NOT NULL, state TEXT NOT NULL, error TEXT NOT NULL,
 created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE INDEX IF NOT EXISTS event_route ON events(route_id, created_at);
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
        # Retire approval settings without sending historical unreviewed content.
        async with self.transaction() as db:
            await db.execute("UPDATE routes SET value=json_remove(value,'$.auto_reply')")
            await db.execute("UPDATE replies SET state='queued' WHERE state='approved'")
            await db.execute(
                "UPDATE replies SET state='blocked',error='Legacy draft retired; send a new message' WHERE state='draft'"
            )
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
            or {"qq": "websocket", "weixin": "polling", "onebots": "http_reverse"}[connection["kind"]]
        )
        async with self.transaction() as db:
            async with db.execute("SELECT value FROM connections WHERE id=?", (connection["id"],)) as cursor:
                row = await cursor.fetchone()
            current = json.loads(row[0]) if row else None
            keys = ("kind", "transport", "api_url", "token", "webhook_secret", "app_id", "app_secret")
            if not current or any(current.get(key) != connection.get(key) for key in keys):
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
        value.setdefault("access_mode", "restricted")
        value.setdefault("parent_route_id", None)
        return value

    async def routes(self) -> list[dict]:
        rows = await self.rows(
            "SELECT r.*,EXISTS(SELECT 1 FROM connections c WHERE c.id=r.channel_id) "
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
                    for key in ("sender_policy", "allowed_user_ids", "access_mode", "reply_source"):
                        row[key] = parent[key]
                    row["enabled"] = row["enabled"] and parent["enabled"]
                else:
                    row["enabled"] = False
        return routes

    async def route(self, identifier: str) -> dict | None:
        return next((row for row in await self.routes() if row["id"] == identifier), None)

    async def route_for_token(self, token: str) -> dict | None:
        rows = await self.rows("SELECT * FROM routes WHERE token_hash=?", (token_hash(token),))
        return await self.route(rows[0]["id"]) if rows else None

    async def add_route(self, value: dict) -> tuple[dict, str]:
        value = {**value, "id": new_id("route")}
        token = secrets.token_urlsafe(32)
        async with self.transaction() as db:
            async with db.execute(
                "SELECT COUNT(*) FROM connections WHERE id IN (?,?)", (value["channel_id"], value["host_id"])
            ) as cursor:
                found = await cursor.fetchone()
                if found is None or found[0] != 2:
                    raise ValueError("Route connections no longer exist")
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

    async def conversation_route(self, parent: dict, conversation_id: str) -> dict:
        current = await self.route(parent["id"])
        if not current or current["conversation_id"] != "*":
            raise ValueError("A current all-conversation route is required")
        parent = current
        value = {**parent, "id": new_id("route"), "conversation_id": conversation_id, "parent_route_id": parent["id"]}
        value.pop("thread_id", None)
        async with self.transaction() as db:
            async with db.execute("SELECT value FROM routes WHERE id=?", (parent["id"],)) as cursor:
                current = await cursor.fetchone()
                if current is None or not json.loads(current[0])["enabled"]:
                    raise ValueError("All-conversation route is no longer enabled")
            await db.execute(
                "INSERT OR IGNORE INTO routes (id,channel_id,kind,conversation_id,value,token_hash) VALUES (?,?,?,?,?,?)",
                (
                    value["id"],
                    value["channel_id"],
                    value["conversation_kind"],
                    conversation_id,
                    json.dumps(value),
                    token_hash(secrets.token_urlsafe(32)),
                ),
            )
        return next(
            row
            for row in await self.routes()
            if row["channel_id"] == parent["channel_id"]
            and row["conversation_kind"] == parent["conversation_kind"]
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
        for row in rows:
            await self.conversation_route(parent, row["conversation_id"])

    async def update_route(self, identifier: str, value: dict, expected: dict) -> bool:
        expected = {key: item for key, item in expected.items() if key != "thread_id"}
        async with self.transaction() as db:
            async with db.execute(
                "SELECT COUNT(*) FROM connections WHERE id IN (?,?)", (value["channel_id"], value["host_id"])
            ) as cursor:
                found = await cursor.fetchone()
                if found is None or found[0] != 2:
                    return False
            async with db.execute("SELECT * FROM routes WHERE id=?", (identifier,)) as cursor:
                row = await cursor.fetchone()
            if (
                not row
                or {key: item for key, item in self.route_view(dict(row)).items() if key != "thread_id"} != expected
            ):
                return False
            await db.execute("UPDATE routes SET value=? WHERE id=?", (json.dumps(value), identifier))
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
            row["message"] = json.loads(row["message"])
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
                row["message"] = json.loads(row["message"])
                row.update(route_id=None, thread_id=None, run_id=None)
            rows = sorted(rows + observations, key=lambda row: row["created_at"], reverse=True)[:limit]
        return rows

    async def pending(self) -> list[dict]:
        # Serializing each conversation keeps run output attributable to one input.
        return await self.rows("""SELECT e.* FROM events e WHERE e.state IN ('queued','submitted') AND NOT EXISTS (
            SELECT 1 FROM events older WHERE older.route_id=e.route_id AND older.state IN ('queued','submitted')
            AND older.rowid<e.rowid) ORDER BY e.rowid LIMIT 100""")

    async def received_messages(self, route: dict, *, limit: int = 20) -> list[dict]:
        """Read source history under current access, independently of past delivery."""
        params: tuple = (route["id"], route["channel_id"], f"{route['conversation_kind']}:{route['conversation_id']}")
        sender_filter = ""
        if route.get("sender_policy", "allowlist") != "any":
            sender_filter = " AND json_extract(message,'$.sender_id') IN (SELECT value FROM json_each(?))"
            params += (json.dumps(route["allowed_user_ids"]),)
        rows = await self.rows(
            """WITH received AS (
                SELECT e.id,e.message,e.created_at,
                    COALESCE(json_extract(e.message,'$.address.account_id'),r.channel_id) AS channel_id,
                    COALESCE(json_extract(e.message,'$.address.conversation_id'),r.kind||':'||r.conversation_id) AS conversation_id
                FROM events e JOIN routes r ON r.id=e.route_id WHERE e.route_id=?
                UNION ALL
                SELECT id,message,created_at,json_extract(message,'$.address.account_id'),
                    json_extract(message,'$.address.conversation_id') FROM observations
            ) SELECT message,channel_id,conversation_id FROM received WHERE channel_id=? AND conversation_id=?"""
            + sender_filter
            + " ORDER BY created_at DESC,id DESC LIMIT ?",
            (*params, max(1, min(limit, 100))),
        )
        messages = []
        for row in reversed(rows):
            message = json.loads(row["message"])
            message.setdefault("address", {"account_id": row["channel_id"], "conversation_id": row["conversation_id"]})
            messages.append(message)
        return messages

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
                SELECT p.id,p.route_id,'outgoing',p.text,NULL,p.state,p.error,p.created_at,r.thread_id,NULL,
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

    async def queue_reply(self, route: dict, text: str, *, event_id: str | None = None) -> dict:
        row = {"id": new_id("reply"), "route_id": route["id"], "event_id": event_id, "text": text, "state": "queued"}
        async with self.transaction() as db:
            await db.execute(
                "INSERT OR IGNORE INTO replies(id,route_id,event_id,text,state) VALUES (?,?,?,?,?)",
                tuple(row[key] for key in ["id", "route_id", "event_id", "text", "state"]),
            )
            if event_id:
                await db.execute("UPDATE events SET state='completed',error=NULL WHERE id=?", (event_id,))
        return row

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

    async def finish_reply(
        self, identifier: str, state: str, *, error: str | None = None, message_id: str | None = None
    ) -> None:
        async with self.transaction() as db:
            await db.execute(
                "UPDATE replies SET state=?,error=?,message_id=? WHERE id=?", (state, error, message_id, identifier)
            )
