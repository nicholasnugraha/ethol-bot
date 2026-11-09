from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from app.ethol.client import EtholClient, SessionExpired
from app.ethol.models import Course

BASE = "https://ethol.pens.ac.id"
COOKIES = [
    {
        "name": "token",
        "value": "old-token",
        "domain": "ethol.pens.ac.id",
        "path": "/",
    },
    {
        "name": "refresh_token",
        "value": "refresh-value",
        "domain": "ethol.pens.ac.id",
        "path": "/api/auth",
    },
]


async def test_get_courses_and_active_sessions() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert "token=old-token" in request.headers.get("cookie", "")
        if request.url.path == "/api/kuliah":
            assert request.url.params["tahun"] == "2026"
            assert request.url.params["semester"] == "1"
            return httpx.Response(
                200,
                json=[
                    {
                        "nomor": 223349,
                        "jenisSchema": 4,
                        "matakuliah": {"nama": "Sistem Komunikasi Optik"},
                        "dosen": "Karimatun Nisa",
                    }
                ],
            )
        if request.url.path == "/api/presensi/aktif-kuliah":
            assert request.url.params["kuliah"] == "223349"
            assert request.url.params["jenis_schema"] == "4"
            return httpx.Response(
                200,
                json=[
                    {
                        "kuliah": 223349,
                        "jenis_schema": 4,
                        "key": "abc123",
                        "open": 1,
                    }
                ],
            )
        return httpx.Response(404)

    client = EtholClient(BASE, COOKIES, transport=httpx.MockTransport(handler))
    try:
        courses = await client.get_courses(2026, 1)
        assert len(courses) == 1
        assert courses[0].nama == "Sistem Komunikasi Optik"

        sessions = await client.get_active_sessions(223349, 4)
        assert len(sessions) == 1
        assert sessions[0].key == "abc123"
        assert sessions[0].open is True
    finally:
        await client.close()


async def test_schedule_payload() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/kuliah/hari-kuliah-in":
            captured.update(json.loads(request.content))
            return httpx.Response(
                200,
                json=[
                    {
                        "kuliah": 223349,
                        "hari": "Senin",
                        "jam_awal": "10:30",
                        "jam_akhir": "12:10",
                        "nomor_hari": 1,
                        "ruang": "SAW-05.07",
                    }
                ],
            )
        return httpx.Response(404)

    client = EtholClient(BASE, COOKIES, transport=httpx.MockTransport(handler))
    try:
        courses = [
            Course(
                nomor=223349,
                jenis_schema=4,
                nama="Sistem Komunikasi Optik",
            )
        ]
        schedule = await client.get_schedule(courses, 2026, 1)
        assert captured["kuliahs"] == [{"nomor": 223349, "jenisSchema": 4}]
        assert captured["tahun"] == 2026
        assert captured["semester"] == 1
        assert schedule[0].nama == "Sistem Komunikasi Optik"
    finally:
        await client.close()


async def test_submit_payload() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/presensi/mahasiswa":
            captured.update(json.loads(request.content))
            return httpx.Response(
                200,
                json={"sukses": True, "pesan": "Presensi berhasil dicatat"},
            )
        return httpx.Response(404)

    client = EtholClient(BASE, COOKIES, transport=httpx.MockTransport(handler))
    try:
        response = await client.submit_presensi(
            kuliah=223349,
            jenis_schema=4,
            mahasiswa=27850,
            key="abc123",
            kuliah_asal=None,
        )
        assert response["sukses"] is True
        assert captured == {
            "kuliah": 223349,
            "jenis_schema": 4,
            "mahasiswa": 27850,
            "key": "abc123",
            "kuliah_asal": None,
        }
    finally:
        await client.close()


async def test_refresh_on_401_and_persist_cookies() -> None:
    calls: list[str] = []
    changed: list[list[dict[str, Any]]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path == "/api/auth/refresh":
            assert "refresh_token=refresh-value" in request.headers.get("cookie", "")
            return httpx.Response(
                200,
                headers={"set-cookie": "token=new-token; Path=/; HttpOnly"},
                json={"sukses": True},
            )
        if request.url.path == "/api/kuliah":
            if "token=new-token" not in request.headers.get("cookie", ""):
                return httpx.Response(401, json={"message": "expired"})
            return httpx.Response(200, json=[])
        return httpx.Response(404)

    async def on_changed(cookies: list[dict[str, Any]]) -> None:
        changed.append(cookies)

    client = EtholClient(
        BASE,
        COOKIES,
        on_cookies_changed=on_changed,
        transport=httpx.MockTransport(handler),
    )
    try:
        courses = await client.get_courses(2026, 1)
        assert courses == []
        assert calls == ["/api/kuliah", "/api/auth/refresh", "/api/kuliah"]
        assert any(cookie["name"] == "token" and cookie["value"] == "new-token" for cookie in changed[0])
    finally:
        await client.close()


async def test_refresh_failure_raises_session_expired() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"message": "expired"})

    client = EtholClient(BASE, COOKIES, transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(SessionExpired):
            await client.get_courses(2026, 1)
    finally:
        await client.close()


async def test_redirect_to_login_raises_session_expired() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            302,
            headers={"location": "https://login.pens.ac.id/cas/login?service=x"},
        )

    client = EtholClient(BASE, COOKIES, transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(SessionExpired):
            await client.get_courses(2026, 1)
    finally:
        await client.close()


async def test_validasi_token_profile() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "nomor": 27850,
                "nipnrp": "2225600043",
                "nama": "Nicholas",
                "hakAkses": ["mahasiswa"],
            },
        )

    client = EtholClient(BASE, COOKIES, transport=httpx.MockTransport(handler))
    try:
        profile = await client.ensure_valid()
        assert profile.nomor == 27850
        assert profile.hak_akses == ["mahasiswa"]
    finally:
        await client.close()
