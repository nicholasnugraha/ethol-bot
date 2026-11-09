from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

import httpx

from app.ethol.models import (
    ActiveSession,
    Course,
    HistoryItem,
    ScheduleItem,
    SessionProfile,
)

logger = logging.getLogger(__name__)


class EtholError(RuntimeError):
    pass


class SessionExpired(EtholError):
    pass


CookieList = list[dict[str, Any]]
OnCookiesChanged = Callable[[CookieList], Awaitable[None]]


class EtholClient:
    """HTTP client for ETHOL's internal API, backed by captured session cookies."""

    def __init__(
        self,
        base_url: str,
        cookies: CookieList | None = None,
        on_cookies_changed: OnCookiesChanged | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._on_cookies_changed = on_cookies_changed
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=httpx.Timeout(20.0, connect=10.0),
            follow_redirects=False,
            transport=transport,
            headers={
                "Accept": "application/json, text/plain, */*",
                "Accept-Language": "id-ID,id;q=0.9,en;q=0.8",
                "Origin": self.base_url,
                "Referer": f"{self.base_url}/mahasiswa/beranda",
                "User-Agent": (
                    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
                ),
            },
        )
        if cookies:
            self.load_cookies(cookies)

    def load_cookies(self, cookies: CookieList) -> None:
        for cookie in cookies:
            name = cookie.get("name")
            value = cookie.get("value")
            if not name or value is None:
                continue
            domain = cookie.get("domain") or "ethol.pens.ac.id"
            path = cookie.get("path") or "/"
            try:
                self._client.cookies.set(name, value, domain=domain, path=path)
            except Exception:  # noqa: BLE001 - skip malformed cookie
                logger.warning("Gagal memuat cookie %s", name)

    def export_cookies(self) -> CookieList:
        return [
            {
                "name": cookie.name,
                "value": cookie.value,
                "domain": cookie.domain,
                "path": cookie.path,
                "expires": cookie.expires,
                "secure": cookie.secure,
            }
            for cookie in self._client.cookies.jar
        ]

    async def close(self) -> None:
        await self._client.aclose()

    async def refresh(self) -> bool:
        """Rotate the short-lived token using refresh_token cookie."""
        try:
            response = await self._client.post("/api/auth/refresh")
        except httpx.HTTPError as exc:
            logger.warning("Gagal menghubungi endpoint refresh: %s", exc)
            return False

        if response.status_code != 200:
            logger.warning("Refresh token ditolak: HTTP %s", response.status_code)
            return False

        if self._on_cookies_changed:
            try:
                await self._on_cookies_changed(self.export_cookies())
            except Exception:  # cookie persistence must not break requests
                logger.exception("Gagal menyimpan cookie setelah refresh")
        logger.debug("Refresh token berhasil")
        return True

    async def _request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        response = await self._client.request(method, url, **kwargs)
        if response.status_code == 401:
            logger.info("Token kedaluwarsa saat %s %s; mencoba refresh", method, url)
            if await self.refresh():
                response = await self._client.request(method, url, **kwargs)
            if response.status_code == 401:
                raise SessionExpired("Sesi ETHOL habis dan refresh gagal")

        if response.status_code >= 400:
            raise EtholError(
                f"ETHOL {method} {url} -> HTTP {response.status_code}: "
                f"{response.text[:300]}"
            )

        if response.is_redirect:
            location = response.headers.get("location", "")
            if "login.pens.ac.id" in location or "/auth/cas" in location:
                raise SessionExpired("Sesi ETHOL dialihkan ke halaman login")

        return response

    async def validasi_token(self) -> SessionProfile | None:
        response = await self._client.get("/api/auth/validasi-token")
        if response.status_code == 401 and await self.refresh():
            response = await self._client.get("/api/auth/validasi-token")
        if response.status_code != 200:
            return None
        return SessionProfile.from_api(response.json())

    async def ensure_valid(self) -> SessionProfile:
        profile = await self.validasi_token()
        if profile is None:
            raise SessionExpired("Sesi ETHOL tidak valid")
        return profile

    async def get_config(self) -> dict[str, Any]:
        response = await self._request("GET", "/api/auth/config")
        return response.json()

    async def get_courses(self, tahun: int, semester: int) -> list[Course]:
        response = await self._request(
            "GET",
            "/api/kuliah",
            params={"tahun": tahun, "semester": semester},
        )
        data = response.json()
        if not isinstance(data, list):
            raise EtholError("Respons /api/kuliah tidak berupa list")
        return [Course.from_api(item) for item in data]

    async def get_schedule(
        self,
        courses: list[Course],
        tahun: int,
        semester: int,
    ) -> list[ScheduleItem]:
        payload = {
            "kuliahs": [
                {"nomor": course.nomor, "jenisSchema": course.jenis_schema}
                for course in courses
            ],
            "tahun": tahun,
            "semester": semester,
        }
        response = await self._request("POST", "/api/kuliah/hari-kuliah-in", json=payload)
        data = response.json()
        if not isinstance(data, list):
            raise EtholError("Respons /api/kuliah/hari-kuliah-in tidak berupa list")

        course_map = {course.nomor: course for course in courses}
        return [ScheduleItem.from_api(item, course_map) for item in data]

    async def get_active_sessions(
        self,
        kuliah: int,
        jenis_schema: int,
    ) -> list[ActiveSession]:
        response = await self._request(
            "GET",
            "/api/presensi/aktif-kuliah",
            params={"kuliah": kuliah, "jenis_schema": jenis_schema},
        )
        data = response.json()
        if not isinstance(data, list):
            return []
        return [
            ActiveSession.from_api(item, kuliah, jenis_schema)
            for item in data
        ]

    async def get_history(
        self,
        kuliah: int,
        jenis_schema: int,
        mahasiswa: int,
    ) -> list[HistoryItem]:
        response = await self._request(
            "GET",
            "/api/presensi/riwayat",
            params={
                "kuliah": kuliah,
                "jenis_schema": jenis_schema,
                "nomor": mahasiswa,
            },
        )
        data = response.json()
        if not isinstance(data, list):
            return []
        return [HistoryItem.from_api(item) for item in data]

    async def submit_presensi(
        self,
        kuliah: int,
        jenis_schema: int,
        mahasiswa: int,
        key: str,
        kuliah_asal: int | None,
    ) -> dict[str, Any]:
        response = await self._request(
            "POST",
            "/api/presensi/mahasiswa",
            json={
                "kuliah": kuliah,
                "jenis_schema": jenis_schema,
                "mahasiswa": mahasiswa,
                "key": key,
                "kuliah_asal": kuliah_asal,
            },
        )
        return response.json()
