"""
Реальная отправка сообщений в Telegram из Celery-воркера (Спринт 3).

Почему ОТДЕЛЬНАЯ сессия (SENDER_SESSION_NAME):
SQLite-сессию Telethon нельзя делить между процессами — listener уже держит
основную, и второй коннект ловит «database is locked». Поэтому у воркера
своя сессия того же аккаунта.

Авторизация (один раз, интерактивно — спросит телефон и код):
    uv run python -m parser_service.telegram.sender

Celery-воркер синхронный, поэтому каждый вызов - короткий asyncio.run:
подключиться, отправить, отключиться. Для лимита 25 исходящих/сутки
overhead подключения несущественен.

ОГРАНИЧЕНИЕ TELETHON: отправка по числовому user_id проходит только если
access_hash юзера есть в кеше ЭТОЙ сессии. Сообщение видел listener (другая
сессия), поэтому по id отправить обычно нельзя — listener сохраняет
@username лида, когда он есть. Лиды без username помечаются как
неотправляемые (менеджер связывается вручную, контакт есть в CRM).
"""

from __future__ import annotations

import asyncio
import logging

from telethon import TelegramClient
from telethon.errors import FloodWaitError  # noqa: F401 — реэкспорт для tasks.py

from parser_service.config import settings

logger = logging.getLogger(__name__)


class SenderNotAuthorized(RuntimeError):
    """Сессия отправителя не авторизована (нужен разовый интерактивный логин)."""


class PeerNotResolvable(RuntimeError):
    """Не удалось определить получателя (числовой id без username)."""


def _resolve_peer(contact: str) -> str | int:
    """
    Привести Lead.contact к виду для send_message.

    "@username" — отдаём как есть; числовой id — тоже отдаём (вдруг юзер
    уже в кеше сессии), но с пониманием, что скорее всего не зарезолвится.
    """
    contact = contact.strip()
    if contact.startswith("@"):
        return contact
    if contact.lstrip("-").isdigit():
        return int(contact)
    return contact


async def _send(contact: str, text: str) -> None:
    client = TelegramClient(
        session=settings.sender_session_name,
        api_id=settings.api_id,
        api_hash=settings.api_hash,
    )
    await client.connect()
    try:
        if not await client.is_user_authorized():
            raise SenderNotAuthorized(
                f"Сессия '{settings.sender_session_name}' не авторизована. "
                "Выполни один раз: uv run python -m parser_service.telegram.sender"
            )
        try:
            await client.send_message(_resolve_peer(contact), text)
        except ValueError as exc:
            # Telethon: "Could not find the input entity" — id без access_hash.
            raise PeerNotResolvable(
                f"Не могу определить получателя {contact!r}: у лида нет "
                "@username, а его id не в кеше сессии отправителя."
            ) from exc
    finally:
        await client.disconnect()


def send_message_sync(contact: str, text: str) -> None:
    """
    Синхронная обёртка для Celery-задачи.

    Бросает: SenderNotAuthorized / PeerNotResolvable (не ретраить),
    FloodWaitError (ретраить через e.seconds).
    """
    asyncio.run(_send(contact, text))
    logger.info("Telegram: сообщение отправлено %s", contact)


def main() -> None:
    """Разовая интерактивная авторизация сессии отправителя."""
    if settings.api_id is None or not settings.api_hash:
        raise RuntimeError("Не заданы API_ID / API_HASH в .env")

    async def _auth() -> None:
        client = TelegramClient(
            session=settings.sender_session_name,
            api_id=settings.api_id,
            api_hash=settings.api_hash,
        )
        await client.start()  # спросит телефон и код при первом запуске
        me = await client.get_me()
        print(
            f"Сессия '{settings.sender_session_name}' авторизована как "
            f"@{me.username or me.id}. Воркер готов отправлять сообщения."
        )
        await client.disconnect()

    asyncio.run(_auth())


if __name__ == "__main__":
    main()
