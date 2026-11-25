from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import aiosqlite

from app.utils.time import iso_now, parse_iso


# All account data is keyed by user_id. Keep the DDL centralized to make it
# impossible for a newly created table to accidentally omit the tenant key.
SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    user_id TEXT PRIMARY KEY,
    notification_chat_id TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS settings (
    user_id TEXT NOT NULL, key TEXT NOT NULL, value TEXT NOT NULL,
    updated_at TEXT NOT NULL, PRIMARY KEY (user_id, key)
);
CREATE TABLE IF NOT EXISTS ethol_session (
    user_id TEXT PRIMARY KEY, cookies_enc TEXT, profile_json TEXT NOT NULL DEFAULT '{}',
    version INTEGER NOT NULL DEFAULT 0, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS course_cache (
    user_id TEXT NOT NULL, tahun INTEGER NOT NULL, semester INTEGER NOT NULL,
    payload TEXT NOT NULL, updated_at TEXT NOT NULL,
    PRIMARY KEY (user_id, tahun, semester)
);
CREATE TABLE IF NOT EXISTS schedule_cache (
    user_id TEXT NOT NULL, tahun INTEGER NOT NULL, semester INTEGER NOT NULL,
    tanggal TEXT NOT NULL, payload TEXT NOT NULL, updated_at TEXT NOT NULL,
    PRIMARY KEY (user_id, tahun, semester, tanggal)
);
CREATE TABLE IF NOT EXISTS attendance_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL,
    tanggal TEXT NOT NULL, kuliah INTEGER NOT NULL, jenis_schema INTEGER NOT NULL,
    session_key TEXT NOT NULL, status TEXT NOT NULL, pesan TEXT, created_at TEXT NOT NULL,
    UNIQUE (user_id, kuliah, session_key)
);
CREATE INDEX IF NOT EXISTS idx_attendance_user_tanggal ON attendance_log(user_id, tanggal);
CREATE TABLE IF NOT EXISTS day_status (
    user_id TEXT NOT NULL, tanggal TEXT NOT NULL, kuliah INTEGER NOT NULL,
    jenis_schema INTEGER NOT NULL, status TEXT NOT NULL, note TEXT, created_at TEXT NOT NULL,
    PRIMARY KEY (user_id, tanggal, kuliah, jenis_schema)
);
CREATE TABLE IF NOT EXISTS notified_sessions (
    user_id TEXT NOT NULL, session_key TEXT NOT NULL, kuliah INTEGER NOT NULL,
    notified_at TEXT NOT NULL, PRIMARY KEY (user_id, session_key)
);
CREATE TABLE IF NOT EXISTS submission_claims (
    user_id TEXT NOT NULL, claim_key TEXT NOT NULL, claimed_at TEXT NOT NULL,
    PRIMARY KEY (user_id, claim_key)
);
"""

LEGACY_TABLES = {"settings", "ethol_session", "course_cache", "schedule_cache", "attendance_log", "day_status", "notified_sessions"}


class Database:
    def __init__(self, path: str, legacy_owner_id: str | int | None = None) -> None:
        self.path = path
        self.legacy_owner_id = str(legacy_owner_id) if legacy_owner_id is not None else None
        self.user_id: str | None = None
        self._root: Database = self
        self._conn: aiosqlite.Connection | None = None
        self._lock = asyncio.Lock()

    def for_user(self, user_id: str | int) -> Database:
        # Scoped repositories are immutable in tenant identity; only the root may
        # create them. This prevents a caller holding one user's repository from
        # accidentally or deliberately re-scoping it to another account.
        if self is not self._root or self.user_id is not None:
            raise RuntimeError("for_user() must be called on the root Database")
        if self._conn is None:
            raise RuntimeError("Database belum terhubung")
        try:
            normalized_user_id = int(user_id)
        except (TypeError, ValueError) as exc:
            raise ValueError("user_id must be a positive integer") from exc
        if normalized_user_id <= 0:
            raise ValueError("user_id must be a positive integer")
        child = object.__new__(Database)
        child.path = self.path
        child.legacy_owner_id = self.legacy_owner_id
        child.user_id = str(normalized_user_id)
        child._root = self._root
        child._conn = self._conn
        child._lock = self._lock
        return child

    def _uid(self) -> str:
        # Owner 0 only preserves the historical unscoped API for older callers;
        # production runtime should always use for_user().
        return self.user_id if self.user_id is not None else (self.legacy_owner_id or "0")

    async def connect(self) -> None:
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(self.path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA journal_mode=WAL")
        await self._conn.execute("PRAGMA busy_timeout=5000")
        await self._conn.execute("PRAGMA foreign_keys=ON")
        # Detect old schema before creating anything; ownership must never be guessed.
        tables = {row[0] for row in await (await self._conn.execute("SELECT name FROM sqlite_master WHERE type='table'")).fetchall()}
        legacy_present = bool(tables & LEGACY_TABLES)
        if legacy_present:
            old_tables = tables & LEGACY_TABLES
            counts = {}
            for table in old_tables:
                counts[table] = (await (await self._conn.execute(f'SELECT COUNT(*) FROM "{table}"')).fetchone())[0]
            nonempty = any(counts.values())
            # Already tenant-scoped DBs have user_id columns; do not mistake for legacy.
            scoped = True
            for table in old_tables:
                columns = await (await self._conn.execute(f'PRAGMA table_info("{table}")')).fetchall()
                if "user_id" not in {row[1] for row in columns}:
                    scoped = False
                    break
            if not scoped:
                if nonempty and self.legacy_owner_id is None:
                    await self._conn.close(); self._conn = None
                    raise RuntimeError("Legacy database contains account data; set legacy_owner_id explicitly to assign ownership")
                await self._migrate_legacy(old_tables)
        await self._create_schema()
        # Existing session rows are retained as generation tombstones.
        await self._conn.commit()

    async def _create_schema(self) -> None:
        conn = self._require_conn()
        statements = [statement.strip() for statement in SCHEMA.split(";") if statement.strip()]
        for statement in statements:
            await conn.execute(statement)
        await conn.commit()

    async def _migrate_legacy(self, old_tables: set[str]) -> None:
        conn = self._require_conn()
        uid = self.legacy_owner_id
        # Empty legacy schemas have no data to assign and can migrate without an owner.
        if uid is None:
            uid = "0"
        await conn.execute("BEGIN IMMEDIATE")
        try:
            for table in old_tables:
                count = (await (await conn.execute(f'SELECT COUNT(*) FROM "{table}"')).fetchone())[0]
                if count:
                    if self.legacy_owner_id is None:
                        raise RuntimeError("Legacy database contains account data; set legacy_owner_id explicitly to assign ownership")
                    break
            await conn.execute("CREATE TABLE IF NOT EXISTS users (user_id TEXT PRIMARY KEY, notification_chat_id TEXT, created_at TEXT NOT NULL)")
            await conn.execute("INSERT OR IGNORE INTO users(user_id,notification_chat_id,created_at) VALUES (?, NULL, ?)", (uid, iso_now()))
            for table in old_tables:
                await conn.execute(f'ALTER TABLE "{table}" RENAME TO "legacy_{table}"')
            # Execute statements individually: executescript implicitly commits SQLite
            # transactions, which would make this migration non-atomic.
            for statement in (part.strip() for part in SCHEMA.split(";") if part.strip()):
                await conn.execute(statement)
            for table in sorted(old_tables):
                count = (await (await conn.execute(f'SELECT COUNT(*) FROM "legacy_{table}"')).fetchone())[0]
                if not count:
                    await conn.execute(f'DROP TABLE "legacy_{table}"')
                    continue
                if table == "ethol_session":
                    rows = await (await conn.execute("SELECT cookies_enc, profile_json, updated_at FROM legacy_ethol_session LIMIT 1")).fetchall()
                    for row in rows:
                        await conn.execute("INSERT INTO ethol_session(user_id,cookies_enc,profile_json,version,updated_at) VALUES(?,?,?,?,?)", (uid,row[0],row[1],1,row[2]))
                elif table == "settings":
                    await conn.execute("INSERT INTO settings SELECT ?,key,value,updated_at FROM legacy_settings", (uid,))
                elif table == "course_cache":
                    await conn.execute("INSERT INTO course_cache SELECT ?,tahun,semester,payload,updated_at FROM legacy_course_cache", (uid,))
                elif table == "schedule_cache":
                    await conn.execute("INSERT INTO schedule_cache SELECT ?,tahun,semester,tanggal,payload,updated_at FROM legacy_schedule_cache", (uid,))
                elif table == "attendance_log":
                    await conn.execute("INSERT INTO attendance_log(id,user_id,tanggal,kuliah,jenis_schema,session_key,status,pesan,created_at) SELECT id,?,tanggal,kuliah,jenis_schema,session_key,status,pesan,created_at FROM legacy_attendance_log", (uid,))
                elif table == "day_status":
                    await conn.execute("INSERT INTO day_status SELECT ?,tanggal,kuliah,jenis_schema,status,note,created_at FROM legacy_day_status", (uid,))
                elif table == "notified_sessions":
                    await conn.execute("INSERT INTO notified_sessions SELECT ?,session_key,kuliah,notified_at FROM legacy_notified_sessions", (uid,))
                await conn.execute(f'DROP TABLE "legacy_{table}"')
            await conn.commit()
        except BaseException:
            await conn.rollback()
            raise

    async def close(self) -> None:
        if self._conn is not None and self is self._root:
            await self._conn.close()
            self._conn = None

    def _require_conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("Database belum terhubung")
        return self._conn

    async def _execute(self, sql: str, params: tuple = ()) -> aiosqlite.Cursor:
        conn = self._require_conn()
        async with self._lock:
            cursor = await conn.execute(sql, params)
            await conn.commit()
            return cursor

    async def _fetchone(self, sql: str, params: tuple = ()) -> aiosqlite.Row | None:
        async with self._lock:
            return await (await self._require_conn().execute(sql, params)).fetchone()

    async def _fetchall(self, sql: str, params: tuple = ()) -> list[aiosqlite.Row]:
        async with self._lock:
            return await (await self._require_conn().execute(sql, params)).fetchall()

    async def register_user(self, user_id: str | int, notification_chat_id: str | int | None = None) -> None:
        uid = str(user_id)
        await self._execute("INSERT INTO users(user_id,notification_chat_id,created_at) VALUES(?,?,?) ON CONFLICT(user_id) DO UPDATE SET notification_chat_id=excluded.notification_chat_id", (uid, str(notification_chat_id) if notification_chat_id is not None else None, iso_now()))

    async def list_users(self) -> list[dict[str, Any]]:
        return [dict(r) for r in await self._fetchall("SELECT user_id,notification_chat_id FROM users ORDER BY user_id")]

    # settings
    async def get_setting(self, key: str, default: str | None = None) -> str | None:
        row = await self._fetchone("SELECT value FROM settings WHERE user_id=? AND key=?", (self._uid(), key))
        return row["value"] if row else default

    async def set_setting(self, key: str, value: str) -> None:
        await self._execute("INSERT INTO settings(user_id,key,value,updated_at) VALUES(?,?,?,?) ON CONFLICT(user_id,key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at", (self._uid(), key, value, iso_now()))

    async def get_bool_setting(self, key: str, default: bool = False) -> bool:
        value = await self.get_setting(key)
        return default if value is None else value.lower() in {"1", "true", "yes", "on"}

    async def set_bool_setting(self, key: str, value: bool) -> None:
        await self.set_setting(key, "1" if value else "0")

    # session generation CAS; clear preserves generation to prevent stale writers.
    async def save_session(self, cookies_enc: str, profile_json: str) -> int:
        uid = self._uid()
        await self._execute("INSERT INTO ethol_session(user_id,cookies_enc,profile_json,version,updated_at) VALUES(?,?,?,1,?) ON CONFLICT(user_id) DO UPDATE SET cookies_enc=excluded.cookies_enc,profile_json=excluded.profile_json,version=ethol_session.version+1,updated_at=excluded.updated_at", (uid,cookies_enc,profile_json,iso_now()))
        row = await self._fetchone("SELECT version FROM ethol_session WHERE user_id=?", (uid,))
        return int(row["version"])

    async def save_session_if_version(self, cookies_enc: str, profile_json: str, expected_version: int) -> bool:
        uid = self._uid()
        # Version 0 denotes no previously-existing session.
        await self._execute("INSERT OR IGNORE INTO ethol_session(user_id,cookies_enc,profile_json,version,updated_at) VALUES(?,NULL,'{}',0,?)", (uid,iso_now()))
        cur = await self._execute("UPDATE ethol_session SET cookies_enc=?,profile_json=?,updated_at=? WHERE user_id=? AND version=?", (cookies_enc,profile_json,iso_now(),uid,expected_version))
        return cur.rowcount == 1

    async def load_session_versioned(self) -> tuple[str, str, int] | None:
        row = await self._fetchone("SELECT cookies_enc,profile_json,version FROM ethol_session WHERE user_id=? AND cookies_enc IS NOT NULL", (self._uid(),))
        return (row["cookies_enc"], row["profile_json"], int(row["version"])) if row else None

    async def load_session(self) -> tuple[str, str] | None:
        session = await self.load_session_versioned()
        return session[:2] if session else None

    async def clear_session(self) -> None:
        uid=self._uid()
        await self._execute("INSERT OR IGNORE INTO ethol_session(user_id,cookies_enc,profile_json,version,updated_at) VALUES(?,NULL,'{}',0,?)", (uid,iso_now()))
        await self._execute("UPDATE ethol_session SET cookies_enc=NULL,profile_json='{}',version=version+1,updated_at=? WHERE user_id=?", (iso_now(),uid))

    # cache; expiry is enforced at read time.
    async def save_courses(self, tahun: int, semester: int, payload: list[dict]) -> None:
        await self._execute("INSERT INTO course_cache(user_id,tahun,semester,payload,updated_at) VALUES(?,?,?,?,?) ON CONFLICT(user_id,tahun,semester) DO UPDATE SET payload=excluded.payload,updated_at=excluded.updated_at", (self._uid(),tahun,semester,json.dumps(payload,ensure_ascii=False),iso_now()))

    async def load_courses(self, tahun: int, semester: int, ttl_seconds: int = 86400) -> list[dict] | None:
        row=await self._fetchone("SELECT payload,updated_at FROM course_cache WHERE user_id=? AND tahun=? AND semester=?",(self._uid(),tahun,semester))
        if not row or (ttl_seconds >= 0 and (parse_iso(iso_now())-parse_iso(row['updated_at'])).total_seconds() > ttl_seconds): return None
        return json.loads(row['payload'])

    async def save_schedule(self, tahun: int, semester: int, tanggal: str, payload: list[dict]) -> None:
        await self._execute("INSERT INTO schedule_cache(user_id,tahun,semester,tanggal,payload,updated_at) VALUES(?,?,?,?,?,?) ON CONFLICT(user_id,tahun,semester,tanggal) DO UPDATE SET payload=excluded.payload,updated_at=excluded.updated_at", (self._uid(),tahun,semester,tanggal,json.dumps(payload,ensure_ascii=False),iso_now()))

    async def load_schedule(self, tahun: int, semester: int, tanggal: str, ttl_seconds: int = 21600) -> list[dict] | None:
        row=await self._fetchone("SELECT payload,updated_at FROM schedule_cache WHERE user_id=? AND tahun=? AND semester=? AND tanggal=?",(self._uid(),tahun,semester,tanggal))
        if not row or (ttl_seconds >= 0 and (parse_iso(iso_now())-parse_iso(row['updated_at'])).total_seconds() > ttl_seconds): return None
        return json.loads(row['payload'])

    # attendance
    async def log_attendance(self, tanggal: str, kuliah: int, jenis_schema: int, session_key: str, status: str, pesan: str | None = None) -> bool:
        cur=await self._execute("INSERT INTO attendance_log(user_id,tanggal,kuliah,jenis_schema,session_key,status,pesan,created_at) VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(user_id,kuliah,session_key) DO UPDATE SET status=excluded.status,pesan=excluded.pesan,created_at=excluded.created_at",(self._uid(),tanggal,kuliah,jenis_schema,session_key,status,pesan,iso_now()))
        return cur.rowcount > 0

    async def has_attendance(self, tanggal: str, kuliah: int) -> bool:
        return await self._fetchone("SELECT 1 FROM attendance_log WHERE user_id=? AND tanggal=? AND kuliah=? AND status='hadir' LIMIT 1",(self._uid(),tanggal,kuliah)) is not None

    async def has_attendance_key(self, tanggal: str, kuliah: int, session_key: str) -> bool:
        return await self._fetchone("SELECT 1 FROM attendance_log WHERE user_id=? AND tanggal=? AND kuliah=? AND session_key=? AND status='hadir' LIMIT 1",(self._uid(),tanggal,kuliah,session_key)) is not None

    async def has_attendance_key_any_date(self, kuliah: int, session_key: str) -> bool:
        return await self._fetchone("SELECT 1 FROM attendance_log WHERE user_id=? AND kuliah=? AND session_key=? AND status='hadir' LIMIT 1",(self._uid(),kuliah,session_key)) is not None

    async def recent_logs(self, limit: int = 10) -> list[dict[str, Any]]:
        return [dict(r) for r in await self._fetchall("SELECT * FROM attendance_log WHERE user_id=? ORDER BY id DESC LIMIT ?",(self._uid(),limit))]

    async def set_day_status(self, tanggal: str, kuliah: int, jenis_schema: int, status: str, note: str | None = None) -> None:
        await self._execute("INSERT INTO day_status(user_id,tanggal,kuliah,jenis_schema,status,note,created_at) VALUES(?,?,?,?,?,?,?) ON CONFLICT(user_id,tanggal,kuliah,jenis_schema) DO UPDATE SET status=excluded.status,note=excluded.note,created_at=excluded.created_at",(self._uid(),tanggal,kuliah,jenis_schema,status,note,iso_now()))

    async def get_day_status(self, tanggal: str, kuliah: int) -> str | None:
        row=await self._fetchone("SELECT status FROM day_status WHERE user_id=? AND tanggal=? AND kuliah=? ORDER BY jenis_schema DESC LIMIT 1",(self._uid(),tanggal,kuliah))
        return row["status"] if row else None

    async def clear_day_status(self, tanggal: str, kuliah: int) -> None:
        await self._execute("DELETE FROM day_status WHERE user_id=? AND tanggal=? AND kuliah=?",(self._uid(),tanggal,kuliah))

    async def mark_notified(self, session_key: str, kuliah: int, cooldown_seconds: int = 600) -> bool:
        uid=self._uid(); now=iso_now()
        async with self._lock:
            conn=self._require_conn()
            await conn.execute("BEGIN IMMEDIATE")
            try:
                row=await (await conn.execute("SELECT notified_at FROM notified_sessions WHERE user_id=? AND session_key=?",(uid,session_key))).fetchone()
                if row and cooldown_seconds > 0 and (parse_iso(now)-parse_iso(row['notified_at'])).total_seconds() < cooldown_seconds:
                    await conn.rollback(); return False
                await conn.execute("INSERT INTO notified_sessions(user_id,session_key,kuliah,notified_at) VALUES(?,?,?,?) ON CONFLICT(user_id,session_key) DO UPDATE SET kuliah=excluded.kuliah,notified_at=excluded.notified_at",(uid,session_key,kuliah,now))
                await conn.commit(); return True
            except BaseException:
                await conn.rollback(); raise

    async def claim_submission(self, claim_key: str) -> bool:
        cur=await self._execute("INSERT OR IGNORE INTO submission_claims(user_id,claim_key,claimed_at) VALUES(?,?,?)",(self._uid(),claim_key,iso_now()))
        return cur.rowcount == 1
