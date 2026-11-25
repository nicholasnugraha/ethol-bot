from types import SimpleNamespace

import pytest
from cryptography.fernet import Fernet

from app.crypto import SecretBox
from app.db import Database
from app.ethol.auth import AuthManager, LoginError
from app.ethol.models import SessionProfile


@pytest.fixture
async def storage(tmp_path):
    db = Database(str(tmp_path / 'users.db'))
    await db.connect()
    yield db
    await db.close()


async def test_old_client_refresh_cannot_overwrite_new_login(storage):
    scoped = storage.for_user(101)
    box = SecretBox(Fernet.generate_key().decode())
    settings = SimpleNamespace(ethol_base_url='https://ethol.pens.ac.id')
    auth = AuthManager(settings, scoped, box)
    profile = SessionProfile.from_api({'nomor': 1, 'nama': 'Owner'})
    await auth._persist_session([{'name': 'token', 'value': 'old'}], profile)
    old_client = await auth.load_client()
    await auth._persist_session([{'name': 'token', 'value': 'new'}], profile)
    await old_client._on_cookies_changed([{'name': 'token', 'value': 'stale'}])
    assert box.decrypt_json((await scoped.load_session())[0])[0]['value'] == 'new'
    assert await storage.for_user(202).load_session() is None
    await old_client.close()


async def test_login_rejects_silent_identity_switch(storage):
    class Broker:
        async def create(self, user_id, success, failure):
            await success([], SessionProfile.from_api({'nomor': 2, 'nama': 'Other'}))
            return 'https://example.com/login/attempt/'

    scoped = storage.for_user(101)
    auth = AuthManager(SimpleNamespace(ethol_base_url='https://ethol.pens.ac.id'),
                       scoped, SecretBox(Fernet.generate_key().decode()), login_broker=Broker())
    await auth._persist_session([], SessionProfile.from_api({'nomor': 1, 'nama': 'Owner'}))
    with pytest.raises(LoginError, match='berbeda'):
        await auth.create_login_url()
    assert 'Owner' in (await scoped.load_session())[1]
