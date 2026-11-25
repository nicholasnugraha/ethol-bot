from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any

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
    """Own one user's HTTP session; interactive browsers live in isolated workers."""

    def __init__(self, settings: Settings, db: Database, secret_box: SecretBox,
                 *, login_broker: Any = None,
                 request_semaphore: asyncio.Semaphore | None = None) -> None:
        self.settings = settings
        self.db = db
        self.secret_box = secret_box
        self.login_broker = login_broker
        self.request_semaphore = request_semaphore
        self._client: EtholClient | None = None
        self._profile: SessionProfile | None = None
        self._profile_json = "{}"
        self._session_invalid_until = 0.0
        self._login_lock = asyncio.Lock()
        self._client_lock = asyncio.Lock()
        self._expiry_lock = asyncio.Lock()
        self._last_expiry_notice = 0.0
        self.on_login_success: OnProfile | None = None
        self.on_login_failure: OnError | None = None
        self.on_session_expired: OnNotify | None = None

    async def start(self) -> None:
        async with self._client_lock:
            if self._client is None:
                self._client = await self.load_client()

    async def stop(self) -> None:
        async with self._client_lock:
            if self._client is not None:
                await self._client.close()
                self._client = None

    async def load_client(self) -> EtholClient | None:
        row = await self.db.load_session_versioned()
        if row is None:
            return None
        cookies_enc, profile_json, version = row
        self._profile_json = profile_json or "{}"
        try:
            cookies = self.secret_box.decrypt_json(cookies_enc)
        except ValueError:
            logger.warning("Stored session cannot be decrypted for user %s", self.db.user_id)
            return None

        async def persist(cookies: list[dict[str, Any]]) -> None:
            # Capture this client's generation, not mutable manager state. A
            # refresh from an old client must never replace a newer login.
            await self.db.save_session_if_version(
                self.secret_box.encrypt_json(cookies), profile_json, version,
            )

        return EtholClient(base_url=self.settings.ethol_base_url,
                           cookies=cookies, on_cookies_changed=persist,
                           request_semaphore=self.request_semaphore)

    async def _persist_session(self, cookies: list[dict[str, Any]],
                               profile: SessionProfile) -> None:
        self._profile = profile
        self._profile_json = json.dumps(profile.to_dict(), ensure_ascii=False)
        self._session_invalid_until = 0.0
        await self.db.save_session(self.secret_box.encrypt_json(cookies), self._profile_json)

    async def reload_client(self) -> EtholClient | None:
        async with self._client_lock:
            if self._client is not None:
                await self._client.close()
            self._client = await self.load_client()
            return self._client

    async def get_client(self) -> EtholClient:
        if self._client is None:
            await self.start()
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
        try:
            cookies = self.secret_box.decrypt_json(row[0])
        except ValueError:
            return None
        for cookie in cookies:
            expires = cookie.get("expires")
            if cookie.get("name") == name and isinstance(expires, (int, float)) and expires > 0:
                return datetime.fromtimestamp(expires).astimezone()
        return None

    async def notify_expired(self) -> None:
        async with self._expiry_lock:
            now = time.monotonic()
            if now - self._last_expiry_notice < 3600:
                return
            self._last_expiry_notice = now
        if self.on_session_expired:
            await self.on_session_expired()

    async def create_login_url(self) -> str:
        if self.login_broker is None:
            raise LoginError("Isolated login broker is not configured")
        async with self._login_lock:
            async def success(cookies: list[dict[str, Any]], profile: SessionProfile) -> None:
                if isinstance(profile, dict):
                    profile = SessionProfile.from_api(profile)
                # Account switching is intentionally forbidden until an explicit
                # unlink/relink flow exists. Never silently replace an identity.
                row = await self.db.load_session()
                if row:
                    old = json.loads(row[1] or "{}")
                    if old.get("nomor") and str(old["nomor"]) != str(profile.nomor):
                        raise LoginError("Akun ETHOL berbeda; hubungi administrator untuk unlink sebelum mengganti akun.")
                await self._persist_session(cookies, profile)
                await self.reload_client()
                if self.on_login_success:
                    try:
                        await self.on_login_success(profile)
                    except Exception:
                        logger.exception("Login saved but success notification could not be delivered")

            async def failure(message: str) -> None:
                if self.on_login_failure:
                    await self.on_login_failure(message)

            return await self.login_broker.create(self.db.user_id, success, failure)
