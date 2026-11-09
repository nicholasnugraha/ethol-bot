from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import aiosqlite

from app.utils.time import iso_now, parse_iso

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ethol_session (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    cookies_enc TEXT NOT NULL,
    profile_json TEXT NOT NULL DEFAULT '{}',
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS course_cache (
    tahun INTEGER NOT NULL,
    semester INTEGER NOT NULL,
    payload TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (tahun, semester)
);

CREATE TABLE IF NOT EXISTS schedule_cache (
    tahun INTEGER NOT NULL,
    semester INTEGER NOT NULL,
    tanggal TEXT NOT NULL,
    payload TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (tahun, semester, tanggal)
);

CREATE TABLE IF NOT EXISTS attendance_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tanggal TEXT NOT NULL,
    kuliah INTEGER NOT NULL,
    jenis_schema INTEGER NOT NULL,
    session_key TEXT NOT NULL,
    status TEXT NOT NULL,
    pesan TEXT,
    created_at TEXT NOT NULL,
    UNIQUE (kuliah, session_key)
);

CREATE INDEX IF NOT EXISTS idx_attendance_tanggal
    ON attendance_log(tanggal);

CREATE TABLE IF NOT EXISTS day_status (
    tanggal TEXT NOT NULL,
    kuliah INTEGER NOT NULL,
    jenis_schema INTEGER NOT NULL,
    status TEXT NOT NULL,
    note TEXT,
    created_at TEXT NOT NULL,
    PRIMARY KEY (tanggal, kuliah, jenis_schema)
);

CREATE TABLE IF NOT EXISTS notified_sessions (
    session_key TEXT PRIMARY KEY,
    kuliah INTEGER NOT NULL,
    notified_at TEXT NOT NULL
);
"""


class Database:
    def __init__(self, path: str) -> None:
        self.path = path
        self._conn: aiosqlite.Connection | None = None
        self._lock = asyncio.Lock()

    async def connect(self) -> None:
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(self.path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA journal_mode=WAL")
        await self._conn.execute("PRAGMA busy_timeout=5000")
        await self._conn.execute("PRAGMA foreign_keys=ON")
        await self._conn.executescript(SCHEMA)
        await self._conn.commit()

    async def close(self) -> None:
        if self._conn is not None:
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
        conn = self._require_conn()
        async with self._lock:
            cursor = await conn.execute(sql, params)
            return await cursor.fetchone()

    async def _fetchall(self, sql: str, params: tuple = ()) -> list[aiosqlite.Row]:
        conn = self._require_conn()
        async with self._lock:
            cursor = await conn.execute(sql, params)
            return await cursor.fetchall()

    # ---- settings -------------------------------------------------------
    async def get_setting(self, key: str, default: str | None = None) -> str | None:
        row = await self._fetchone("SELECT value FROM settings WHERE key = ?", (key,))
        return row["value"] if row else default

    async def set_setting(self, key: str, value: str) -> None:
        now = iso_now()
        await self._execute(
            """
            INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
            """,
            (key, value, now),
        )

    async def get_bool_setting(self, key: str, default: bool = False) -> bool:
        value = await self.get_setting(key)
        if value is None:
            return default
        return value.lower() in {"1", "true", "yes", "on"}

    async def set_bool_setting(self, key: str, value: bool) -> None:
        await self.set_setting(key, "1" if value else "0")

    # ---- session --------------------------------------------------------
    async def save_session(self, cookies_enc: str, profile_json: str) -> None:
        await self._execute(
            """
            INSERT INTO ethol_session (id, cookies_enc, profile_json, updated_at)
            VALUES (1, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                cookies_enc = excluded.cookies_enc,
                profile_json = excluded.profile_json,
                updated_at = excluded.updated_at
            """,
            (cookies_enc, profile_json, iso_now()),
        )

    async def load_session(self) -> tuple[str, str] | None:
        row = await self._fetchone(
            "SELECT cookies_enc, profile_json FROM ethol_session WHERE id = 1"
        )
        if not row:
            return None
        return row["cookies_enc"], row["profile_json"]

    async def clear_session(self) -> None:
        await self._execute("DELETE FROM ethol_session WHERE id = 1")

    # ---- caches ---------------------------------------------------------
    async def save_courses(self, tahun: int, semester: int, payload: list[dict]) -> None:
        await self._execute(
            """
            INSERT INTO course_cache (tahun, semester, payload, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(tahun, semester) DO UPDATE SET
                payload = excluded.payload,
                updated_at = excluded.updated_at
            """,
            (tahun, semester, json.dumps(payload, ensure_ascii=False), iso_now()),
        )

    async def load_courses(self, tahun: int, semester: int) -> list[dict] | None:
        row = await self._fetchone(
            "SELECT payload FROM course_cache WHERE tahun = ? AND semester = ?",
            (tahun, semester),
        )
        if not row:
            return None
        return json.loads(row["payload"])

    async def save_schedule(
        self,
        tahun: int,
        semester: int,
        tanggal: str,
        payload: list[dict],
    ) -> None:
        await self._execute(
            """
            INSERT INTO schedule_cache (tahun, semester, tanggal, payload, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(tahun, semester, tanggal) DO UPDATE SET
                payload = excluded.payload,
                updated_at = excluded.updated_at
            """,
            (tahun, semester, tanggal, json.dumps(payload, ensure_ascii=False), iso_now()),
        )

    async def load_schedule(
        self,
        tahun: int,
        semester: int,
        tanggal: str,
    ) -> list[dict] | None:
        row = await self._fetchone(
            """
            SELECT payload FROM schedule_cache
            WHERE tahun = ? AND semester = ? AND tanggal = ?
            """,
            (tahun, semester, tanggal),
        )
        if not row:
            return None
        return json.loads(row["payload"])

    # ---- attendance log -------------------------------------------------
    async def log_attendance(
        self,
        tanggal: str,
        kuliah: int,
        jenis_schema: int,
        session_key: str,
        status: str,
        pesan: str | None = None,
    ) -> bool:
        cursor = await self._execute(
            """
            INSERT INTO attendance_log
                (tanggal, kuliah, jenis_schema, session_key, status, pesan, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(kuliah, session_key) DO UPDATE SET
                status = excluded.status,
                pesan = excluded.pesan,
                created_at = excluded.created_at
            """,
            (tanggal, kuliah, jenis_schema, session_key, status, pesan, iso_now()),
        )
        return cursor.rowcount > 0

    async def has_attendance(self, tanggal: str, kuliah: int) -> bool:
        row = await self._fetchone(
            """
            SELECT 1 FROM attendance_log
            WHERE tanggal = ? AND kuliah = ? AND status = 'hadir' LIMIT 1
            """,
            (tanggal, kuliah),
        )
        return row is not None

    async def has_attendance_key(
        self,
        tanggal: str,
        kuliah: int,
        session_key: str,
    ) -> bool:
        row = await self._fetchone(
            """
            SELECT 1 FROM attendance_log
            WHERE tanggal = ? AND kuliah = ? AND session_key = ? AND status = 'hadir'
            LIMIT 1
            """,
            (tanggal, kuliah, session_key),
        )
        return row is not None

    async def has_attendance_key_any_date(self, kuliah: int, session_key: str) -> bool:
        row = await self._fetchone(
            """
            SELECT 1 FROM attendance_log
            WHERE kuliah = ? AND session_key = ? AND status = 'hadir'
            LIMIT 1
            """,
            (kuliah, session_key),
        )
        return row is not None

    async def recent_logs(self, limit: int = 10) -> list[dict[str, Any]]:
        rows = await self._fetchall(
            "SELECT * FROM attendance_log ORDER BY id DESC LIMIT ?", (limit,)
        )
        return [dict(row) for row in rows]

    # ---- day status -----------------------------------------------------
    async def set_day_status(
        self,
        tanggal: str,
        kuliah: int,
        jenis_schema: int,
        status: str,
        note: str | None = None,
    ) -> None:
        await self._execute(
            """
            INSERT INTO day_status (tanggal, kuliah, jenis_schema, status, note, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(tanggal, kuliah, jenis_schema) DO UPDATE SET
                status = excluded.status,
                note = excluded.note,
                created_at = excluded.created_at
            """,
            (tanggal, kuliah, jenis_schema, status, note, iso_now()),
        )

    async def get_day_status(self, tanggal: str, kuliah: int) -> str | None:
        row = await self._fetchone(
            "SELECT status FROM day_status WHERE tanggal = ? AND kuliah = ?",
            (tanggal, kuliah),
        )
        return row["status"] if row else None

    async def clear_day_status(self, tanggal: str, kuliah: int) -> None:
        await self._execute(
            "DELETE FROM day_status WHERE tanggal = ? AND kuliah = ?",
            (tanggal, kuliah),
        )

    # ---- notification dedup ---------------------------------------------
    async def mark_notified(
        self,
        session_key: str,
        kuliah: int,
        cooldown_seconds: int = 600,
    ) -> bool:
        """Return True only if this session should be notified now."""
        row = await self._fetchone(
            "SELECT notified_at FROM notified_sessions WHERE session_key = ?",
            (session_key,),
        )
        now = iso_now()
        if row:
            if cooldown_seconds > 0:
                age = (parse_iso(now) - parse_iso(row["notified_at"])).total_seconds()
                if age < cooldown_seconds:
                    return False
            await self._execute(
                "UPDATE notified_sessions SET notified_at = ? WHERE session_key = ?",
                (now, session_key),
            )
            return True

        await self._execute(
            "INSERT INTO notified_sessions (session_key, kuliah, notified_at) VALUES (?, ?, ?)",
            (session_key, kuliah, now),
        )
        return True
