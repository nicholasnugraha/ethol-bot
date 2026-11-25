from __future__ import annotations

import hashlib
import hmac
import json
import time
from types import SimpleNamespace

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from app.login.broker import LoginBroker, _Login


def make_settings():
    return SimpleNamespace(
        gateway_host='127.0.0.1', gateway_port=8080, public_base_url='https://bot.example.test',
        login_cookie_secure=False, login_timeout_seconds=300, ethol_base_url='https://ethol.pens.ac.id',
    )


async def make_client(broker: LoginBroker) -> TestClient:
    app = web.Application(client_max_size=1024 * 1024)
    app.on_response_prepare.append(broker._security_headers)
    app.router.add_get('/login/{login_id}', broker._login_page)
    app.router.add_get('/login/{login_id}/', broker._login_page)
    app.router.add_get('/login/{login_id}/{asset:.*}', broker._proxy)
    app.router.add_post('/internal/login/{login_id}/complete', broker._complete)
    app.router.add_post('/internal/login/{login_id}/fail', broker._fail)
    client = TestClient(TestServer(app))
    await client.start_server()
    return client


async def test_bootstrap_link_is_single_use_and_sets_path_cookie():
    broker = LoginBroker(make_settings(), docker_client=object())
    login = _Login('42', b'secret', time.monotonic(), worker_host='127.0.0.1')
    login.bootstrap_secret = 'bootstrap-secret'
    broker._logins['login-id'] = login
    client = await make_client(broker)
    try:
        response = await client.get('/login/login-id?token=bootstrap-secret', allow_redirects=False)
        assert response.status == 302
        assert 'vnc.html' in response.headers['Location']
        cookie = response.cookies['login_proxy']
        assert cookie['httponly'] is True
        assert cookie['path'] == '/login/login-id/'
        assert (await client.get('/login/login-id?token=bootstrap-secret', allow_redirects=False)).status == 403
        login.done = True
        assert (await client.get('/login/login-id?token=bootstrap-secret', allow_redirects=False)).status == 410
    finally:
        await client.close()


async def test_internal_completion_signature_domain_filter_and_one_shot():
    broker = LoginBroker(make_settings(), docker_client=object())
    received = []

    async def success(cookies, profile):
        received.append((cookies, profile.nomor))

    login_id = 'id-for-test'
    broker._logins[login_id] = _Login('42', b'secret', time.monotonic(), on_success=success)
    client = await make_client(broker)
    try:
        payload = {
            'cookies': [
                {'name': 'token', 'value': 'private-cookie', 'domain': '.ethol.pens.ac.id', 'path': '/'},
                {'name': 'bad', 'value': 'no', 'domain': 'attacker.invalid', 'path': '/'},
            ],
            'profile': {'nomor': 42, 'nipnrp': 'a', 'nama': 'Test', 'hakAkses': ['mahasiswa']},
        }
        body = json.dumps(payload, separators=(',', ':')).encode()
        assert (await client.post(f'/internal/login/{login_id}/complete', data=body)).status == 401
        signature = hmac.new(b'secret', login_id.encode() + b'.' + body, hashlib.sha256).hexdigest()
        response = await client.post(f'/internal/login/{login_id}/complete', data=body,
                                     headers={'X-Login-Signature': signature})
        assert response.status == 400
        assert received == []
        payload['cookies'].pop()
        body = json.dumps(payload, separators=(',', ':')).encode()
        signature = hmac.new(b'secret', login_id.encode() + b'.' + body, hashlib.sha256).hexdigest()
        assert (await client.post(f'/internal/login/{login_id}/complete', data=body,
                                  headers={'X-Login-Signature': signature})).status == 200
        assert received[0][0][0]['value'] == 'private-cookie'
        assert received[0][1] == 42
        assert (await client.post(f'/internal/login/{login_id}/complete', data=body,
                                  headers={'X-Login-Signature': signature})).status == 409
    finally:
        await client.close()


async def test_static_proxy_auth_restricts_path_and_round_trips_upstream(monkeypatch):
    broker = LoginBroker(make_settings(), docker_client=object())
    login = _Login('42', b'secret', time.monotonic(), worker_host='127.0.0.1')
    broker._logins['login-id'] = login

    class FakeResponse:
        status_code = 200
        content = b'fake-novnc-js'
        headers = {'content-type': 'application/javascript'}

    class FakeAsyncClient:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def get(self, url, **kwargs):
            assert url == 'http://127.0.0.1:6080/core/util/browser.js'
            assert kwargs['headers']['X-Auth-Token'] == login.proxy_token
            return FakeResponse()

    monkeypatch.setattr('app.login.broker.httpx.AsyncClient', FakeAsyncClient)
    client = await make_client(broker)
    try:
        assert (await client.get('/login/login-id/core/util/browser.js')).status == 403
        client.session.cookie_jar.update_cookies(
            {'login_proxy': login.proxy_token}, response_url=client.make_url('/login/login-id/')
        )
        response = await client.get('/login/login-id/core/util/browser.js')
        assert response.status == 200
        assert await response.read() == b'fake-novnc-js'
        assert response.headers['Cache-Control'] == 'no-store'
        assert (await client.get('/login/login-id/../../internal/login/id/complete')).status in {403, 404, 405}
    finally:
        await client.close()


async def test_websocket_binary_roundtrip_and_teardown(monkeypatch):
    import asyncio
    from aiohttp import WSMsgType

    class Upstream:
        def __init__(self):
            self.queue = asyncio.Queue()
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            pass
        async def send(self, data):
            await self.queue.put(data)
        def __aiter__(self):
            return self
        async def __anext__(self):
            return await self.queue.get()

    upstream = Upstream()
    def connect(url, **kwargs):
        assert 'token=' in url
        assert kwargs['additional_headers']['Authorization'].startswith('Basic ')
        return upstream
    monkeypatch.setattr('app.login.broker.websockets.connect', connect)
    broker = LoginBroker(make_settings(), docker_client=object())
    login = _Login('42', b'secret', time.monotonic(), worker_host='127.0.0.1')
    broker._logins['login-id'] = login
    client = await make_client(broker)
    try:
        headers = {'Origin': 'https://bot.example.test',
                   'Cookie': f'other=value; login_proxy={login.proxy_token}'}
        ws = await client.ws_connect('/login/login-id/websockify', headers=headers)
        await ws.send_bytes(b'RFB-test')
        message = await ws.receive(timeout=2)
        assert message.type == WSMsgType.BINARY
        assert message.data == b'RFB-test'
        await broker._destroy('login-id', login)
        assert (await ws.receive(timeout=2)).type in {WSMsgType.CLOSE, WSMsgType.CLOSED}
    finally:
        await client.close()


async def test_websocket_requires_cookie_origin_and_only_websockify():
    broker = LoginBroker(make_settings(), docker_client=object())
    login = _Login('42', b'secret', time.monotonic(), worker_host='127.0.0.1')
    broker._logins['login-id'] = login
    client = await make_client(broker)
    try:
        response = await client.get('/login/login-id/websockify', headers={'Upgrade': 'websocket', 'Origin': 'https://bot.example.test'})
        assert response.status in {400, 403}
    finally:
        await client.close()
