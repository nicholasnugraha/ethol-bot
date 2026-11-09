from __future__ import annotations

from dataclasses import dataclass

from app.bot.notifications import Notifier
from app.config import Settings
from app.db import Database
from app.ethol.auth import AuthManager
from app.ethol.service import EtholService


@dataclass(slots=True)
class AppContext:
    settings: Settings
    db: Database
    auth: AuthManager
    service: EtholService
    notifier: Notifier
