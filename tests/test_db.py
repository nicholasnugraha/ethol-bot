from __future__ import annotations

from app.db import Database


async def test_settings(tmp_path) -> None:
    db = Database(str(tmp_path / "test.db"))
    await db.connect()
    try:
        assert await db.get_setting("x") is None
        assert await db.get_bool_setting("x", True) is True
        await db.set_setting("x", "1")
        assert await db.get_bool_setting("x") is True
        await db.set_bool_setting("x", False)
        assert await db.get_bool_setting("x") is False
    finally:
        await db.close()


async def test_session_roundtrip(tmp_path) -> None:
    db = Database(str(tmp_path / "test.db"))
    await db.connect()
    try:
        assert await db.load_session() is None
        await db.save_session("enc-cookies", '{"nomor": 1}')
        assert await db.load_session() == ("enc-cookies", '{"nomor": 1}')
        await db.save_session("enc-cookies-2", '{"nomor": 2}')
        assert await db.load_session() == ("enc-cookies-2", '{"nomor": 2}')
        await db.clear_session()
        assert await db.load_session() is None
    finally:
        await db.close()


async def test_course_and_schedule_cache(tmp_path) -> None:
    db = Database(str(tmp_path / "test.db"))
    await db.connect()
    try:
        await db.save_courses(2026, 1, [{"nomor": 1, "nama": "MK"}])
        assert await db.load_courses(2026, 1) == [{"nomor": 1, "nama": "MK"}]
        assert await db.load_courses(2025, 1) is None

        await db.save_schedule(2026, 1, "2026-11-02", [{"kuliah": 1}])
        assert await db.load_schedule(2026, 1, "2026-11-02") == [{"kuliah": 1}]
        assert await db.load_schedule(2026, 1, "2026-11-03") is None
    finally:
        await db.close()


async def test_attendance_and_day_status(tmp_path) -> None:
    db = Database(str(tmp_path / "test.db"))
    await db.connect()
    try:
        assert await db.has_attendance("2026-11-02", 223349) is False
        assert await db.log_attendance(
            "2026-11-02", 223349, 4, "key1", "hadir", "ok"
        ) is True
        assert await db.log_attendance(
            "2026-11-02", 223349, 4, "key1", "gagal", "retry"
        ) is True
        assert await db.has_attendance("2026-11-02", 223349) is False
        assert await db.log_attendance(
            "2026-11-02", 223349, 4, "key1", "hadir", "ok again"
        ) is True
        assert await db.has_attendance("2026-11-02", 223349) is True
        assert await db.has_attendance_key("2026-11-02", 223349, "key1") is True
        assert await db.has_attendance_key("2026-11-02", 223349, "other") is False
        assert await db.has_attendance_key_any_date(223349, "key1") is True
        assert await db.has_attendance_key_any_date(223349, "other") is False

        logs = await db.recent_logs(10)
        assert len(logs) == 1
        assert logs[0]["status"] == "hadir"
        assert logs[0]["pesan"] == "ok again"

        assert await db.get_day_status("2026-11-02", 223349) is None
        await db.set_day_status("2026-11-02", 223349, 4, "izin", "acara keluarga")
        assert await db.get_day_status("2026-11-02", 223349) == "izin"
        await db.set_day_status("2026-11-02", 223349, 4, "sakit")
        assert await db.get_day_status("2026-11-02", 223349) == "sakit"
        await db.clear_day_status("2026-11-02", 223349)
        assert await db.get_day_status("2026-11-02", 223349) is None
    finally:
        await db.close()


async def test_notified_cooldown(tmp_path) -> None:
    db = Database(str(tmp_path / "test.db"))
    await db.connect()
    try:
        assert await db.mark_notified("key1", 223349, cooldown_seconds=600) is True
        assert await db.mark_notified("key1", 223349, cooldown_seconds=600) is False
        assert await db.mark_notified("key1", 223349, cooldown_seconds=0) is True
    finally:
        await db.close()
