from __future__ import annotations

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from app.bot.handlers import router
from app.bot.keyboards import attendance_keyboard, main_menu
from app.ethol.models import ActiveSession, Course


def test_main_module_imports() -> None:
    import app.main

    assert callable(app.main.main)


def test_dispatcher_accepts_router() -> None:
    bot = Bot(
        token="1234567890:fake-token",
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher()
    dp["ctx"] = object()
    assert "ctx" in dp.workflow_data
    dp.include_router(router)
    update_types = dp.resolve_used_update_types()
    assert "message" in update_types
    assert "callback_query" in update_types
    assert bot.token == "1234567890:fake-token"


def test_main_menu_keyboard() -> None:
    keyboard = main_menu(True, "confirm")
    assert len(keyboard.inline_keyboard) >= 2
    callbacks = [
        button.callback_data
        for row in keyboard.inline_keyboard
        for button in row
        if button.callback_data
    ]
    assert "menu:toggle" in callbacks
    assert "menu:mode" in callbacks


def test_attendance_callback_data_fits_telegram_limit() -> None:
    course = Course(
        nomor=223349,
        jenis_schema=4,
        nama="Sistem Komunikasi Optik",
    )
    session = ActiveSession(
        kuliah=223349,
        jenis_schema=4,
        key="etuYGfxfu3",
        open=True,
    )
    keyboard = attendance_keyboard(course, session)
    for row in keyboard.inline_keyboard:
        for button in row:
            assert button.callback_data is not None
            assert len(button.callback_data.encode("utf-8")) <= 64
