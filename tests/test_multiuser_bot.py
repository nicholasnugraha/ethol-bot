from __future__ import annotations

import pytest

from app.config import Settings
from app.crypto import SecretBox
from app.db import Database
from app.main import UserRuntime


class FakeBot:
    async def send_message(self, chat_id, text, reply_markup=None):
        return None


class FakeBroker:
    pass


@pytest.mark.asyncio
async def test_runtime_creates_scoped_contexts(tmp_path):
    from cryptography.fernet import Fernet

    settings = Settings(
        _env_file=None,
        telegram_bot_token="1234567890:fake-token",
        allowed_chat_ids="101,202",
        encryption_key=Fernet.generate_key().decode(),
    )
    db = Database(str(tmp_path / "users.db"))
    await db.connect()
    runtime = UserRuntime(settings, db, FakeBot(), SecretBox(settings.encryption_key), FakeBroker(), {101, 202})
    try:
        one = await runtime.get(101)
        two = await runtime.get(202)
        assert one is runtime.contexts[101]
        assert two is runtime.contexts[202]
        assert one.db.user_id == "101"
        assert two.db.user_id == "202"
        assert one.auth is not two.auth
        assert one.service is not two.service
        assert one.notifier.recipient_id == 101
        assert two.notifier.recipient_id == 202
        assert {u["user_id"] for u in await db.list_users()} == {"101", "202"}
        with pytest.raises(PermissionError):
            await runtime.get(303)
    finally:
        await runtime.stop()
        await db.close()
