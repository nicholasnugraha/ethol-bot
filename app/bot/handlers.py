# ruff: noqa: BLE001
from __future__ import annotations

import logging
from html import escape

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandStart
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message

from app.bot.formatters import format_log_entry, format_today_schedule
from app.bot.keyboards import login_keyboard, main_menu, refresh_today_keyboard
from app.context import AppContext
from app.ethol.client import SessionExpired
from app.utils.time import in_window, today_str

logger = logging.getLogger(__name__)
router = Router(name="ethol-bot")

WELCOME = """🤖 <b>Bot Absensi ETHOL</b>

Perintah utama:
/hariini — mata kuliah hari ini + status sesi
/status — status sesi & konfigurasi
/on — aktifkan pemantauan absensi
/off — matikan pemantauan
/mode — ganti Konfirmasi ↔ Auto
/login — login / perbarui sesi via noVNC
/log — 10 log presensi terakhir
/izin &lt;mk&gt; — tandai izin (lokal)
/sakit &lt;mk&gt; — tandai sakit (lokal)
/batal &lt;mk&gt; — hapus status izin/sakit lokal

<i>Presensi hanya dikirim saat dosen membuka sesi. Gunakan mode Auto hanya jika kamu benar-benar hadir.</i>"""

LOGIN_TEXT = """🔐 <b>Login ETHOL via noVNC</b>

1. Tekan tombol <b>Buka noVNC</b>.
2. Masukkan password VNC (nilai <code>VNC_PASSWORD</code> di <code>.env</code>).
3. Di browser VPS, login dengan NetID &amp; password PENS.
4. Setelah dashboard ETHOL terbuka, bot otomatis menyimpan sesi.

<i>Browser sedang disiapkan. Jika layar masih kosong, tunggu beberapa detik lalu refresh.</i>"""


async def _menu(ctx: AppContext) -> InlineKeyboardMarkup:
    enabled = await ctx.db.get_bool_setting("attendance_enabled", False)
    mode = await ctx.db.get_setting("attendance_mode", ctx.settings.attendance_mode)
    return main_menu(enabled, mode)


async def _edit(
    callback: CallbackQuery,
    text: str,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> None:
    if callback.message is None:
        return
    try:
        await callback.message.edit_text(text, reply_markup=reply_markup)
    except TelegramBadRequest as exc:
        if "message is not modified" not in str(exc).lower():
            await callback.message.answer(text, reply_markup=reply_markup)


def _error_text(exc: Exception) -> str:
    return f"⚠️ Terjadi error: <code>{escape(str(exc)[:300])}</code>"



async def _today_text(ctx: AppContext) -> tuple[str, InlineKeyboardMarkup]:
    items = await ctx.service.get_today_schedule(force=True)
    now = ctx.service.now_fn()
    today = today_str(now, ctx.settings.timezone)

    status_map: dict[int, str] = {}
    for item in items:
        status = await ctx.db.get_day_status(today, item.kuliah)
        if status:
            status_map[item.kuliah] = status
            continue
        if await ctx.db.has_attendance(today, item.kuliah):
            status_map[item.kuliah] = "hadir"
            continue
        course = await ctx.service.get_course(item.kuliah)
        if course:
            session = await ctx.service.get_active_session(course)
            if session:
                status_map[item.kuliah] = "dibuka"
            elif not in_window(
                now,
                item.jam_awal,
                item.jam_akhir,
                ctx.settings.window_before_minutes,
                ctx.settings.window_after_minutes,
            ):
                status_map[item.kuliah] = "lewat"
            else:
                status_map[item.kuliah] = "belum"

    return format_today_schedule(items, now, status_map), refresh_today_keyboard()


async def _status_text(ctx: AppContext) -> str:
    enabled = await ctx.db.get_bool_setting("attendance_enabled", False)
    mode = await ctx.db.get_setting("attendance_mode", ctx.settings.attendance_mode)

    try:
        profile = await ctx.auth.get_profile()
        session_line = f"✅ valid — {escape(profile.nama)}"
    except SessionExpired:
        session_line = "❌ habis / belum login"
    except Exception as exc:
        session_line = f"⚠️ error: {escape(str(exc)[:120])}"

    try:
        items = await ctx.service.get_today_schedule()
        course_count: int | str = len(items)
    except Exception:
        course_count = "?"

    logs = await ctx.db.recent_logs(1)
    last_log = format_log_entry(logs[0]) if logs else "-"

    return (
        "📊 <b>Status Bot</b>\n\n"
        f"Sesi ETHOL: {session_line}\n"
        f"Absensi: {'🟢 ON' if enabled else '🔴 OFF'}\n"
        f"Mode: {'Auto' if mode == 'auto' else 'Konfirmasi'}\n"
        f"MK hari ini: {course_count}\n\n"
        f"<b>Log terakhir</b>\n{last_log}"
    )


@router.message(CommandStart())
async def cmd_start(message: Message, ctx: AppContext) -> None:
    await message.answer(WELCOME, reply_markup=await _menu(ctx))


@router.message(Command("help"))
async def cmd_help(message: Message, ctx: AppContext) -> None:
    await message.answer(WELCOME, reply_markup=await _menu(ctx))


@router.message(Command("login"))
async def cmd_login(message: Message, ctx: AppContext) -> None:
    ctx.auth.start_login_background()
    await message.answer(
        LOGIN_TEXT,
        reply_markup=login_keyboard(ctx.settings.novnc_public_url),
    )


@router.message(Command("hariini", "today"))
async def cmd_today(message: Message, ctx: AppContext) -> None:
    try:
        text, markup = await _today_text(ctx)
    except SessionExpired:
        await message.answer(
            "Sesi ETHOL habis. Kirim /login untuk memperbarui.",
            reply_markup=login_keyboard(ctx.settings.novnc_public_url),
        )
        return
    except Exception as exc:
        await message.answer(_error_text(exc))
        return
    await message.answer(text, reply_markup=markup)


@router.message(Command("status"))
async def cmd_status(message: Message, ctx: AppContext) -> None:
    await message.answer(await _status_text(ctx), reply_markup=await _menu(ctx))


@router.message(Command("on"))
async def cmd_on(message: Message, ctx: AppContext) -> None:
    try:
        await ctx.auth.get_profile()
    except SessionExpired:
        await message.answer("Sesi ETHOL belum valid. Kirim /login dulu.")
        return
    except Exception as exc:
        await message.answer(_error_text(exc))
        return
    await ctx.db.set_bool_setting("attendance_enabled", True)
    await message.answer(
        "🟢 Absensi diaktifkan. Bot akan memantau sesi presensi.",
        reply_markup=await _menu(ctx),
    )


@router.message(Command("off"))
async def cmd_off(message: Message, ctx: AppContext) -> None:
    await ctx.db.set_bool_setting("attendance_enabled", False)
    await message.answer("🔴 Absensi dimatikan.", reply_markup=await _menu(ctx))


async def _toggle_mode(ctx: AppContext) -> str:
    current = await ctx.db.get_setting("attendance_mode", ctx.settings.attendance_mode)
    new_mode = "auto" if current != "auto" else "confirm"
    await ctx.db.set_setting("attendance_mode", new_mode)
    if new_mode == "auto":
        return (
            "⚙️ Mode diubah ke: <b>Auto</b>\n\n"
            "⚠️ Bot akan langsung mengirim presensi saat sesi dibuka, tanpa konfirmasi. "
            "Gunakan hanya jika kamu benar-benar hadir di kelas."
        )
    return (
        "⚙️ Mode diubah ke: <b>Konfirmasi</b>\n\n"
        "Bot akan meminta kamu memilih Hadir/Izin/Sakit sebelum mengirim."
    )


@router.message(Command("mode"))
async def cmd_mode(message: Message, ctx: AppContext) -> None:
    await message.answer(await _toggle_mode(ctx), reply_markup=await _menu(ctx))


async def _set_day_status(message: Message, ctx: AppContext, status: str) -> None:
    args = (message.text or "").split(maxsplit=1)
    if len(args) < 2:
        await message.answer(
            f"Format: /{status} &lt;nomor_atau_nama_mk&gt;\n"
            f"Contoh: /{status} 223349"
        )
        return

    query = args[1].strip()
    try:
        course = await ctx.service.find_course(query)
    except SessionExpired:
        await message.answer("Sesi ETHOL habis. Kirim /login dulu.")
        return
    except Exception as exc:
        await message.answer(_error_text(exc))
        return

    if course is None:
        await message.answer(f"MK tidak ditemukan: {escape(query)}")
        return

    today = today_str(ctx.service.now_fn(), ctx.settings.timezone)
    await ctx.db.set_day_status(today, course.nomor, course.jenis_schema, status)
    await message.answer(
        f"📝 <b>{status.capitalize()}</b> dicatat untuk <b>{escape(course.nama)}</b>.\n"
        "Tidak ada presensi yang dikirim ke ETHOL untuk MK ini hari ini."
    )


@router.message(Command("izin"))
async def cmd_izin(message: Message, ctx: AppContext) -> None:
    await _set_day_status(message, ctx, "izin")


@router.message(Command("sakit"))
async def cmd_sakit(message: Message, ctx: AppContext) -> None:
    await _set_day_status(message, ctx, "sakit")


@router.message(Command("batal"))
async def cmd_batal(message: Message, ctx: AppContext) -> None:
    args = (message.text or "").split(maxsplit=1)
    if len(args) < 2:
        await message.answer("Format: /batal &lt;nomor_atau_nama_mk&gt;")
        return

    query = args[1].strip()
    try:
        course = await ctx.service.find_course(query)
    except SessionExpired:
        await message.answer("Sesi ETHOL habis. Kirim /login dulu.")
        return
    except Exception as exc:
        await message.answer(_error_text(exc))
        return

    if course is None:
        await message.answer(f"MK tidak ditemukan: {escape(query)}")
        return

    today = today_str(ctx.service.now_fn(), ctx.settings.timezone)
    await ctx.db.clear_day_status(today, course.nomor)
    await message.answer(
        f"♻️ Status lokal untuk <b>{escape(course.nama)}</b> dihapus. "
        "Watcher akan memproses MK ini lagi."
    )


@router.message(Command("log"))
async def cmd_log(message: Message, ctx: AppContext) -> None:
    logs = await ctx.db.recent_logs(10)
    if not logs:
        await message.answer("Belum ada log presensi.")
        return
    text = "📜 <b>10 log terakhir</b>\n\n" + "\n\n".join(
        format_log_entry(entry) for entry in logs
    )
    await message.answer(text)


@router.callback_query(F.data.startswith("menu:"))
async def on_menu(callback: CallbackQuery, ctx: AppContext) -> None:
    action = callback.data.split(":", 1)[1]
    answered = False
    try:
        if action == "today":
            text, markup = await _today_text(ctx)
            await _edit(callback, text, markup)
        elif action == "status":
            await _edit(callback, await _status_text(ctx), await _menu(ctx))
        elif action == "toggle":
            enabled = await ctx.db.get_bool_setting("attendance_enabled", False)
            if not enabled:
                try:
                    await ctx.auth.get_profile()
                except SessionExpired:
                    await callback.answer("Sesi belum valid. /login dulu.", show_alert=True)
                    answered = True
                    return
            await ctx.db.set_bool_setting("attendance_enabled", not enabled)
            await _edit(callback, await _status_text(ctx), await _menu(ctx))
        elif action == "mode":
            await _edit(callback, await _toggle_mode(ctx), await _menu(ctx))
        elif action == "login":
            ctx.auth.start_login_background()
            await _edit(
                callback,
                LOGIN_TEXT,
                login_keyboard(ctx.settings.novnc_public_url),
            )
        elif action == "log":
            logs = await ctx.db.recent_logs(10)
            text = (
                "📜 <b>10 log terakhir</b>\n\n" + "\n\n".join(
                    format_log_entry(entry) for entry in logs
                )
                if logs
                else "Belum ada log presensi."
            )
            await _edit(callback, text, await _menu(ctx))
    except SessionExpired:
        await _edit(
            callback,
            "Sesi ETHOL habis. Kirim /login untuk memperbarui.",
            login_keyboard(ctx.settings.novnc_public_url),
        )
    except Exception as exc:
        await _edit(callback, _error_text(exc), await _menu(ctx))
    finally:
        if not answered:
            await callback.answer()


@router.callback_query(F.data.startswith("att:"))
async def on_attendance(callback: CallbackQuery, ctx: AppContext) -> None:
    try:
        parts = callback.data.split(":")
        if len(parts) != 5:
            raise ValueError("Data tombol tidak valid")

        action = parts[1]
        kuliah = int(parts[2])
        jenis_schema = int(parts[3])
        key = parts[4]

        if action not in {"h", "i", "s"}:
            await callback.answer("Aksi tidak dikenal", show_alert=True)
            return

        course = await ctx.service.get_course(kuliah)
        if course is None:
            await callback.answer("MK tidak ditemukan", show_alert=True)
            return

        today = today_str(ctx.service.now_fn(), ctx.settings.timezone)

        if action in {"i", "s"}:
            status = "izin" if action == "i" else "sakit"
            await ctx.db.set_day_status(today, kuliah, jenis_schema, status)
            await _edit(
                callback,
                f"📝 <b>{status.capitalize()}</b> dicatat untuk <b>{escape(course.nama)}</b>.\n"
                "Tidak ada presensi yang dikirim ke ETHOL.",
            )
            await callback.answer("Tercatat")
            return

        session = await ctx.service.get_active_session(course)
        if session is None or session.key != key:
            await callback.answer("Sesi sudah ditutup", show_alert=True)
            await _edit(callback, "⌛ Sesi presensi sudah ditutup atau berganti.")
            return

        profile = await ctx.auth.get_profile()
        result = await ctx.service.submit(course, session, profile)

        icon = "✅" if result.sukses else "⚠️"
        title = "Presensi berhasil" if result.sukses else "Presensi gagal"
        await _edit(
            callback,
            f"{icon} <b>{title}</b>\n\n"
            f"<b>{escape(course.nama)}</b>\n"
            f"<i>{escape(result.pesan)}</i>",
        )
        await callback.answer(result.pesan[:200], show_alert=not result.sukses)
    except SessionExpired:
        await callback.answer("Sesi ETHOL habis. /login dulu.", show_alert=True)
    except Exception as exc:
        logger.exception("Gagal memproses tombol presensi")
        await callback.answer("Gagal memproses presensi", show_alert=True)
        await _edit(callback, _error_text(exc))
