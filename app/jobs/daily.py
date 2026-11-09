from __future__ import annotations

import asyncio
import logging
from datetime import timedelta

from app.bot.formatters import format_today_schedule
from app.bot.notifications import Notifier
from app.config import Settings
from app.db import Database
from app.ethol.auth import AuthManager
from app.ethol.client import SessionExpired
from app.ethol.service import EtholService
from app.utils.time import today_str

logger = logging.getLogger(__name__)


class DailySummary:
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
        self._task = asyncio.create_task(self._run(), name="daily-summary")

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
                await self._maybe_send()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Daily summary error")

            try:
                await asyncio.wait_for(self._stop.wait(), timeout=30)
            except TimeoutError:
                pass

    async def _maybe_send(self) -> None:
        now = self.service.now_fn()
        hour, minute = (int(part) for part in self.settings.daily_summary_time.split(":"))
        target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if not (target <= now < target + timedelta(minutes=10)):
            return

        today = today_str(now, self.settings.timezone)
        last_sent = await self.db.get_setting("last_daily_summary")
        if last_sent == today:
            return

        await self.send_summary()
        await self.db.set_setting("last_daily_summary", today)

    async def send_summary(self) -> None:
        now = self.service.now_fn()
        try:
            items = await self.service.get_today_schedule()
        except SessionExpired:
            await self.auth.notify_expired()
            return

        text = format_today_schedule(items, now)
        refresh_expiry = await self.auth.session_cookie_expiry("refresh_token")
        if refresh_expiry and refresh_expiry - now.astimezone() < timedelta(days=2):
            text += (
                "\n\n⚠️ <b>Sesi ETHOL hampir habis.</b>\n"
                "Kirim /login untuk memperbarui lewat noVNC."
            )

        await self.notifier.broadcast(text)
