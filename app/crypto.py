from __future__ import annotations

import json
from typing import Any

from cryptography.fernet import Fernet, InvalidToken


class SecretBox:
    """Encrypt/decrypt sensitive JSON (cookies) at rest using Fernet."""

    def __init__(self, key: str) -> None:
        try:
            self._fernet = Fernet(key.encode("utf-8"))
        except Exception as exc:  # pragma: no cover - defensive
            raise ValueError(
                "ENCRYPTION_KEY tidak valid. Generate dengan: "
                'python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"'
            ) from exc

    def encrypt_json(self, data: Any) -> str:
        raw = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        return self._fernet.encrypt(raw).decode("utf-8")

    def decrypt_json(self, token: str) -> Any:
        try:
            raw = self._fernet.decrypt(token.encode("utf-8"))
        except InvalidToken as exc:
            raise ValueError(
                "Gagal mendekripsi data tersimpan. ENCRYPTION_KEY berubah atau data rusak."
            ) from exc
        return json.loads(raw.decode("utf-8"))
