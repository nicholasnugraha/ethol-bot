from __future__ import annotations

import httpx


def test_cookie_path_rules() -> None:
    cookies = httpx.Cookies()
    cookies.set("token", "access", domain="ethol.pens.ac.id", path="/")
    cookies.set("refresh_token", "refresh", domain="ethol.pens.ac.id", path="/api/auth")

    client = httpx.Client(cookies=cookies)

    refresh_request = client.build_request(
        "POST", "https://ethol.pens.ac.id/api/auth/refresh"
    )
    refresh_cookie = refresh_request.headers.get("cookie", "")
    assert "token=access" in refresh_cookie
    assert "refresh_token=refresh" in refresh_cookie

    courses_request = client.build_request(
        "GET", "https://ethol.pens.ac.id/api/kuliah?tahun=2026&semester=1"
    )
    courses_cookie = courses_request.headers.get("cookie", "")
    assert "token=access" in courses_cookie
    assert "refresh_token=refresh" not in courses_cookie


def test_cookie_parent_domain() -> None:
    cookies = httpx.Cookies()
    cookies.set("token", "access", domain=".pens.ac.id", path="/")
    client = httpx.Client(cookies=cookies)
    request = client.build_request("GET", "https://ethol.pens.ac.id/api/auth/config")
    assert "token=access" in request.headers.get("cookie", "")
