"""
Слушатель Telegram: мониторинг чатов из БД по триггерным словам.

Логика:
  1. Поднимаем TelegramClient (сессия из .env).
  2. Читаем активные чаты (MonitoredChat.is_active=True) из БД.
  3. Подписываемся на новые сообщения в этих чатах (events.NewMessage).
  4. При совпадении с триггером — создаём Lead+Message в БД и ставим
     задачу в Celery (не блокируя event loop Telethon).
  5. FloodWaitError обрабатываем: спим e.seconds и продолжаем, а не падаем.

Запуск:  python -m parser_service.telegram.listener
"""

from __future__ import annotations

import asyncio
import logging

from sqlmodel import select
from telethon import TelegramClient, events
from telethon.errors import FloodWaitError

from parser_service.celery_app.tasks import process_incoming_message
from parser_service.config import settings
from parser_service.db.models import (
    Campaign,
    Lead,
    LeadStatus,
    Message,
    MessageDirection,
    MonitoredChat,
)
from parser_service.db.session import get_session, init_db
from parser_service.telegram.client import create_client
from parser_service.triggers import ruleset_for_campaign

logging.basicConfig(
    level=settings.log_level,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("listener")


def _load_active_chats() -> list[MonitoredChat]:
    """
    Загрузить активные мониторимые чаты из БД.

    Список чатов живёт в БД (не в хардкоде) — будущая админка сможет
    добавлять/выключать чаты без перезапуска парсера (нужен только restart
    listener, чтобы переподписаться; hot-reload — задел на будущее).
    """
    with get_session() as db:
        chats = db.exec(
            select(MonitoredChat).where(MonitoredChat.is_active == True)  # noqa: E712
        ).all()
        # Отвязываем объекты от сессии (нужны только значения полей).
        return list(chats)


def _load_campaign_settings(campaign_ids: set[int]) -> dict[int, dict]:
    """
    Подтянуть settings кампаний для per-campaign триггеров (Спринт 2).

    Триггеры теперь живут в Campaign.settings["triggers"] — у каждой ниши
    свой набор. Кампании без triggers работают по legacy-набору из config.
    """
    if not campaign_ids:
        return {}
    with get_session() as db:
        campaigns = db.exec(
            select(Campaign).where(Campaign.id.in_(campaign_ids))  # type: ignore[union-attr]
        ).all()
        return {c.id: dict(c.settings or {}) for c in campaigns}


def _resolve_chat_target(chat: MonitoredChat):
    """
    Привести chat_identifier к виду, который понимает Telethon.

    Числовой id (в т.ч. отрицательный для супергрупп) отдаём как int,
    остальное (@username / ссылку) — как строку.
    """
    ident = chat.chat_identifier.strip()
    if ident.lstrip("-").isdigit():
        return int(ident)
    return ident


async def _persist_lead_and_message(
    chat: MonitoredChat,
    sender_id: int | None,
    sender_username: str | None,
    text: str,
) -> int | None:
    """
    Создать Lead и входящий Message в БД. Вернуть message_id (или None).

    contact: предпочитаем @username — по нему воркер-отправитель сможет
    написать лиду (по голому user_id другая сессия отправить не может),
    и менеджеру в CRM он полезнее. Фолбэк — числовой id.

    Работа с синхронной сессией короткая, поэтому оборачиваем в to_thread,
    чтобы не блокировать event loop Telethon.
    """
    if sender_username:
        contact = f"@{sender_username}"
    else:
        contact = str(sender_id) if sender_id is not None else None

    def _write() -> int:
        with get_session() as db:
            lead = Lead(
                campaign_id=chat.campaign_id,
                source=f"telegram:{chat.chat_identifier}",
                contact=contact,
                status=LeadStatus.NEW,
            )
            db.add(lead)
            db.flush()  # получаем lead.id до коммита

            message = Message(
                lead_id=lead.id,
                text=text,
                direction=MessageDirection.INCOMING,
            )
            db.add(message)
            db.flush()
            return message.id

    return await asyncio.to_thread(_write)


def _register_handler(
    client: TelegramClient, chat_map: dict, campaign_settings: dict[int, dict]
) -> None:
    """
    Зарегистрировать один обработчик NewMessage на все мониторимые чаты.
    chat_map: {target -> MonitoredChat} для восстановления campaign_id.
    campaign_settings: {campaign_id -> Campaign.settings} для триггеров ниши.
    """
    targets = list(chat_map.keys())

    @client.on(events.NewMessage(chats=targets))
    async def _handler(event: events.NewMessage.Event) -> None:
        text = event.message.message or ""

        # Определяем, к какому мониторимому чату относится событие.
        chat = chat_map.get(event.chat_id) or chat_map.get(
            getattr(event, "chat_id", None)
        )
        if chat is None:
            # Фолбэк: берём первый (обычно targets из одного чата на событие).
            chat = next(iter(chat_map.values()))

        # Триггеры ниши этой кампании (Спринт 2), с фолбэком на legacy-набор.
        ruleset = ruleset_for_campaign(
            chat.campaign_id, campaign_settings.get(chat.campaign_id)
        )
        matches = ruleset.match(text)
        if not matches:
            return  # сообщение без триггеров — игнорируем

        keywords = ", ".join(m.keyword for m in matches)
        logger.info(
            "Триггер [%s] в чате %s от %s: %r",
            keywords,
            chat.chat_identifier,
            event.sender_id,
            text[:120],
        )

        # username отправителя (если есть) — для контакта лида.
        sender_username: str | None = None
        try:
            sender = await event.get_sender()
            sender_username = getattr(sender, "username", None)
        except Exception:  # noqa: BLE001 — не критично, обойдёмся id
            logger.debug("Не удалось получить sender для %s", event.sender_id)

        try:
            message_id = await _persist_lead_and_message(
                chat=chat,
                sender_id=event.sender_id,
                sender_username=sender_username,
                text=text,
            )
        except Exception:  # noqa: BLE001 — не роняем listener из-за одной ошибки БД
            logger.exception("Не удалось сохранить лид/сообщение в БД")
            return

        if message_id is not None:
            # Обработку/ответ отдаём в Celery — event loop не блокируем.
            process_incoming_message.delay(message_id)
            logger.info("Задача в Celery поставлена (message_id=%s)", message_id)

    logger.info("Обработчик зарегистрирован на %d чат(ов)", len(targets))


async def run() -> None:
    """Основной цикл слушателя с обработкой FloodWaitError."""
    init_db()  # на Спринте 1 создаём таблицы при старте

    chats = _load_active_chats()
    if not chats:
        logger.warning(
            "Нет активных чатов в MonitoredChats. Добавь чаты в БД "
            "(is_active=true) и перезапусти listener."
        )
    campaign_settings = _load_campaign_settings({c.campaign_id for c in chats})

    client = create_client()

    # На случай FloodWait при подключении/резолве — оборачиваем в цикл.
    while True:
        try:
            await client.start()  # авторизация по сессии из .env
            logger.info("Клиент Telegram подключён.")

            # Собираем карту target -> chat. Резолвим сущности заранее.
            chat_map: dict = {}
            for chat in chats:
                target = _resolve_chat_target(chat)
                try:
                    entity = await client.get_entity(target)
                    chat_map[entity.id] = chat
                    # Обновим человекочитаемое имя, если пусто.
                    logger.info("Мониторю: %s (id=%s)", chat.chat_identifier, entity.id)
                except FloodWaitError as e:
                    logger.warning("FloodWait %s сек при резолве %s — сплю.", e.seconds, target)
                    await asyncio.sleep(e.seconds)
                except Exception:  # noqa: BLE001
                    logger.exception("Не удалось зарезолвить чат %s — пропускаю", target)

            if not chat_map:
                logger.warning("Ни один чат не зарезолвился. Жду и пробую снова...")
                await asyncio.sleep(30)
                continue

            _register_handler(client, chat_map, campaign_settings)

            logger.info("Слушаю новые сообщения. Ctrl+C для выхода.")
            await client.run_until_disconnected()
            break  # штатное отключение — выходим из цикла

        except FloodWaitError as e:
            # Ключевое требование ТЗ: спим нужное время, а не падаем.
            logger.warning("FloodWaitError: сплю %s секунд.", e.seconds)
            await asyncio.sleep(e.seconds)
            # После сна цикл повторится и переподключимся.
        except (KeyboardInterrupt, asyncio.CancelledError):
            logger.info("Остановка по сигналу пользователя.")
            break
        except Exception:  # noqa: BLE001
            logger.exception("Непредвиденная ошибка listener — перезапуск через 15 сек.")
            await asyncio.sleep(15)
        finally:
            if client.is_connected():
                await client.disconnect()


def main() -> None:
    """CLI-точка входа."""
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        logger.info("Выход.")


if __name__ == "__main__":
    main()
