from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from cryptography.fernet import Fernet

from app.config import Settings
from app.db import Database
from app.ethol.models import (
    ActiveSession,
    Course,
    ScheduleItem,
    SessionProfile,
    SubmitResult,
)
from app.jobs.watcher import Watcher

JAKARTA = ZoneInfo("Asia/Jakarta")
NOW = datetime(2026, 11, 2, 10, 30, tzinfo=JAKARTA)
COURSE = Course(
    nomor=223349,
    jenis_schema=4,
    nama="Sistem Komunikasi Optik",
    dosen="Karimatun Nisa",
)
ITEM = ScheduleItem(
    kuliah=223349,
    nama="Sistem Komunikasi Optik",
    nomor_hari=1,
    jam_awal="10:30",
    jam_akhir="12:10",
    ruang="SAW-05.07",
    dosen="Karimatun Nisa",
)
PROFILE = SessionProfile(
    nomor=27850,
    nipnrp="2225600043",
    nama="Nicholas",
    hak_akses=["mahasiswa"],
)


class FakeAuth:
    def __init__(self) -> None:
        self.expired = False

    async def get_profile(self) -> SessionProfile:
        return PROFILE

    async def notify_expired(self) -> None:
        self.expired = True


class FakeService:
    def __init__(self, session: ActiveSession | None) -> None:
        self.session = session
        self.submitted: list[tuple[Course, ActiveSession]] = []
        self.now_fn = lambda: NOW

    async def get_today_schedule(self, force: bool = False) -> list[ScheduleItem]:
        return [ITEM]

    async def get_all_courses(self, force: bool = False) -> list[Course]:
        return [COURSE]

    async def get_course(self, nomor: int) -> Course | None:
        return COURSE if nomor == COURSE.nomor else None

    async def get_active_session(self, course: Course) -> ActiveSession | None:
        return self.session

    async def submit(
        self,
        course: Course,
        session: ActiveSession,
        profile: SessionProfile,
    ) -> SubmitResult:
        self.submitted.append((course, session))
        return SubmitResult(True, "Presensi berhasil dicatat", True)


class FakeNotifier:
    def __init__(self) -> None:
        self.opened = 0
        self.results = 0

    async def session_open(self, course, item, session) -> None:
        self.opened += 1

    async def attendance_result(self, course, item, result, auto=False) -> None:
        self.results += 1


def make_settings(mode: str = "confirm") -> Settings:
    return Settings(
        telegram_bot_token="1234567890:fake-token",
        allowed_chat_ids="1",
        encryption_key=Fernet.generate_key().decode(),
        attendance_mode=mode,
    )


def make_session() -> ActiveSession:
    return ActiveSession(kuliah=223349, jenis_schema=4, key="abc123", open=True)


async def test_confirm_mode_notifies_without_submit(tmp_path) -> None:
    db = Database(str(tmp_path / "test.db"))
    await db.connect()
    try:
        auth = FakeAuth()
        service = FakeService(make_session())
        notifier = FakeNotifier()
        watcher = Watcher(make_settings("confirm"), db, service, auth, notifier)

        await watcher._tick()

        assert notifier.opened == 1
        assert notifier.results == 0
        assert service.submitted == []
    finally:
        await db.close()


async def test_auto_mode_submits(tmp_path) -> None:
    db = Database(str(tmp_path / "test.db"))
    await db.connect()
    try:
        auth = FakeAuth()
        service = FakeService(make_session())
        notifier = FakeNotifier()
        watcher = Watcher(make_settings("auto"), db, service, auth, notifier)

        await watcher._tick()

        assert len(service.submitted) == 1
        assert notifier.results == 1
        assert notifier.opened == 0
    finally:
        await db.close()


async def test_day_status_izin_skips(tmp_path) -> None:
    db = Database(str(tmp_path / "test.db"))
    await db.connect()
    try:
        await db.set_day_status("2026-11-02", 223349, 4, "izin")
        auth = FakeAuth()
        service = FakeService(make_session())
        notifier = FakeNotifier()
        watcher = Watcher(make_settings("confirm"), db, service, auth, notifier)

        await watcher._tick()

        assert notifier.opened == 0
        assert service.submitted == []
    finally:
        await db.close()


async def test_existing_attendance_skips(tmp_path) -> None:
    db = Database(str(tmp_path / "test.db"))
    await db.connect()
    try:
        await db.log_attendance(
            "2026-11-02", 223349, 4, "abc123", "hadir", "ok"
        )
        auth = FakeAuth()
        service = FakeService(make_session())
        notifier = FakeNotifier()
        watcher = Watcher(make_settings("confirm"), db, service, auth, notifier)

        await watcher._tick()

        assert notifier.opened == 0
        assert service.submitted == []
    finally:
        await db.close()


async def test_no_session_does_nothing(tmp_path) -> None:
    db = Database(str(tmp_path / "test.db"))
    await db.connect()
    try:
        auth = FakeAuth()
        service = FakeService(None)
        notifier = FakeNotifier()
        watcher = Watcher(make_settings("confirm"), db, service, auth, notifier)

        await watcher._tick()

        assert notifier.opened == 0
        assert service.submitted == []
    finally:
        await db.close()


class MakeupService(FakeService):
    async def get_today_schedule(self, force: bool = False) -> list[ScheduleItem]:
        return []


async def test_makeup_session_on_other_day_is_detected(tmp_path) -> None:
    db = Database(str(tmp_path / "test.db"))
    await db.connect()
    try:
        auth = FakeAuth()
        service = MakeupService(make_session())
        notifier = FakeNotifier()
        watcher = Watcher(make_settings("confirm"), db, service, auth, notifier)

        await watcher._tick()

        assert notifier.opened == 1
        assert service.submitted == []
    finally:
        await db.close()
