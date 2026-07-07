"""
Celery-задачи парсер-сервиса.

Две задачи:
  * process_incoming_message — обработка входящего (тут позже будет вызов AI Core).
  * send_outgoing_message    — отправка исходящего с суточным лимитом (лимиты TG).

Redis используется и как брокер Celery, и как счётчик исходящих сообщений.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

import redis

from parser_service.celery_app.celery_config import celery_app
from parser_service.config import settings
from parser_service.db.models import Lead, LeadStatus, Message, MessageDirection
from parser_service.db.session import get_session

logger = logging.getLogger(__name__)

# Отдельный синхронный клиент Redis для счётчика (не через Celery-брокер).
_redis = redis.Redis.from_url(settings.redis_url, decode_responses=True)

# Сколько секунд в сутках — TTL для суточного счётчика-ключа.
_ONE_DAY_SECONDS = 24 * 60 * 60


def _outbox_key(account: str) -> str:
    """
    Ключ Redis для суточного счётчика исходящих.

    Формат: outbox_count:{account}:{YYYY-MM-DD} (UTC).
    Дата в ключе даёт естественный «сброс» счётчика в новых сутках,
    а TTL подчищает старые ключи.
    """
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return f"outbox_count:{account}:{today}"


# =============================================================================
# process_incoming_message
# =============================================================================
@celery_app.task(name="parser.process_incoming_message")
def process_incoming_message(message_id: int) -> None:
    """
    Обработать входящее сообщение (найденный триггером лид-контакт).

    ТОЧКА ИНТЕГРАЦИИ С AI CORE:
    Здесь позже будет вызов AI Core API — POST /api/chat, который вернёт
    текст ответа с учётом RAG-базы кампании, после чего мы поставим
    send_outgoing_message. Пока — только лог-заглушка.
    """
    logger.info("process_incoming_message: message_id=%s", message_id)

    # Подтягиваем сообщение (демонстрация сквозного пайплайна).
    with get_session() as db:
        message = db.get(Message, message_id)
        if message is None:
            logger.warning("Сообщение id=%s не найдено в БД", message_id)
            return

    # TODO(AI Core): вызвать AI Core API.
    #   POST {AI_CORE_URL}/api/chat
    #   payload = {"lead_id": message.lead_id, "text": message.text, ...}
    #   reply = response.json()["reply"]
    #   send_outgoing_message.delay(lead_id=message.lead_id, text=reply)
    logger.info("would call AI core here (message_id=%s)", message_id)


# =============================================================================
# send_outgoing_message
# =============================================================================
@celery_app.task(
    bind=True,
    name="parser.send_outgoing_message",
    # tenacity нам тут не нужен: у Celery свой механизм retry.
    max_retries=None,  # ограничиваем не числом попыток, а логикой лимита
)
def send_outgoing_message(self, lead_id: int, text: str, account: str = "default") -> None:
    """
    Отправить исходящее сообщение лиду с учётом суточного лимита.

    Лимит: не более settings.daily_outbox_limit (≈25) исходящих на аккаунт
    за сутки (жёсткие ограничения Telegram для новых аккаунтов).

    Если лимит достигнут — НЕ отправляем, логируем и откладываем задачу
    (retry через несколько часов), чтобы отправить в следующие сутки.
    """
    key = _outbox_key(account)
    current = int(_redis.get(key) or 0)

    if current >= settings.daily_outbox_limit:
        # Лимит выбран — переносим отправку. countdown в секундах.
        retry_in = 3 * _ONE_DAY_SECONDS // 24  # 3 часа
        logger.warning(
            "Суточный лимит исходящих достигнут (%s/%s) для account=%s. "
            "Откладываю отправку lead_id=%s на %s сек.",
            current,
            settings.daily_outbox_limit,
            account,
            lead_id,
            retry_in,
        )
        # Возвращаем задачу в отложенную очередь.
        raise self.retry(countdown=retry_in)

    # --- Лимит не превышен: «отправляем» ---
    # TODO(Telethon): здесь будет реальная отправка через TelegramClient.
    #   Отправку удобнее делать в отдельном процессе с event loop Telethon
    #   (например, через общую очередь), т.к. Celery-воркер синхронный.
    #   Пока — заглушка + запись исходящего в БД и инкремент счётчика.
    logger.info("Отправка (заглушка) lead_id=%s: %r", lead_id, text)

    with get_session() as db:
        lead = db.get(Lead, lead_id)
        if lead is None:
            logger.warning("Lead id=%s не найден, отправку пропускаю", lead_id)
            return

        db.add(
            Message(
                lead_id=lead_id,
                text=text,
                direction=MessageDirection.OUTGOING,
            )
        )
        # Обновим статус лида: с ним уже связались.
        if lead.status == LeadStatus.NEW:
            lead.status = LeadStatus.CONTACTED
            db.add(lead)

    # Инкремент суточного счётчика + выставление TTL на первый инкремент.
    new_count = _redis.incr(key)
    if new_count == 1:
        _redis.expire(key, _ONE_DAY_SECONDS)

    logger.info(
        "Исходящее учтено: %s/%s (account=%s)",
        new_count,
        settings.daily_outbox_limit,
        account,
    )
