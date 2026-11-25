from __future__ import annotations

import pytest

from app.db import Database


async def test_user_scoped_settings_sessions_and_submissions(tmp_path) -> None:
    root = Database(str(tmp_path / "multi.db"))
    await root.connect()
    try:
        alice = root.for_user(101)
        bob = root.for_user(202)
        await alice.set_setting("mode", "a")
        await bob.set_setting("mode", "b")
        assert await alice.get_setting("mode") == "a"
        assert await bob.get_setting("mode") == "b"
        with pytest.raises(RuntimeError, match="root Database"):
            alice.for_user(202)
        with pytest.raises(ValueError, match="positive integer"):
            root.for_user(0)
        with pytest.raises(ValueError, match="positive integer"):
            root.for_user(-1)

        assert await alice.save_session_if_version("a", "{}", 0)
        assert await alice.load_session_versioned() == ("a", "{}", 0)
        # A refresh updates session contents without advancing the login generation.
        assert await alice.save_session_if_version("a-refresh", "{}", 0)
        assert await alice.load_session_versioned() == ("a-refresh", "{}", 0)
        assert not await alice.save_session_if_version("stale", "{}", 1)
        await alice.clear_session()
        assert await alice.load_session_versioned() is None
        # Clearing advances the generation, so a prior login worker cannot restore it.
        assert not await alice.save_session_if_version("stale", "{}", 0)
        assert await alice.save_session("new-login", "{}") == 2
        assert await alice.load_session_versioned() == ("new-login", "{}", 2)
        assert await bob.load_session_versioned() is None
        assert await alice.claim_submission("post:123")
        assert not await alice.claim_submission("post:123")
        assert await bob.claim_submission("post:123")
    finally:
        await root.close()


async def test_registration_and_notifications_are_scoped(tmp_path) -> None:
    root = Database(str(tmp_path / "users.db"))
    await root.connect()
    try:
        await root.register_user(77, 777)
        assert await root.list_users() == [{"user_id": "77", "notification_chat_id": "777"}]
        a, b = root.for_user(77), root.for_user(88)
        assert await a.mark_notified("session", 1)
        assert not await a.mark_notified("session", 1)
        assert await b.mark_notified("session", 1)
    finally:
        await root.close()


async def test_empty_legacy_schema_migrates_without_owner(tmp_path) -> None:
    import aiosqlite

    path = str(tmp_path / "empty-legacy.db")
    conn = await aiosqlite.connect(path)
    await conn.execute("CREATE TABLE settings(key TEXT PRIMARY KEY,value TEXT NOT NULL,updated_at TEXT NOT NULL)")
    await conn.commit()
    await conn.close()

    db = Database(path)
    await db.connect()
    try:
        assert await db.for_user(9).get_setting("missing") is None
        assert await db.for_user(9).set_setting("new", "value") is None
        assert await db.for_user(9).get_setting("new") == "value"
    finally:
        await db.close()


async def test_nonempty_legacy_database_requires_explicit_owner(tmp_path) -> None:
    import aiosqlite

    path = str(tmp_path / "legacy.db")
    conn = await aiosqlite.connect(path)
    await conn.execute("CREATE TABLE settings(key TEXT PRIMARY KEY,value TEXT NOT NULL,updated_at TEXT NOT NULL)")
    await conn.execute("INSERT INTO settings VALUES('x','y','2026-01-01T00:00:00+00:00')")
    await conn.commit()
    await conn.close()

    with pytest.raises(RuntimeError, match="legacy_owner_id"):
        await Database(path).connect()
    db = Database(path, legacy_owner_id=55)
    await db.connect()
    try:
        assert await db.for_user(55).get_setting("x") == "y"
        assert await db.for_user(56).get_setting("x") is None
    finally:
        await db.close()
