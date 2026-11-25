from __future__ import annotations

"""Isolated ephemeral browser workers and a private login-result gateway.

The gateway only accepts authenticated worker callbacks; it is not a proxy.
"""

import asyncio
import hashlib
import hmac
import json
import logging
import secrets
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote, urlsplit

from aiohttp import web
import httpx
import websockets

from app.ethol.models import SessionProfile

log = logging.getLogger(__name__)


@dataclass
class _Login:
    user_id: str
    secret: bytes
    created: float
    container: Any = None
    task: asyncio.Task[Any] | None = None
    done: bool = False
    callback_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    on_success: Any = None
    on_failure: Any = None
    worker_host: str | None = None
    proxy_token: str = field(default_factory=lambda: secrets.token_urlsafe(24))
    bootstrap_secret: str = field(default_factory=lambda: secrets.token_urlsafe(32))
    bootstrap_used: bool = False
    browser_ws: set[Any] = field(default_factory=set)


class LoginBroker:
    """Manage bounded Docker workers and signed, one-shot login callbacks."""

    def __init__(self, settings: Any, *, docker_client: Any = None) -> None:
        self.settings = settings
        self._docker = docker_client
        self._owns_docker = docker_client is None
        self._logins: dict[str, _Login] = {}
        self._lock = asyncio.Lock()
        self._runner: web.AppRunner | None = None
        self._site: web.TCPSite | None = None
        self._sweeper: asyncio.Task[None] | None = None
        self._closed = False
        self._deployment_id = str(getattr(settings, 'login_deployment_id', 'default'))

    async def start(self) -> None:
        if self._runner:
            return
        if self._docker is None:
            import docker
            self._docker = docker.from_env()
        app = web.Application(client_max_size=1024 * 1024)
        app.on_response_prepare.append(self._security_headers)
        app.router.add_get('/login/{login_id}', self._login_page)
        app.router.add_get('/login/{login_id}/', self._login_page)
        app.router.add_get('/login/{login_id}/{asset:.*}', self._proxy)
        app.router.add_post('/internal/login/{login_id}/complete', self._complete)
        app.router.add_post('/internal/login/{login_id}/fail', self._fail)
        self._runner = web.AppRunner(app, access_log=None)
        await self._runner.setup()
        self._site = web.TCPSite(self._runner, self.settings.gateway_host, self.settings.gateway_port)
        await self._site.start()
        self._closed = False
        # Recover only workers created by this explicit deployment. Do not touch
        # other deployments sharing the Docker daemon.
        await self._remove_orphan_workers()
        self._sweeper = asyncio.create_task(self._sweep(), name='login-broker-sweeper')

    async def _remove_orphan_workers(self) -> None:
        try:
            containers = await asyncio.to_thread(
                self._docker.containers.list,
                all=True,
                filters={'label': [
                    'ethol.login-worker=true',
                    f'ethol.deployment={self._deployment_id}',
                ]},
            )
            for container in containers:
                try:
                    await asyncio.to_thread(container.remove, force=True, v=True)
                except Exception:
                    log.warning('Could not remove stale login worker %s', container.id[:12])
        except Exception:
            log.exception('Could not clean stale login workers')

    async def stop(self) -> None:
        self._closed = True
        if self._sweeper:
            self._sweeper.cancel()
            await asyncio.gather(self._sweeper, return_exceptions=True)
            self._sweeper = None
        async with self._lock:
            items = list(self._logins.items())
        for login_id, login in items:
            await self._destroy(login_id, login)
        if self._runner:
            await self._runner.cleanup()
            self._runner = None
            self._site = None
        if self._owns_docker and self._docker is not None:
            await asyncio.to_thread(self._docker.close)
            self._docker = None

    async def create(self, user_id: str, on_success: Any, on_failure: Any) -> str:
        if not self._runner:
            raise RuntimeError('LoginBroker.start() must be called before create()')
        await self._cleanup_expired()
        login_id = secrets.token_urlsafe(24)
        login = _Login(str(user_id), secrets.token_bytes(32), time.monotonic(), on_success=on_success, on_failure=on_failure)
        async with self._lock:
            if self._closed:
                raise RuntimeError('Login broker is stopped')
            if any(x.user_id == login.user_id and not x.done for x in self._logins.values()):
                raise RuntimeError('A login is already active for this user')
            maximum = int(self.settings.login_max_workers)
            if sum(not x.done for x in self._logins.values()) >= maximum:
                raise RuntimeError('Login capacity is busy; try again shortly')
            self._logins[login_id] = login
        try:
            info = await asyncio.to_thread(self._create_worker, login_id, login)
            login.container = info
            login.worker_host = self._container_address(info)
            await self._wait_for_worker(info, int(self.settings.login_timeout_seconds))
            login.task = asyncio.create_task(self._watch(login_id, login, on_success, on_failure))
        except Exception:
            await self._destroy(login_id, login)
            log.exception('Could not start isolated login worker')
            raise RuntimeError('Unable to start secure login worker') from None
        base = str(self.settings.public_base_url).rstrip('/')
        return f'{base}/login/{login_id}?token={login.bootstrap_secret}'

    def _create_worker(self, login_id: str, login: _Login) -> Any:
        labels = {
            'app': 'ethol-login-worker',
            'ethol.login-worker': 'true',
            'ethol.deployment': self._deployment_id,
            'login_id': login_id,
        }
        container = self._docker.containers.run(
            self.settings.login_worker_image,
            detach=True,
            auto_remove=False,
            labels=labels,
            network=self.settings.login_worker_network,
            environment={
                'LOGIN_ID': login_id,
                'LOGIN_SECRET': login.secret.hex(),
                'PROXY_TOKEN': login.proxy_token,
                'GATEWAY_URL': f'http://ethol-bot:{self.settings.gateway_port}',
                'CAS_LOGIN_URL': self.settings.cas_login_url,
                'ETHOL_BASE_URL': self.settings.ethol_base_url,
                'LOGIN_TIMEOUT_SECONDS': str(self.settings.login_timeout_seconds),
                'NOVNC_PUBLIC_URL': str(self.settings.public_base_url).rstrip('/') + f'/login/{login_id}',
                'COOKIE_SECURE': str(bool(self.settings.login_cookie_secure)).lower(),
            },
            shm_size='512m',
            mem_limit='1g',
            nano_cpus=1_000_000_000,
            pids_limit=256,
            cap_drop=['ALL'],
            security_opt=['no-new-privileges:true'],
            read_only=True,
            tmpfs={'/tmp': 'rw,noexec,nosuid,size=256m', '/run': 'rw,noexec,nosuid,size=32m'},
        )
        return container

    async def _watch(self, login_id: str, login: _Login, on_success: Any, on_failure: Any) -> None:
        timeout = int(self.settings.login_timeout_seconds)
        deadline = time.monotonic() + timeout
        try:
            while not login.done and time.monotonic() < deadline:
                await asyncio.sleep(1)
                state = await asyncio.to_thread(self._worker_state, login.container)
                if state == 'dead' and not login.done:
                    async with login.callback_lock:
                        if not login.done:
                            login.done = True
                            await on_failure('Browser login worker stopped before completing.')
                    break
            if not login.done:
                async with login.callback_lock:
                    if not login.done:
                        login.done = True
                        await on_failure('Login timed out. Start a new login and try again.')
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception('Login worker monitor failed')
            if not login.done:
                await on_failure('Login worker failed. Start a new login and try again.')
        finally:
            await self._destroy(login_id, login)

    async def _wait_for_worker(self, container: Any, timeout: int) -> None:
        deadline = time.monotonic() + min(timeout, 45)
        while time.monotonic() < deadline:
            state = await asyncio.to_thread(self._worker_state, container)
            if state == 'dead':
                raise RuntimeError('Login worker exited during startup')
            try:
                address = self._container_address(container)
                async with httpx.AsyncClient(timeout=1) as client:
                    response = await client.get(f'http://{address}:6080/vnc.html',
                                                headers={'Authorization': self._basic_auth(self._logins[next(k for k,v in self._logins.items() if v.container is container)].proxy_token)})
                    if response.status_code in {200, 401}:
                        return
            except Exception:
                pass
            await asyncio.sleep(0.5)
        raise RuntimeError('Login worker readiness timed out')

    @staticmethod
    def _basic_auth(token: str) -> str:
        import base64
        return 'Basic ' + base64.b64encode(f'gateway:{token}'.encode()).decode()

    @staticmethod
    def _worker_state(container: Any) -> str:
        try:
            container.reload()
            return 'dead' if container.status in {'exited', 'dead', 'removing'} else 'running'
        except Exception:
            return 'dead'

    @staticmethod
    async def _security_headers(request: web.Request, response: web.StreamResponse) -> None:
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'no-referrer'
        response.headers['Content-Security-Policy'] = "default-src 'self'; style-src 'self' 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'"

    @staticmethod
    def _container_address(container: Any) -> str:
        container.reload()
        networks = container.attrs.get('NetworkSettings', {}).get('Networks', {})
        for network in networks.values():
            address = network.get('IPAddress')
            if address:
                return address
        raise RuntimeError('Login worker has no address on its private network')

    async def _login_page(self, request: web.Request) -> web.Response:
        login_id = request.match_info['login_id']
        login = await self._get(login_id)
        if login is None or login.done:
            raise web.HTTPGone(text='Login link expired')
        token = request.query.get('token', '')
        async with login.callback_lock:
            if login.bootstrap_used or not token or not hmac.compare_digest(token, login.bootstrap_secret):
                raise web.HTTPForbidden(text='Invalid or used login link')
            login.bootstrap_used = True
        response = web.HTTPFound(f'/login/{login_id}/vnc.html?autoconnect=1&resize=scale&path=login/{login_id}/websockify')
        response.set_cookie('login_proxy', login.proxy_token, httponly=True,
                            secure=bool(self.settings.login_cookie_secure), samesite='Strict',
                            path=f'/login/{login_id}/', max_age=int(self.settings.login_timeout_seconds))
        raise response

    async def _proxy(self, request: web.Request) -> web.StreamResponse:
        login_id = request.match_info['login_id']
        login = await self._get(login_id)
        if login is None or login.done or not login.worker_host:
            raise web.HTTPGone(text='Login link expired')
        token = request.cookies.get('login_proxy')
        if token is None or not hmac.compare_digest(token, login.proxy_token):
            raise web.HTTPForbidden(text='Login browser cookie required')
        if request.headers.get('Upgrade', '').lower() == 'websocket':
            origin = request.headers.get('Origin', '')
            public = urlsplit(self.settings.public_base_url)
            expected = f'{public.scheme}://{public.netloc}'
            if origin != expected:
                raise web.HTTPForbidden(text='Invalid origin')
            if request.match_info.get('asset') != 'websockify':
                raise web.HTTPNotFound()
            ws = web.WebSocketResponse(heartbeat=30, max_msg_size=2**20)
            await ws.prepare(request)
            login.browser_ws.add(ws)
            try:
                basic_auth = 'Basic ' + __import__('base64').b64encode(f'gateway:{login.proxy_token}'.encode()).decode()
                async with websockets.connect(f'ws://{login.worker_host}:6080/websockify?token={quote(login.proxy_token)}', additional_headers={'X-Auth-Token': login.proxy_token, 'X-Login-Proxy': login.proxy_token, 'Authorization': basic_auth}, max_size=2**20, ping_interval=30) as upstream:
                    async def to_worker() -> None:
                        async for msg in ws:
                            if msg.type == web.WSMsgType.TEXT:
                                await upstream.send(msg.data)
                            elif msg.type == web.WSMsgType.BINARY:
                                await upstream.send(msg.data)
                            elif msg.type == web.WSMsgType.CLOSE:
                                break
                    async def to_client() -> None:
                        async for msg in upstream:
                            if isinstance(msg, bytes):
                                await ws.send_bytes(msg)
                            else:
                                await ws.send_str(msg)
                    tasks = [asyncio.create_task(to_worker()), asyncio.create_task(to_client())]
                    try:
                        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                    finally:
                        for task in tasks:
                            if not task.done():
                                task.cancel()
                        await asyncio.gather(*tasks, return_exceptions=True)
            except Exception:
                log.info('noVNC websocket disconnected for login %s', login_id)
            finally:
                login.browser_ws.discard(ws)
                await ws.close()
            return ws
        asset = request.match_info.get('asset', '') or 'vnc.html'
        if asset.startswith('/') or '..' in asset.split('/') or '\\\\' in asset:
            raise web.HTTPNotFound()
        if asset not in {'vnc.html', 'vnc_lite.html'} and not asset.startswith(('app/', 'core/', 'vendor/')):
            # Only proxy resources from the worker's noVNC web root.
            raise web.HTTPNotFound()
        proxy_cookie = request.cookies.get('login_proxy', '')
        if not hmac.compare_digest(proxy_cookie, login.proxy_token):
            raise web.HTTPForbidden(text='Login browser cookie required')
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                # Forward only noVNC-required settings; never relay arbitrary query values.
                params = {key: value for key, value in request.query.items() if key in {'autoconnect', 'resize', 'path', 'reconnect'}}
                response = await client.get(f'http://{login.worker_host}:6080/{asset}', params=params,
                                            headers={'Host': login.worker_host, 'X-Auth-Token': login.proxy_token,
                                                     'X-Login-Proxy': login.proxy_token,
                                                     'Authorization': 'Basic ' + __import__('base64').b64encode(f'gateway:{login.proxy_token}'.encode()).decode()})
        except Exception:
            raise web.HTTPBadGateway(text='Login display is starting')
        # httpx transparently decompresses upstream responses; forwarding the old
        # content-length would corrupt streamed/static bodies in browsers.
        headers = {k: v for k, v in response.headers.items() if k.lower() == 'content-type'}
        result = web.Response(body=response.content, status=response.status_code, headers=headers)
        return result

    async def _complete(self, request: web.Request) -> web.Response:
        login_id = request.match_info['login_id']
        login = await self._get(login_id)
        if login is None:
            raise web.HTTPNotFound()
        body = await request.read()
        if not self._valid_signature(request, login, body):
            raise web.HTTPUnauthorized()
        try:
            data = json.loads(body)
            cookies = data['cookies']
            raw_profile = data['profile']
            profile = SessionProfile.from_dict(raw_profile) if 'hak_akses' in raw_profile else SessionProfile.from_api(raw_profile)
            if not isinstance(cookies, list) or not cookies or len(cookies) > 200:
                raise ValueError()
            # Restrict accepted cookie properties and enforce the ETHOL origin.
            allowed_host = (urlsplit(self.settings.ethol_base_url).hostname or '').lower()
            clean: list[dict[str, Any]] = []
            for item in cookies:
                domain = str(item.get('domain') or '').lstrip('.').lower()
                allowed_domains = {allowed_host}
                if allowed_host == 'ethol.pens.ac.id':
                    allowed_domains.add('pens.ac.id')
                if domain not in allowed_domains:
                    raise ValueError()
                clean.append({k: item[k] for k in ('name', 'value', 'domain', 'path', 'expires', 'httpOnly', 'secure', 'sameSite') if k in item})
        except (ValueError, TypeError, KeyError, AttributeError, json.JSONDecodeError):
            raise web.HTTPBadRequest(text='Invalid login result')
        async with login.callback_lock:
            if login.done:
                raise web.HTTPConflict()
            callback = login.on_success
            if callback:
                try:
                    await callback(clean, profile)
                except Exception:
                    log.exception('Login completion callback rejected result')
                    login.done = True
                    failure = login.on_failure
                    if failure:
                        try:
                            await failure('Login result could not be accepted. Please start again.')
                        except Exception:
                            log.exception('Could not notify user about rejected login result')
                    raise web.HTTPConflict(text='Login result was rejected')
            login.done = True
        return web.json_response({'ok': True})

    async def _fail(self, request: web.Request) -> web.Response:
        login_id = request.match_info['login_id']
        login = await self._get(login_id)
        if login is None:
            raise web.HTTPNotFound()
        body = await request.read()
        if not self._valid_signature(request, login, body):
            raise web.HTTPUnauthorized()
        message = 'Login failed. Please try again.'
        try:
            candidate = json.loads(body).get('message')
            if isinstance(candidate, str):
                message = candidate[:200]
        except Exception:
            pass
        async with login.callback_lock:
            if not login.done:
                login.done = True
                callback = login.on_failure
                if callback:
                    await callback(message)
        return web.json_response({'ok': True})

    async def _get(self, login_id: str) -> _Login | None:
        async with self._lock:
            login = self._logins.get(login_id)
            if login and time.monotonic() - login.created > int(self.settings.login_timeout_seconds):
                return None
            return login

    @staticmethod
    def _valid_signature(request: web.Request, login: _Login, body: bytes) -> bool:
        supplied = request.headers.get('X-Login-Signature', '')
        expected = hmac.new(login.secret, request.match_info['login_id'].encode() + b'.' + body, hashlib.sha256).hexdigest()
        return hmac.compare_digest(supplied, expected)

    async def _cleanup_expired(self) -> None:
        async with self._lock:
            expired = [(key, value) for key, value in self._logins.items() if time.monotonic() - value.created > int(self.settings.login_timeout_seconds) + 30]
        for key, value in expired:
            await self._destroy(key, value)

    async def _sweep(self) -> None:
        while True:
            await asyncio.sleep(15)
            await self._cleanup_expired()
            async with self._lock:
                stale = [(key, value) for key, value in self._logins.items() if value.done]
            for key, value in stale:
                await self._destroy(key, value)

    async def _destroy(self, login_id: str, login: _Login) -> None:
        for websocket in tuple(login.browser_ws):
            try:
                await websocket.close(code=1001, message=b'Login expired')
            except Exception:
                pass
        login.browser_ws.clear()
        async with self._lock:
            if self._logins.get(login_id) is not login:
                return
            self._logins.pop(login_id, None)
        if login.task and login.task is not asyncio.current_task() and not login.task.done():
            login.task.cancel()
        if login.container:
            try:
                await asyncio.to_thread(login.container.remove, force=True, v=True)
            except Exception:
                pass
