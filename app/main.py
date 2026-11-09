from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware, Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand, TelegramObject

from app.bot.handlers import router
from app.bot.notifications import Notifier
from app.config import get_settings
from app.context import AppContext
from app.crypto import SecretBox
from app.db import Database
from app.ethol.auth import AuthManager
from app.ethol.client import SessionExpired
from app.ethol.service import EtholService
from app.jobs.daily import DailySummary
from app.jobs.watcher import SessionKeeper, Watcher

logger = logging.getLogger(__name__)


class AllowlistMiddleware(BaseMiddleware):
    def __init__(self, allowed: set[int]) -> None:
        self.allowed = allowed

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user = data.get("event_from_user")
        if user is None or user.id not in self.allowed:
            if user:
                logger.warning("Akses ditolak untuk Telegram user %s", user.id)
            return None
        return await handler(event, data)


async def main() -> None:
    settings = get_settings()
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    db = Database(settings.db_path)
    await db.connect()

    secret_box = SecretBox(settings.encryption_key)
    auth = AuthManager(settings, db, secret_box)
    await auth.start()
    service = EtholService(auth, db, settings)

    bot = Bot(
        token=settings.telegram_bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    notifier = Notifier(bot, settings)
    ctx = AppContext(settings=settings, db=db, auth=auth, service=service, notifier=notifier)

    dp = Dispatcher(storage=MemoryStorage())
    dp["ctx"] = ctx
    allowlist = AllowlistMiddleware(set(settings.chat_ids))
    dp.message.middleware(allowlist)
    dp.callback_query.middleware(allowlist)
    dp.include_router(router)

    auth.on_login_success = notifier.login_success
    auth.on_login_failure = notifier.login_failure
    auth.on_session_expired = notifier.session_expired

    watcher = Watcher(settings, db, service, auth, notifier)
    session_keeper = SessionKeeper(settings, auth)
    daily = DailySummary(settings, db, service, auth, notifier)

    await watcher.start()
    await session_keeper.start()
    await daily.start()

    try:
        try:
            await bot.set_my_commands(
                [
                    BotCommand(command="start", description="Menu utama"),
                    BotCommand(command="hariini", description="Mata kuliah hari ini"),
                    BotCommand(command="status", description="Status bot"),
                    BotCommand(command="on", description="Aktifkan absensi"),
                    BotCommand(command="off", description="Matikan absensi"),
                    BotCommand(command="mode", description="Konfirmasi / Auto"),
                    BotCommand(command="login", description="Login via noVNC"),
                    BotCommand(command="log", description="Log presensi"),
                    BotCommand(command="izin", description="Tandai izin"),
                    BotCommand(command="sakit", description="Tandai sakit"),
                    BotCommand(command="batal", description="Hapus status izin/sakit"),
                ]
            )
        except Exception:
            logger.exception("Gagal mengatur daftar perintah Telegram")

        logger.info("Bot absensi ETHOL aktif")
        try:
            await auth.get_profile()
            await notifier.broadcast(
                "🤖 Bot absensi ETHOL aktif. Sesi ETHOL valid. Kirim /start untuk menu."
            )
        except SessionExpired:
            await notifier.session_expired()
        except Exception:
            logger.exception("Gagal memeriksa sesi ETHOL saat startup")
            await notifier.broadcast(
                "⚠️ Bot aktif, tetapi gagal memeriksa sesi ETHOL. Kirim /status atau /login."
            )

        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        await watcher.stop()
        await session_keeper.stop()
        await daily.stop()
        await auth.stop()
        await db.close()
        await bot.session.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot dihentikan")
