from __future__ import annotations

import asyncio
import logging

from app.bot.notifications import Notifier
from app.config import Settings
from app.db import Database
from app.ethol.auth import AuthManager
from app.ethol.client import SessionExpired
from app.ethol.models import Course, ScheduleItem
from app.ethol.service import EtholService
from app.utils.time import today_str

logger = logging.getLogger(__name__)


class Watcher:
    def __init__(
        self,
        settings: Settings,
        db: Database,
        service: EtholService,
        auth: AuthManager,
        notifier: Notifier,
    ) -> None:
        self.settings = settings
        self.db = db
        self.service = service
        self.auth = auth
        self.notifier = notifier
        self._stop = asyncio.Event()
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._run(), name="attendance-watcher")

    async def stop(self) -> None:
        self._stop.set()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _run(self) -> None:
        while not self._stop.is_set():
            try:
                enabled = await self.db.get_bool_setting("attendance_enabled", False)
                if enabled:
                    await self._tick()
            except SessionExpired:
                await self.auth.notify_expired()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Watcher loop error")

            try:
                await asyncio.wait_for(
                    self._stop.wait(),
                    timeout=self.settings.poll_interval_seconds,
                )
            except TimeoutError:
                pass

    async def _schedule_map(self) -> dict[int, ScheduleItem]:
        """Schedule info for display; missing for makeup sessions on other days."""
        try:
            items = await self.service.get_today_schedule()
        except Exception:
            logger.debug("Jadwal hari ini tidak tersedia untuk tampilan", exc_info=True)
            return {}
        return {item.kuliah: item for item in items}

    @staticmethod
    def _fallback_item(course: Course, now) -> ScheduleItem:
        return ScheduleItem(
            kuliah=course.nomor,
            nama=course.nama,
            nomor_hari=now.isoweekday(),
            jam_awal="-",
            jam_akhir="-",
            ruang="-",
            dosen=course.dosen,
        )

    async def _tick(self) -> None:
        profile = await self.auth.get_profile()
        # Check every enrolled course, not only those scheduled for today.
        # This catches makeup/replacement sessions opened on a different day
        # or outside the normal class time window.
        courses = await self.service.get_all_courses()
        if not courses:
            return

        schedule_map = await self._schedule_map()
        now = self.service.now_fn()
        today = today_str(now, self.settings.timezone)
        mode = await self.db.get_setting("attendance_mode", self.settings.attendance_mode)
        cooldown = 120 if mode == "auto" else 600

        for course in courses:
            day_status = await self.db.get_day_status(today, course.nomor)
            if day_status in {"izin", "sakit", "skip"}:
                continue

            session = await self.service.get_active_session(course)
            if session is None:
                continue

            if await self.db.has_attendance_key_any_date(course.nomor, session.key):
                continue

            # Rate-limit both notifications and auto-submit attempts per session key.
            if not await self.db.mark_notified(session.key, course.nomor, cooldown_seconds=cooldown):
                continue

            item = schedule_map.get(course.nomor) or self._fallback_item(course, now)
            if mode == "auto":
                result = await self.service.submit(course, session, profile)
                await self.notifier.attendance_result(course, item, result, auto=True)
            else:
                await self.notifier.session_open(course, item, session)


class SessionKeeper:
    """Menjaga sesi tetap hidup: validasi berkala dan refresh saat token kedaluwarsa."""

    def __init__(
        self,
        settings: Settings,
        auth: AuthManager,
    ) -> None:
        self.settings = settings
        self.auth = auth
        self._stop = asyncio.Event()
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._run(), name="session-keeper")

    async def stop(self) -> None:
        self._stop.set()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _run(self) -> None:
        while not self._stop.is_set():
            try:
                if await self.auth.has_session():
                    await self.auth.get_profile()
            except SessionExpired:
                await self.auth.notify_expired()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Session keeper error")

            try:
                await asyncio.wait_for(
                    self._stop.wait(),
                    timeout=self.settings.session_check_seconds,
                )
            except TimeoutError:
                pass
