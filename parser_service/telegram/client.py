"""
Инициализация Telethon-клиента.

Клиент принимает опциональный proxy-параметр (через .env), но РЕАЛЬНЫЙ
код подключения прокси на Спринте 1 НЕ подключаем — только заглушку,
чтобы позже включить прокси одной строкой конфига.
"""

from __future__ import annotations

import logging

from telethon import TelegramClient

from parser_service.config import ProxyConfig, settings

logger = logging.getLogger(__name__)


def _build_proxy(proxy: ProxyConfig):
    """
    Собрать объект прокси для Telethon.

    ЗАГЛУШКА (Спринт 1): реальный прокси не используем. Возвращаем None,
    пока proxy.enabled=False.

    TODO: когда понадобится прокси — заполнить .env (PROXY_ENABLED=true и
    остальные PROXY_*) и раскомментировать сборку кортежа ниже. Telethon
    ожидает кортеж вида:
        (socks.SOCKS5, host, port, True, username, password)
    (нужен пакет python-socks / PySocks). Одной строкой конфига — как и просили.
    """
    if not proxy.enabled:
        return None

    # --- ниже — будущий реальный код (пока не активируем) ---
    # import socks
    # proto = {"socks5": socks.SOCKS5, "http": socks.HTTP}[proxy.proxy_type]
    # return (proto, proxy.host, proxy.port, True, proxy.username, proxy.password)

    logger.warning(
        "PROXY_ENABLED=true, но реальный прокси-код ещё не активирован (Спринт 1). "
        "Работаем без прокси."
    )
    return None


def create_client() -> TelegramClient:
    """
    Создать (но не подключать) экземпляр TelegramClient.

    Сессия, api_id и api_hash берутся из настроек (.env). Подключение и
    авторизация выполняются вызывающей стороной (listener.py) через
    `async with client` или `client.start()`.
    """
    if settings.api_id is None or not settings.api_hash:
        raise RuntimeError(
            "Не заданы API_ID / API_HASH. Заполни .env "
            "(получить на https://my.telegram.org)."
        )

    proxy = _build_proxy(settings.proxy)

    client = TelegramClient(
        session=settings.session_name,
        api_id=settings.api_id,
        api_hash=settings.api_hash,
        proxy=proxy,  # None на Спринте 1 — прокси-заглушка
    )
    logger.info(
        "TelegramClient создан (сессия=%s, proxy=%s)",
        settings.session_name,
        "on" if proxy else "off",
    )
    return client
