from __future__ import annotations

import pytest
from cryptography.fernet import Fernet

from app.crypto import SecretBox


def test_roundtrip() -> None:
    box = SecretBox(Fernet.generate_key().decode())
    data = {"token": "abc", "nested": [1, 2, 3], "nama": "Nicholas"}
    encrypted = box.encrypt_json(data)
    assert encrypted != str(data)
    assert box.decrypt_json(encrypted) == data


def test_invalid_key() -> None:
    with pytest.raises(ValueError):
        SecretBox("not-a-valid-fernet-key")


def test_wrong_key_cannot_decrypt() -> None:
    first = SecretBox(Fernet.generate_key().decode())
    second = SecretBox(Fernet.generate_key().decode())
    encrypted = first.encrypt_json({"a": 1})
    with pytest.raises(ValueError):
        second.decrypt_json(encrypted)
