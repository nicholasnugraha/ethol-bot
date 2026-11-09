from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Awaitable, Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from playwright.async_api import BrowserContext, Playwright, async_playwright

from app.config import Settings
from app.crypto import SecretBox
from app.db import Database
from app.ethol.client import EtholClient, SessionExpired
from app.ethol.models import SessionProfile

logger = logging.getLogger(__name__)


class LoginError(RuntimeError):
    pass


OnProfile = Callable[[SessionProfile], Awaitable[None]]
OnError = Callable[[str], Awaitable[None]]
OnNotify = Callable[[], Awaitable[None]]


class AuthManager:
    """Owns the ETHOL session: interactive CAS login, cookie storage, refresh."""

    def __init__(self, settings: Settings, db: Database, secret_box: SecretBox) -> None:
        self.settings = settings
        self.db = db
        self.secret_box = secret_box

        self._playwright: Playwright | None = None
        self._context: BrowserContext | None = None
        self._client: EtholClient | None = None
        self._profile: SessionProfile | None = None
        self._profile_json = "{}"
        self._session_invalid_until = 0.0

        self._login_lock = asyncio.Lock()
        self._login_task: asyncio.Task | None = None
        self._expiry_lock = asyncio.Lock()
        self._last_expiry_notice = 0.0

        self.on_login_success: OnProfile | None = None
        self.on_login_failure: OnError | None = None
        self.on_session_expired: OnNotify | None = None

    async def start(self) -> None:
        if self._playwright is not None:
            return
        self._playwright = await async_playwright().start()
        Path(self.settings.browser_profile_dir).mkdir(parents=True, exist_ok=True)
        self._clean_stale_locks()
        self._client = await self.load_client()

    async def stop(self) -> None:
        if self._login_task and not self._login_task.done():
            self._login_task.cancel()
            try:
                await self._login_task
            except asyncio.CancelledError:
                pass

        if self._context is not None:
            try:
                await self._context.close()
            except Exception:
                logger.debug("Gagal menutup context browser", exc_info=True)
            self._context = None

        if self._client is not None:
            await self._client.close()
            self._client = None

        if self._playwright is not None:
            await self._playwright.stop()
            self._playwright = None

    def _clean_stale_locks(self) -> None:
        profile = Path(self.settings.browser_profile_dir)
        for name in ("SingletonLock", "SingletonSocket", "SingletonCookie"):
            lock = profile / name
            try:
                lock.unlink(missing_ok=True)
            except OSError:
                pass

    async def load_client(self) -> EtholClient | None:
        row = await self.db.load_session()
        if row is None:
            return None
        cookies_enc, profile_json = row
        self._profile_json = profile_json or "{}"
        try:
            cookies = self.secret_box.decrypt_json(cookies_enc)
        except ValueError:
            logger.exception("Sesi tersimpan tidak bisa didekripsi; perlu login ulang")
            return None

        return EtholClient(
            base_url=self.settings.ethol_base_url,
            cookies=cookies,
            on_cookies_changed=self._persist_cookies,
        )

    async def _persist_cookies(self, cookies: list[dict[str, Any]]) -> None:
        cookies_enc = self.secret_box.encrypt_json(cookies)
        await self.db.save_session(cookies_enc, self._profile_json)

    async def _persist_session(
        self,
        cookies: list[dict[str, Any]],
        profile: SessionProfile,
    ) -> None:
        self._profile = profile
        self._profile_json = json.dumps(profile.to_dict(), ensure_ascii=False)
        self._session_invalid_until = 0.0
        await self._persist_cookies(cookies)

    async def reload_client(self) -> EtholClient | None:
        if self._client is not None:
            await self._client.close()
        self._client = await self.load_client()
        return self._client

    async def get_client(self) -> EtholClient:
        if self._client is None:
            self._client = await self.load_client()
        if self._client is None:
            self._session_invalid_until = time.monotonic() + 300
            raise SessionExpired("Belum ada sesi ETHOL. Kirim /login untuk memulai.")
        return self._client

    async def get_profile(self) -> SessionProfile:
        if time.monotonic() < self._session_invalid_until:
            raise SessionExpired("Sesi ETHOL tidak valid; login ulang diperlukan")
        client = await self.get_client()
        try:
            profile = await client.ensure_valid()
        except SessionExpired:
            # Back off for 5 minutes; a transient network error should not
            # force an immediate re-login, and a real expiry should not hammer
            # ETHOL every polling tick.
            self._session_invalid_until = time.monotonic() + 300
            raise
        self._profile = profile
        self._profile_json = json.dumps(profile.to_dict(), ensure_ascii=False)
        return profile

    async def has_session(self) -> bool:
        return await self.db.load_session() is not None

    async def session_cookie_expiry(self, name: str = "refresh_token") -> datetime | None:
        row = await self.db.load_session()
        if row is None:
            return None
        cookies_enc, _ = row
        try:
            cookies = self.secret_box.decrypt_json(cookies_enc)
        except ValueError:
            return None
        for cookie in cookies:
            if cookie.get("name") == name:
                expires = cookie.get("expires")
                if isinstance(expires, (int, float)) and expires > 0:
                    return datetime.fromtimestamp(expires).astimezone()
        return None

    async def notify_expired(self) -> None:
        async with self._expiry_lock:
            now = time.monotonic()
            if now - self._last_expiry_notice < 3600:
                return
            self._last_expiry_notice = now
        logger.warning("Sesi ETHOL habis; perlu login ulang")
        if self.on_session_expired:
            await self.on_session_expired()

    # ---- interactive login ----------------------------------------------
    def start_login_background(self) -> None:
        if self._login_task and not self._login_task.done():
            logger.info("Login interaktif sudah berjalan")
            return
        self._login_task = asyncio.create_task(self._login_background())

    async def _login_background(self) -> None:
        try:
            profile = await self.login_interactive()
            if self.on_login_success:
                await self.on_login_success(profile)
        except Exception as exc:
            logger.exception("Login interaktif gagal")
            if self.on_login_failure:
                await self.on_login_failure(str(exc))

    async def login_interactive(self) -> SessionProfile:
        async with self._login_lock:
            if self._playwright is None:
                await self.start()
            assert self._playwright is not None

            if self._context is not None:
                try:
                    await self._context.close()
                except Exception:
                    logger.debug("Gagal menutup context lama", exc_info=True)
                self._context = None

            logger.info("Membuka Chromium untuk login CAS di noVNC")
            context = await self._playwright.chromium.launch_persistent_context(
                user_data_dir=self.settings.browser_profile_dir,
                headless=False,
                args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"],
                viewport={"width": 1280, "height": 800},
                locale="id-ID",
                timezone_id=self.settings.timezone,
            )
            self._context = context

            try:
                page = context.pages[0] if context.pages else await context.new_page()
                await page.goto(
                    self.settings.cas_login_url,
                    wait_until="domcontentloaded",
                    timeout=60_000,
                )
                profile = await self._wait_for_login(context, page)
                cookies = await context.cookies()
                await self._persist_session(cookies, profile)
                await self.reload_client()
                logger.info("Login CAS berhasil untuk %s", profile.nama)
                return profile
            finally:
                if not self.settings.keep_browser_open:
                    try:
                        await context.close()
                    except Exception:
                        logger.debug("Gagal menutup context login", exc_info=True)
                    self._context = None

    async def _wait_for_login(
        self,
        context: BrowserContext,
        page: Any,
    ) -> SessionProfile:
        deadline = time.monotonic() + self.settings.login_timeout_seconds
        while time.monotonic() < deadline:
            if page.is_closed():
                raise LoginError("Jendela browser ditutup sebelum login selesai")
            try:
                # Prefer a same-origin fetch from the page: it definitely uses
                # the browser's cookie jar, including HttpOnly cookies.
                if "ethol.pens.ac.id" in page.url:
                    data = await page.evaluate(
                        """async () => {
                            const r = await fetch('/api/auth/validasi-token', {
                                credentials: 'include'
                            });
                            if (r.status !== 200) return null;
                            return await r.json();
                        }"""
                    )
                    if data and data.get("nomor"):
                        return SessionProfile.from_api(data)

                response = await context.request.get(
                    f"{self.settings.ethol_base_url}/api/auth/validasi-token",
                    timeout=10_000,
                )
                if response.status == 200:
                    return SessionProfile.from_api(await response.json())
            except Exception:  # noqa: BLE001, S110 - expected while user is still logging in
                pass
            await asyncio.sleep(2)
        raise LoginError("Batas waktu login habis. Coba /login lagi.")
