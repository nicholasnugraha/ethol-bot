from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware, Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand, TelegramObject, Message

from app.bot.handlers import router
from app.bot.notifications import Notifier
from app.ethol.auth import AuthManager
from app.ethol.service import EtholService
from app.config import get_settings
from app.context import AppContext
from app.crypto import SecretBox
from app.db import Database
from app.ethol.client import SessionExpired
from app.jobs.daily import DailySummary
from app.jobs.watcher import SessionKeeper, Watcher

logger = logging.getLogger(__name__)


class AllowlistMiddleware(BaseMiddleware):
    def __init__(self, allowed: set[int], runtime: "UserRuntime | None" = None) -> None:
        self.allowed = allowed
        self.runtime = runtime

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
        message = event if isinstance(event, Message) else getattr(event, "message", None)
        chat = getattr(message, "chat", None)
        if chat is None or chat.type != "private" or chat.id != user.id:
            logger.warning("Private chat required for Telegram user %s", user.id)
            return None
        if self.runtime is not None:
            try:
                data["ctx"] = await self.runtime.get(user.id)
            except PermissionError:
                logger.warning("Unregistered Telegram user %s", user.id)
                return None
        return await handler(event, data)


class UserRuntime:
    """Lazily owns one isolated service/auth/notifier/job set per Telegram user."""
    def __init__(self, settings, db, bot, secret_box, broker, allowed_users: set[int]) -> None:
        self.settings, self.db, self.bot = settings, db, bot
        self.allowed_users = frozenset(allowed_users)
        self.secret_box, self.broker = secret_box, broker
        self.contexts: dict[int, AppContext] = {}
        self.jobs: dict[int, tuple[Watcher, SessionKeeper, DailySummary]] = {}
        self._lock = asyncio.Lock()
        self._request_semaphore = asyncio.Semaphore(settings.ethol_max_concurrency)

    async def get(self, user_id: int) -> AppContext:
        if user_id not in self.allowed_users:
            raise PermissionError("User is not allowlisted")
        async with self._lock:
            if user_id in self.contexts:
                return self.contexts[user_id]
            await self.db.register_user(user_id, user_id)
            user_db = self.db.for_user(user_id)
            notifier = Notifier(self.bot, self.settings, recipient_id=user_id)
            auth = AuthManager(self.settings, user_db, self.secret_box,
                               login_broker=self.broker,
                               request_semaphore=self._request_semaphore)
            service = EtholService(auth, user_db, self.settings)
            ctx = AppContext(self.settings, user_db, auth, service, notifier, user_id)
            auth.on_login_success = notifier.login_success
            auth.on_login_failure = notifier.login_failure
            auth.on_session_expired = notifier.session_expired
            await auth.start()
            watcher = Watcher(self.settings, user_db, service, auth, notifier, recipient_id=user_id)
            keeper = SessionKeeper(self.settings, auth)
            daily = DailySummary(self.settings, user_db, service, auth, notifier, recipient_id=user_id)
            await watcher.start()
            await keeper.start()
            await daily.start()
            self.contexts[user_id] = ctx
            self.jobs[user_id] = (watcher, keeper, daily)
            return ctx

    async def restore(self, allowlist: set[int]) -> None:
        users = await self.db.list_users()
        registered = {int(entry["user_id"]) for entry in users}
        for user_id in allowlist:
            if user_id not in registered:
                await self.db.register_user(user_id, user_id)
            await self.get(user_id)

    async def stop(self) -> None:
        for ctx in self.contexts.values():
            watcher, keeper, daily = self.jobs[ctx.user_id]
            await watcher.stop(); await keeper.stop(); await daily.stop(); await ctx.auth.stop()
        self.contexts.clear()
        self.jobs.clear()


async def main() -> None:
    settings = get_settings()
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    db = Database(settings.db_path, legacy_owner_id=settings.legacy_owner_id)
    await db.connect()

    secret_box = SecretBox(settings.encryption_key)
    bot = Bot(
        token=settings.telegram_bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    # LoginBroker is provided by the isolated login-worker package.
    from app.login.broker import LoginBroker
    broker = LoginBroker(settings)
    allowed_users = set(settings.chat_ids)
    runtime = UserRuntime(settings, db, bot, secret_box, broker, allowed_users)

    dp = Dispatcher(storage=MemoryStorage())
    dp["ctx"] = runtime
    allowlist = AllowlistMiddleware(allowed_users, runtime)
    dp.message.middleware(allowlist)
    dp.callback_query.middleware(allowlist)
    dp.include_router(router)

    try:
        await broker.start()
        await runtime.restore(allowed_users)
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
        for user_id in allowed_users:
            ctx = await runtime.get(user_id)
            try:
                await ctx.auth.get_profile()
                await ctx.notifier.send("🤖 Bot absensi ETHOL aktif. Sesi ETHOL valid. Kirim /start untuk menu.")
            except SessionExpired:
                await ctx.auth.notify_expired()
            except Exception:
                logger.exception("Gagal memeriksa sesi ETHOL saat startup untuk user %s", user_id)
                await ctx.notifier.send("⚠️ Bot aktif, tetapi gagal memeriksa sesi ETHOL. Kirim /status atau /login.")

        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        try:
            await runtime.stop()
        finally:
            try:
                await broker.stop()
            finally:
                try:
                    await db.close()
                finally:
                    await bot.session.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot dihentikan")
