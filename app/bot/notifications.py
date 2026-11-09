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
    def __init__(self, bot: Bot, settings: Settings) -> None:
        self.bot = bot
        self.settings = settings

    async def broadcast(
        self,
        text: str,
        reply_markup: InlineKeyboardMarkup | None = None,
    ) -> None:
        for chat_id in self.settings.chat_ids:
            try:
                await self.bot.send_message(chat_id, text, reply_markup=reply_markup)
            except Exception:
                logger.exception("Gagal mengirim pesan ke chat %s", chat_id)

    async def session_open(
        self,
        course: Course,
        item: ScheduleItem,
        session: ActiveSession,
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
        await self.broadcast(text, reply_markup=attendance_keyboard(course, session))

    async def attendance_result(
        self,
        course: Course,
        item: ScheduleItem,
        result: SubmitResult,
        auto: bool = False,
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
        await self.broadcast(text)

    async def session_expired(self) -> None:
        text = (
            "🔐 <b>Sesi ETHOL habis</b>\n\n"
            "Bot tidak bisa mengakses ETHOL. Buka noVNC (halaman login CAS PENS) untuk login ulang.\n"
            "Password VNC = nilai <code>VNC_PASSWORD</code> di <code>.env</code>.\n\n"
            "Setelah dashboard ETHOL terbuka, sesi otomatis tersimpan."
        )
        await self.broadcast(text, reply_markup=login_keyboard(self.settings.novnc_public_url))

    async def login_success(self, profile: SessionProfile) -> None:
        text = (
            "✅ <b>Login berhasil</b>\n\n"
            f"Sesi ETHOL untuk <b>{escape(profile.nama)}</b> sudah tersimpan.\n"
            "Bot siap digunakan."
        )
        await self.broadcast(text)

    async def login_failure(self, message: str) -> None:
        text = (
            "⚠️ <b>Login gagal</b>\n\n"
            f"{escape(message)}\n\n"
            "Coba /login lagi."
        )
        await self.broadcast(text)
