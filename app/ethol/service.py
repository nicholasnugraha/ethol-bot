from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from datetime import datetime
from typing import Any

from app.config import Settings
from app.db import Database
from app.ethol.auth import AuthManager
from app.ethol.models import (
    ActiveSession,
    Course,
    ScheduleItem,
    SessionProfile,
    SubmitResult,
)
from app.utils.time import now_jakarta, today_str

logger = logging.getLogger(__name__)


class EtholService:
    def __init__(
        self,
        auth: AuthManager,
        db: Database,
        settings: Settings,
        now_fn: Callable[[], datetime] | None = None,
    ) -> None:
        self.auth = auth
        self.db = db
        self.settings = settings
        self.now_fn = now_fn or (lambda: now_jakarta(settings.timezone))

        self._config_cache: tuple[float, dict[str, Any]] | None = None
        self._courses_cache: dict[tuple[int, int], tuple[float, list[Course]]] = {}
        self._schedule_cache: dict[tuple[int, int, str], tuple[float, list[ScheduleItem]]] = {}
        self._submit_lock = asyncio.Lock()

    def _now(self) -> datetime:
        return self.now_fn()

    def _today(self) -> str:
        return today_str(self._now(), self.settings.timezone)

    async def get_config(self, force: bool = False) -> dict[str, Any]:
        now = time.monotonic()
        if not force and self._config_cache and now - self._config_cache[0] < 3600:
            return self._config_cache[1]

        client = await self.auth.get_client()
        config = await client.get_config()
        self._config_cache = (now, config)
        return config

    async def get_courses(
        self,
        tahun: int,
        semester: int,
        force: bool = False,
    ) -> list[Course]:
        key = (tahun, semester)
        now = time.monotonic()
        cached = self._courses_cache.get(key)
        if not force and cached and now - cached[0] < 86_400:
            return cached[1]

        if not force:
            payload = await self.db.load_courses(tahun, semester)
            if payload:
                courses = [Course.from_dict(item) for item in payload]
                self._courses_cache[key] = (now, courses)
                return courses

        client = await self.auth.get_client()
        courses = await client.get_courses(tahun, semester)
        await self.db.save_courses(tahun, semester, [c.to_dict() for c in courses])
        self._courses_cache[key] = (now, courses)
        return courses

    async def get_schedule(
        self,
        tahun: int,
        semester: int,
        courses: list[Course],
        force: bool = False,
    ) -> list[ScheduleItem]:
        tanggal = self._today()
        key = (tahun, semester, tanggal)
        now = time.monotonic()
        cached = self._schedule_cache.get(key)
        if not force and cached and now - cached[0] < 21_600:
            return cached[1]

        if not force:
            payload = await self.db.load_schedule(tahun, semester, tanggal)
            if payload:
                schedule = [ScheduleItem.from_dict(item) for item in payload]
                self._schedule_cache[key] = (now, schedule)
                return schedule

        client = await self.auth.get_client()
        schedule = await client.get_schedule(courses, tahun, semester)
        await self.db.save_schedule(
            tahun,
            semester,
            tanggal,
            [item.to_dict() for item in schedule],
        )
        self._schedule_cache[key] = (now, schedule)
        return schedule

    async def get_today_schedule(self, force: bool = False) -> list[ScheduleItem]:
        config = await self.get_config(force=force)
        tahun = int(config["tahun_aktif"])
        semester = int(config["semester_aktif"])
        courses = await self.get_courses(tahun, semester, force=force)
        schedule = await self.get_schedule(tahun, semester, courses, force=force)

        today = self._now().isoweekday()
        today_items = [item for item in schedule if item.nomor_hari == today]
        return sorted(today_items, key=lambda item: item.jam_awal)

    async def get_all_courses(self, force: bool = False) -> list[Course]:
        """All courses the student is enrolled in, regardless of schedule."""
        config = await self.get_config(force=force)
        return await self.get_courses(
            int(config["tahun_aktif"]),
            int(config["semester_aktif"]),
            force=force,
        )

    async def get_course(self, nomor: int) -> Course | None:
        config = await self.get_config()
        courses = await self.get_courses(
            int(config["tahun_aktif"]),
            int(config["semester_aktif"]),
        )
        return next((course for course in courses if course.nomor == nomor), None)

    async def find_course(self, query: str) -> Course | None:
        config = await self.get_config()
        courses = await self.get_courses(
            int(config["tahun_aktif"]),
            int(config["semester_aktif"]),
        )
        query = query.strip()
        if query.isdigit():
            return next((c for c in courses if c.nomor == int(query)), None)

        needle = query.casefold()
        return next(
            (course for course in courses if needle in course.nama.casefold()),
            None,
        )

    async def get_active_session(self, course: Course) -> ActiveSession | None:
        client = await self.auth.get_client()
        sessions = await client.get_active_sessions(course.nomor, course.jenis_schema)
        return next((session for session in sessions if session.open and session.key), None)

    async def submit(
        self,
        course: Course,
        session: ActiveSession,
        profile: SessionProfile,
    ) -> SubmitResult:
        """Submit attendance once, re-validating the session key and verifying history."""
        async with self._submit_lock:
            if await self.db.has_attendance_key_any_date(course.nomor, session.key):
                return SubmitResult(True, "Presensi sudah tercatat sebelumnya", True)

            fresh = await self.get_active_session(course)
            if fresh is None or fresh.key != session.key:
                return SubmitResult(False, "Sesi presensi sudah ditutup atau berganti")

            client = await self.auth.get_client()
            try:
                response = await client.submit_presensi(
                    kuliah=course.nomor,
                    jenis_schema=course.jenis_schema,
                    mahasiswa=profile.nomor,
                    key=fresh.key,
                    kuliah_asal=course.kuliah_asal,
                )
            except Exception as exc:
                # A timeout can happen after ETHOL already recorded the
                # attendance. Check history before reporting a failure.
                try:
                    history = await client.get_history(
                        course.nomor,
                        course.jenis_schema,
                        profile.nomor,
                    )
                    if any(item.key == fresh.key for item in history):
                        await self.db.log_attendance(
                            tanggal=self._today(),
                            kuliah=course.nomor,
                            jenis_schema=course.jenis_schema,
                            session_key=fresh.key,
                            status="hadir",
                            pesan=f"Terverifikasi setelah error: {str(exc)[:200]}",
                        )
                        return SubmitResult(
                            True,
                            "Presensi tercatat (terverifikasi setelah error)",
                            True,
                        )
                except Exception:  # best-effort verification
                    logger.debug("Verifikasi riwayat setelah error gagal", exc_info=True)

                await self.db.log_attendance(
                    tanggal=self._today(),
                    kuliah=course.nomor,
                    jenis_schema=course.jenis_schema,
                    session_key=fresh.key,
                    status="gagal",
                    pesan=str(exc)[:500],
                )
                raise

            sukses = bool(response.get("sukses"))
            pesan = str(
                response.get("pesan")
                or ("Presensi berhasil dicatat" if sukses else "Presensi gagal")
            )
            verified = False

            if sukses:
                try:
                    history = await client.get_history(
                        course.nomor,
                        course.jenis_schema,
                        profile.nomor,
                    )
                    verified = any(item.key == fresh.key for item in history)
                except Exception:  # verification failure must not hide a success
                    logger.debug("Verifikasi riwayat gagal", exc_info=True)
                    pesan += " (verifikasi riwayat gagal)"
                else:
                    if not verified:
                        pesan += " (belum terlihat di riwayat)"

            await self.db.log_attendance(
                tanggal=self._today(),
                kuliah=course.nomor,
                jenis_schema=course.jenis_schema,
                session_key=fresh.key,
                status="hadir" if sukses else "gagal",
                pesan=pesan,
            )
            return SubmitResult(sukses, pesan, verified)
