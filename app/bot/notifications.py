from __future__ import annotations

import logging
from html import escape

from aiogram import Bot
from aiogram.types import InlineKeyboardMarkup

from app.bot.keyboards import attendance_keyboard, login_keyboard
from app.config import Settings
from app.ethol.models import (
    ActiveSession,
    Course,
    ScheduleItem,
    SessionProfile,
    SubmitResult,
)
from app.utils.time import format_time_range

logger = logging.getLogger(__name__)


class Notifier:
    def __init__(self, bot: Bot, settings: Settings, recipient_id: int | None = None) -> None:
        self.bot = bot
        self.settings = settings
        self.recipient_id = recipient_id

    async def send(
        self,
        text: str,
        reply_markup: InlineKeyboardMarkup | None = None,
        *,
        recipient_id: int | None = None,
    ) -> None:
        target = recipient_id if recipient_id is not None else self.recipient_id
        if target is None:
            raise ValueError("Notifier requires an explicit recipient")
        try:
            await self.bot.send_message(target, text, reply_markup=reply_markup)
        except Exception:
            logger.exception("Gagal mengirim pesan ke chat %s", target)
            raise

    async def broadcast(
        self,
        text: str,
        reply_markup: InlineKeyboardMarkup | None = None,
        *,
        recipient_id: int | None = None,
    ) -> None:
        # Compatibility alias with safe, explicit single-recipient semantics.
        await self.send(text, reply_markup, recipient_id=recipient_id)

    async def session_open(
        self,
        course: Course,
        item: ScheduleItem,
        session: ActiveSession,
        *,
        recipient_id: int | None = None,
    ) -> None:
        text = (
            "🔔 <b>Sesi presensi dibuka</b>\n\n"
            f"<b>{escape(course.nama)}</b>\n"
            f"🕐 {format_time_range(item.jam_awal, item.jam_akhir)}"
            f" · 📍 {escape(item.ruang or '-')}\n"
            f"🆔 <code>{course.nomor}</code>\n"
        )
        if course.dosen:
            text += f"👨‍🏫 {escape(course.dosen)}\n"
        text += (
            "\nPilih status kehadiran.\n"
            "<b>Hadir</b> akan mengirim presensi ke ETHOL.\n"
            "<b>Izin/Sakit</b> hanya dicatat lokal dan tidak dikirim ke server."
        )
        await self.broadcast(text, reply_markup=attendance_keyboard(course, session), recipient_id=recipient_id)

    async def attendance_result(
        self,
        course: Course,
        item: ScheduleItem,
        result: SubmitResult,
        auto: bool = False,
        *,
        recipient_id: int | None = None,
    ) -> None:
        icon = "✅" if result.sukses else "⚠️"
        title = "Presensi berhasil" if result.sukses else "Presensi gagal"
        mode = " (otomatis)" if auto else ""
        text = (
            f"{icon} <b>{title}{mode}</b>\n\n"
            f"<b>{escape(course.nama)}</b>\n"
            f"🕐 {format_time_range(item.jam_awal, item.jam_akhir)}\n"
            f"<i>{escape(result.pesan)}</i>"
        )
        await self.broadcast(text, recipient_id=recipient_id)

    async def session_expired(self, *, recipient_id: int | None = None) -> None:
        text = (
            "🔐 <b>Sesi ETHOL habis</b>\n\n"
            "Bot tidak bisa mengakses ETHOL. Kirim /login untuk memulai sesi login pribadi.\n\n"
            "Setelah dashboard ETHOL terbuka, sesi otomatis tersimpan."
        )
        await self.broadcast(text, recipient_id=recipient_id)

    async def login_success(self, profile: SessionProfile, *, recipient_id: int | None = None) -> None:
        text = (
            "✅ <b>Login berhasil</b>\n\n"
            f"Sesi ETHOL untuk <b>{escape(profile.nama)}</b> sudah tersimpan.\n"
            "Bot siap digunakan."
        )
        await self.broadcast(text, recipient_id=recipient_id)

    async def login_failure(self, message: str, *, recipient_id: int | None = None) -> None:
        text = (
            "⚠️ <b>Login gagal</b>\n\n"
            f"{escape(message)}\n\n"
            "Coba /login lagi."
        )
        await self.broadcast(text, recipient_id=recipient_id)
