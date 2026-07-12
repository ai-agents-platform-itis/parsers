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

    # --- Веб-парсеры: Avito / Instagram (Спринт 2) ---
    # Интервалы поллинга (сек). Слишком частый поллинг = бан по IP, поэтому
    # значения по умолчанию консервативные.
    avito_poll_interval: int = _get_int("AVITO_POLL_INTERVAL", 300) or 300
    instagram_poll_interval: int = _get_int("INSTAGRAM_POLL_INTERVAL", 600) or 600
    # Headless-режим Playwright. Для локальной отладки удобно false (видно браузер).
    playwright_headless: bool = (
        os.getenv("PLAYWRIGHT_HEADLESS", "true").lower() == "true"
    )
    # Таймаут загрузки страницы, мс.
    page_timeout_ms: int = _get_int("PARSER_PAGE_TIMEOUT_MS", 30_000) or 30_000
    # Кука sessionid Instagram (без неё IG почти всё прячет за логином).
    # Берётся из браузера залогиненного аккаунта: DevTools -> Application ->
    # Cookies -> instagram.com -> sessionid.
    instagram_sessionid: str | None = os.getenv("INSTAGRAM_SESSIONID") or None
    # Сколько дней помним обработанные объявления/комментарии (дедуп в Redis).
    seen_items_ttl_days: int = _get_int("SEEN_ITEMS_TTL_DAYS", 7) or 7

    # --- AmoCRM (Спринт 3) ---
    # Базовый URL аккаунта, напр. https://yourcompany.amocrm.ru
    amocrm_base_url: str | None = os.getenv("AMOCRM_BASE_URL") or None
    # Долгосрочный токен (amoCRM: Настройки -> Интеграции -> создать интеграцию
    # -> «Ключи и доступы» -> долгосрочный токен). Проще OAuth-флоу и хватает
    # для бесплатного тарифа.
    amocrm_access_token: str | None = os.getenv("AMOCRM_ACCESS_TOKEN") or None
    # Опционально: воронка/статус/ответственный для новых сделок.
    amocrm_pipeline_id: int | None = _get_int("AMOCRM_PIPELINE_ID")
    amocrm_status_id: int | None = _get_int("AMOCRM_STATUS_ID")
    amocrm_responsible_user_id: int | None = _get_int("AMOCRM_RESPONSIBLE_USER_ID")

    # --- «Горячие» лиды (Спринт 3) ---
    # Порог скоринга (0..100): score >= порога -> лид уходит в CRM + менеджеру.
    # Скоринг пока интерим-эвристика (scoring.py), позже — Агент-Квалификатор.
    hot_lead_threshold: int = _get_int("HOT_LEAD_THRESHOLD", 60) or 60
    # Телеграм-бот для уведомлений менеджера (обычный Bot API, не Telethon):
    # токен из @BotFather + chat_id менеджера (узнать: @userinfobot).
    manager_bot_token: str | None = os.getenv("MANAGER_BOT_TOKEN") or None
    manager_chat_id: str | None = os.getenv("MANAGER_CHAT_ID") or None

    # --- Реальная отправка в Telegram (Спринт 3) ---
    # false — заглушка Спринта 1 (лог + запись в БД без отправки).
    telegram_send_enabled: bool = (
        os.getenv("TELEGRAM_SEND_ENABLED", "false").lower() == "true"
    )
    # ОТДЕЛЬНАЯ сессия Telethon для отправки из Celery-воркера: одну session-базу
    # (SQLite) нельзя делить между listener и воркером — «database is locked».
    # Авторизация один раз: uv run python -m parser_service.telegram.sender
    sender_session_name: str = os.getenv(
        "SENDER_SESSION_NAME",
        f"{os.getenv('SESSION_NAME', 'parser_session')}_sender",
    )

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
