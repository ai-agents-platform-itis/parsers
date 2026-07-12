"""
Инициализация Telethon-клиента.

Спринт 4: прокси-заглушка заменена реальной ротацией. Клиент берёт следующий
рабочий прокси из пула (parser_service.proxy_pool) — через python-socks.
Включается PROXY_ENABLED=true; при выключенных прокси работает напрямую.
"""

from __future__ import annotations

import logging

from telethon import TelegramClient

from parser_service.config import settings
from parser_service.proxy_pool import Proxy, next_proxy

logger = logging.getLogger(__name__)


def create_client(session_name: str | None = None) -> tuple[TelegramClient, Proxy | None]:
    """
    Создать (но не подключать) экземпляр TelegramClient с прокси из пула.

    Возвращает (client, proxy): proxy нужен вызывающему, чтобы при ошибке
    подключения пометить его битым (pool.mark_bad) и переподключиться на
    другом. proxy=None — работаем без прокси (пул пуст/выключен).

    session_name позволяет переиспользовать функцию для сессии отправителя.
    """
    if settings.api_id is None or not settings.api_hash:
        raise RuntimeError(
            "Не заданы API_ID / API_HASH. Заполни .env "
            "(получить на https://my.telegram.org)."
        )

    proxy = next_proxy()
    client = TelegramClient(
        session=session_name or settings.session_name,
        api_id=settings.api_id,
        api_hash=settings.api_hash,
        proxy=proxy.telethon_proxy() if proxy else None,
    )
    logger.info(
        "TelegramClient создан (сессия=%s, proxy=%s)",
        session_name or settings.session_name,
        proxy.identity if proxy else "off",
    )
    return client, proxy
