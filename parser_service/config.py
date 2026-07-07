"""
Конфигурация сервиса парсинга.

Все секреты и настройки читаются из переменных окружения (.env).
Ничего не хардкодим в коде — только значения по умолчанию для локальной разработки.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

# Загружаем .env из корня проекта (если файл есть). В проде переменные
# приходят из окружения контейнера, и load_dotenv просто ничего не делает.
load_dotenv()


def _get_int(name: str, default: int | None = None) -> int | None:
    """Аккуратно читаем целочисленную переменную окружения."""
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    return int(raw)


@dataclass(frozen=True)
class ProxyConfig:
    """
    Настройки прокси для Telethon.

    На старте проекта прокси НЕ используется (enabled=False). Позже включается
    одной строкой в .env: PROXY_ENABLED=true + заполнить хост/порт/тип.
    Реальный код подключения прокси в client.py — заглушка (см. TODO там).
    """

    enabled: bool = False
    proxy_type: str | None = None  # socks5 / http и т.п.
    host: str | None = None
    port: int | None = None
    username: str | None = None
    password: str | None = None

    @classmethod
    def from_env(cls) -> "ProxyConfig":
        return cls(
            enabled=os.getenv("PROXY_ENABLED", "false").lower() == "true",
            proxy_type=os.getenv("PROXY_TYPE"),
            host=os.getenv("PROXY_HOST"),
            port=_get_int("PROXY_PORT"),
            username=os.getenv("PROXY_USERNAME"),
            password=os.getenv("PROXY_PASSWORD"),
        )


@dataclass(frozen=True)
class Settings:
    """Единый объект настроек всего сервиса."""

    # --- Telegram (Telethon) ---
    api_id: int | None = _get_int("API_ID")
    api_hash: str | None = os.getenv("API_HASH")
    # Имя файла сессии Telethon (без расширения .session).
    session_name: str = os.getenv("SESSION_NAME", "parser_session")

    # --- Инфраструктура ---
    # По умолчанию — локальный Postgres из docker-compose.
    database_url: str = os.getenv(
        "DATABASE_URL",
        "postgresql+psycopg2://parser:parser@localhost:5432/parser_db",
    )
    redis_url: str = os.getenv("REDIS_URL", "redis://localhost:6379/0")

    # --- Прокси (опционально) ---
    proxy: ProxyConfig = field(default_factory=ProxyConfig.from_env)

    # --- Лимиты Telegram ---
    # Не более 20-30 исходящих в сутки на новый аккаунт. Берём безопасные 25.
    # TODO: в будущем лимит станет настройкой кампании/аккаунта, а не глобальной.
    daily_outbox_limit: int = _get_int("DAILY_OUTBOX_LIMIT", 25) or 25

    # --- Логирование ---
    log_level: str = os.getenv("LOG_LEVEL", "INFO")


# =============================================================================
# ТРИГГЕРНЫЕ СЛОВА
# =============================================================================
# ВНИМАНИЕ: пока хардкодим список триггеров здесь для Спринта 1.
# TODO: в будущем триггерные слова переедут в Campaign.settings (JSON в БД),
#       чтобы каждая кампания (ниша) имела свой набор ключевых слов,
#       а админка могла редактировать их без деплоя.
TRIGGER_KEYWORDS: list[str] = [
    "бонус",
    "кадастровый номер",
    "кадастр",
    "промокод",
    "скидка",
]


# Единый инстанс настроек, импортируемый по всему проекту.
settings = Settings()
