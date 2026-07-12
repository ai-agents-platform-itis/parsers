"""
Уведомление менеджера о «горячем» лиде (Спринт 3).

Шлём через обычный Telegram Bot API (requests), а НЕ через Telethon:
боту не нужна сессия/авторизация юзербота, нет конфликтов event loop в
Celery-воркере, и лимиты юзербота (25/сутки) на служебные уведомления
не тратятся. Токен — из @BotFather, chat_id менеджера — из @userinfobot
(менеджер должен один раз нажать Start у бота).
"""

from __future__ import annotations

import logging

import requests
from tenacity import retry, stop_after_attempt, wait_exponential

from parser_service.config import settings

logger = logging.getLogger(__name__)

_TIMEOUT = 10


def is_configured() -> bool:
    """Задан ли бот-нотификатор в .env."""
    return bool(settings.manager_bot_token and settings.manager_chat_id)


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=2, max=20), reraise=True)
def send_to_manager(text: str) -> None:
    """Отправить сообщение менеджеру. Бросает исключение после 3 неудач."""
    if not is_configured():
        raise RuntimeError("Заполни MANAGER_BOT_TOKEN и MANAGER_CHAT_ID в .env")
    resp = requests.post(
        f"https://api.telegram.org/bot{settings.manager_bot_token}/sendMessage",
        json={"chat_id": settings.manager_chat_id, "text": text},
        timeout=_TIMEOUT,
    )
    resp.raise_for_status()
    logger.info("Менеджер уведомлён (chat_id=%s)", settings.manager_chat_id)
