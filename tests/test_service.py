from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from cryptography.fernet import Fernet

from app.config import Settings
from app.db import Database
from app.ethol.models import (
    ActiveSession,
    Course,
    HistoryItem,
    ScheduleItem,
    SessionProfile,
)
from app.ethol.service import EtholService

JAKARTA = ZoneInfo("Asia/Jakarta")
NOW = datetime(2026, 11, 2, 10, 30, tzinfo=JAKARTA)

COURSE_OPTIK = Course(
    nomor=223349,
    jenis_schema=4,
    nama="Sistem Komunikasi Optik",
    dosen="Karimatun Nisa",
    kuliah_asal=None,
)
COURSE_STATISTIK = Course(
    nomor=223337,
    jenis_schema=4,
    nama="Statistik dan Probabilitas",
    dosen="Hani'ah Mahmudah",
    kuliah_asal=None,
)


class FakeClient:
    def __init__(self) -> None:
        self.active_key = "abc123"
        self.submitted: list[dict] = []

    async def get_config(self) -> dict:
        return {"tahun_aktif": 2026, "semester_aktif": 1}

    async def get_courses(self, tahun: int, semester: int) -> list[Course]:
        return [COURSE_OPTIK, COURSE_STATISTIK]

    async def get_schedule(
        self,
        courses: list[Course],
        tahun: int,
        semester: int,
    ) -> list[ScheduleItem]:
        return [
            ScheduleItem(
                kuliah=223349,
                nama="Sistem Komunikasi Optik",
                nomor_hari=1,
                jam_awal="10:30",
                jam_akhir="12:10",
                ruang="SAW-05.07",
                dosen="Karimatun Nisa",
            ),
            ScheduleItem(
                kuliah=223337,
                nama="Statistik dan Probabilitas",
                nomor_hari=2,
                jam_awal="13:50",
                jam_akhir="15:30",
                ruang="SAW-10.08",
                dosen="Hani'ah Mahmudah",
            ),
        ]

    async def get_active_sessions(
        self,
        kuliah: int,
        jenis_schema: int,
    ) -> list[ActiveSession]:
        if kuliah == 223349:
            return [
                ActiveSession(
                    kuliah=223349,
                    jenis_schema=4,
                    key=self.active_key,
                    open=True,
                )
            ]
        return []

    async def submit_presensi(self, **kwargs) -> dict:
        self.submitted.append(kwargs)
        return {"sukses": True, "pesan": "Presensi berhasil dicatat"}

    async def get_history(
        self,
        kuliah: int,
        jenis_schema: int,
        mahasiswa: int,
    ) -> list[HistoryItem]:
        return [
            HistoryItem(
                nomor=1,
                tanggal="02-11-2026 10:30:00",
                key=self.active_key,
            )
        ]


class FakeAuth:
    def __init__(self, client: FakeClient) -> None:
        self.client = client

    async def get_client(self) -> FakeClient:
        return self.client


def make_settings() -> Settings:
    return Settings(
        telegram_bot_token="1234567890:fake-token",
        allowed_chat_ids="1",
        encryption_key=Fernet.generate_key().decode(),
        timezone="Asia/Jakarta",
    )


async def test_today_schedule_and_find_course(tmp_path) -> None:
    db = Database(str(tmp_path / "test.db"))
    await db.connect()
    try:
        client = FakeClient()
        service = EtholService(FakeAuth(client), db, make_settings(), now_fn=lambda: NOW)

        items = await service.get_today_schedule()
        assert len(items) == 1
        assert items[0].kuliah == 223349

        course = await service.find_course("sistem")
        assert course is not None
        assert course.nomor == 223349

        by_number = await service.find_course("223337")
        assert by_number is not None
        assert by_number.nama == "Statistik dan Probabilitas"
    finally:
        await db.close()


async def test_submit_success_and_log(tmp_path) -> None:
    db = Database(str(tmp_path / "test.db"))
    await db.connect()
    try:
        client = FakeClient()
        service = EtholService(FakeAuth(client), db, make_settings(), now_fn=lambda: NOW)
        profile = SessionProfile(
            nomor=27850,
            nipnrp="2225600043",
            nama="Nicholas",
            hak_akses=["mahasiswa"],
        )

        session = await service.get_active_session(COURSE_OPTIK)
        assert session is not None
        result = await service.submit(COURSE_OPTIK, session, profile)

        assert result.sukses is True
        assert result.verified is True
        assert client.submitted[0]["key"] == "abc123"
        assert await db.has_attendance("2026-11-02", 223349) is True
    finally:
        await db.close()


async def test_submit_rejects_stale_key(tmp_path) -> None:
    db = Database(str(tmp_path / "test.db"))
    await db.connect()
    try:
        client = FakeClient()
        service = EtholService(FakeAuth(client), db, make_settings(), now_fn=lambda: NOW)
        profile = SessionProfile(
            nomor=27850,
            nipnrp="2225600043",
            nama="Nicholas",
            hak_akses=["mahasiswa"],
        )
        session = await service.get_active_session(COURSE_OPTIK)
        assert session is not None

        client.active_key = "changed"
        result = await service.submit(COURSE_OPTIK, session, profile)

        assert result.sukses is False
        assert "ditutup" in result.pesan.lower()
        assert client.submitted == []
    finally:
        await db.close()


async def test_submit_duplicate_does_not_post_again(tmp_path) -> None:
    db = Database(str(tmp_path / "test.db"))
    await db.connect()
    try:
        client = FakeClient()
        service = EtholService(FakeAuth(client), db, make_settings(), now_fn=lambda: NOW)
        profile = SessionProfile(
            nomor=27850,
            nipnrp="2225600043",
            nama="Nicholas",
            hak_akses=["mahasiswa"],
        )
        session = await service.get_active_session(COURSE_OPTIK)
        assert session is not None

        first = await service.submit(COURSE_OPTIK, session, profile)
        second = await service.submit(COURSE_OPTIK, session, profile)

        assert first.sukses is True
        assert second.sukses is True
        assert "sudah" in second.pesan.lower()
        assert len(client.submitted) == 1
    finally:
        await db.close()
