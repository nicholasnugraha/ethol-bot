from __future__ import annotations

"""Worker entry point; runs Chromium/Xvfb and posts a signed result once."""

import asyncio
import hashlib
import hmac
import json
import os
import time
from urllib.parse import urlsplit

import httpx
from playwright.async_api import async_playwright


def signed_headers(login_id: str, secret: bytes, body: bytes) -> dict[str, str]:
    digest = hmac.new(secret, login_id.encode() + b'.' + body, hashlib.sha256).hexdigest()
    return {'X-Login-Signature': digest, 'Content-Type': 'application/json'}


async def main() -> None:
    login_id = os.environ['LOGIN_ID']
    secret = bytes.fromhex(os.environ['LOGIN_SECRET'])
    gateway = os.environ['GATEWAY_URL'].rstrip('/')
    cas_url = os.environ['CAS_LOGIN_URL']
    ethol_url = os.environ['ETHOL_BASE_URL'].rstrip('/')
    deadline = time.monotonic() + int(os.environ.get('LOGIN_TIMEOUT_SECONDS', '300'))
    browser = None
    completed = False
    error = 'Login did not complete.'
    try:
        async with async_playwright() as p:
            # Headed browser on the worker's private Xvfb display; no browser
            # process or profile is shared with the bot or another login.
            browser = await p.chromium.launch(headless=False, args=['--no-sandbox', '--disable-dev-shm-usage'])
            context = await browser.new_context()
            page = await context.new_page()
            await page.goto(cas_url, wait_until='domcontentloaded', timeout=60000)
            while time.monotonic() < deadline:
                await asyncio.sleep(2)
                current = urlsplit(page.url)
                target = urlsplit(ethol_url)
                if current.hostname and target.hostname and current.hostname.lower() == target.hostname.lower():
                    try:
                        response = await page.request.get(ethol_url + '/api/auth/validasi-token', timeout=8000)
                        if response.ok:
                            payload = await response.json()
                            if isinstance(payload, dict) and payload.get('nomor') is not None:
                                cookies = await context.cookies(ethol_url)
                                body = json.dumps({'cookies': cookies, 'profile': payload}, separators=(',', ':')).encode()
                                async with httpx.AsyncClient(timeout=10) as client:
                                    result = await client.post(f'{gateway}/internal/login/{login_id}/complete', content=body,
                                                               headers=signed_headers(login_id, secret, body))
                                    result.raise_for_status()
                                completed = True
                                return
                    except Exception:
                        pass
                if page.is_closed():
                    break
        error = 'Login timed out or the browser was closed.'
    except Exception:
        error = 'Login worker failed. Please try again.'
    finally:
        if browser:
            try:
                await browser.close()
            except Exception:
                # Playwright may already have torn down its transport on timeout.
                pass
        if not completed:
            body = json.dumps({'message': error}, separators=(',', ':')).encode()
            try:
                async with httpx.AsyncClient(timeout=5) as client:
                    await client.post(f'{gateway}/internal/login/{login_id}/fail', content=body,
                                      headers=signed_headers(login_id, secret, body))
            except Exception:
                pass


if __name__ == '__main__':
    asyncio.run(main())
