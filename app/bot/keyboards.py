from __future__ import annotations

from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.ethol.models import ActiveSession, Course


def main_menu(attendance_enabled: bool, mode: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="📅 Hari Ini", callback_data="menu:today")
    builder.button(text="📊 Status", callback_data="menu:status")
    builder.button(
        text=f"{'🟢' if attendance_enabled else '🔴'} Absensi {'ON' if attendance_enabled else 'OFF'}",
        callback_data="menu:toggle",
    )
    builder.button(
        text=f"⚙️ Mode: {'Auto' if mode == 'auto' else 'Konfirmasi'}",
        callback_data="menu:mode",
    )
    builder.button(text="🔐 Login / Perbarui Sesi", callback_data="menu:login")
    builder.button(text="📜 Log", callback_data="menu:log")
    builder.adjust(2, 2, 2)
    return builder.as_markup()


def attendance_keyboard(course: Course, session: ActiveSession) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    base = f"{course.nomor}:{course.jenis_schema}:{session.key}"
    builder.button(text="✅ Hadir", callback_data=f"att:h:{base}")
    builder.button(text="📝 Izin", callback_data=f"att:i:{base}")
    builder.button(text="🤒 Sakit", callback_data=f"att:s:{base}")
    builder.adjust(3)
    return builder.as_markup()


def login_keyboard(url: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="🖥️ Buka noVNC", url=url)
    return builder.as_markup()


def refresh_today_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="🔄 Refresh", callback_data="menu:today")
    return builder.as_markup()
