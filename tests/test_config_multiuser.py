import pytest
from cryptography.fernet import Fernet
from pydantic import ValidationError

from app.config import Settings


def settings(**kwargs):
    return Settings(_env_file=None, telegram_bot_token='1234567890:fake',
                    allowed_chat_ids='101,202', encryption_key=Fernet.generate_key().decode(), **kwargs)


@pytest.mark.parametrize('url', ['http://example.com', 'https://user:pass@example.com',
                                 'https://example.com/path', 'https://example.com?token=x',
                                 'https://example.com/#fragment'])
def test_public_origin_rejects_unsafe_values(url):
    with pytest.raises(ValidationError):
        settings(public_base_url=url)


def test_https_origin_and_explicit_owner():
    assert settings(public_base_url='https://example.com/').public_base_url == 'https://example.com'
    assert settings(legacy_owner_id=101).legacy_owner_id == 101
    with pytest.raises(ValidationError):
        settings(legacy_owner_id=-101)
