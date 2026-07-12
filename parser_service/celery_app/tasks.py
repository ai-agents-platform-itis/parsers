"""
Celery-задачи парсер-сервиса.

Задачи:
  * process_incoming_message — обработка входящего: интерим-скоринг + передача
    «горячих» лидов (вызов AI Core — точка интеграции, TODO).
  * push_hot_lead — сделка в AmoCRM + уведомление менеджера (Спринт 3).
  * send_outgoing_message    — отправка исходящего с суточным лимитом (лимиты TG).
  * poll_avito / poll_instagram — периодический поллинг веб-источников
    (запускаются Celery beat, см. beat_schedule в celery_config).

Redis используется и как брокер Celery, и как счётчик исходящих сообщений.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

import redis
from sqlmodel import select

from parser_service.celery_app.celery_config import celery_app
from parser_service.config import settings
from parser_service.db.models import (
    Campaign,
    Lead,
    LeadStatus,
    Message,
    MessageDirection,
    MonitoredSource,
    SourcePlatform,
)
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

    Спринт 3: считаем интерим-скоринг (scoring.py) и, если лид «горячий»
    (score >= HOT_LEAD_THRESHOLD), передаём его в CRM + менеджеру.

    ТОЧКА ИНТЕГРАЦИИ С AI CORE:
    Здесь позже будет вызов AI Core API — POST /api/chat, который вернёт
    текст ответа с учётом RAG-базы кампании И score от Агента-Квалификатора
    (заменит интерим-эвристику), после чего мы поставим send_outgoing_message.
    """
    from parser_service.scoring import estimate_score
    from parser_service.triggers import ruleset_for_campaign

    logger.info("process_incoming_message: message_id=%s", message_id)

    hot_lead_id: int | None = None
    with get_session() as db:
        message = db.get(Message, message_id)
        if message is None:
            logger.warning("Сообщение id=%s не найдено в БД", message_id)
            return
        lead = db.get(Lead, message.lead_id)
        campaign = db.get(Campaign, lead.campaign_id) if lead else None
        if lead is None or campaign is None:
            logger.warning("Лид/кампания для сообщения id=%s не найдены", message_id)
            return

        # Интерим-скоринг по триггерам кампании (до Агента-Квалификатора).
        ruleset = ruleset_for_campaign(campaign.id, campaign.settings)
        score = estimate_score(message.text, ruleset.match(message.text))
        if score > lead.score:
            lead.score = score
            db.add(lead)
        logger.info("Лид id=%s: score=%s (порог=%s)", lead.id, lead.score, settings.hot_lead_threshold)

        # «Горячий» и ещё не передан в CRM -> квалифицируем и передаём.
        if lead.score >= settings.hot_lead_threshold and lead.crm_lead_id is None:
            if lead.status in (LeadStatus.NEW, LeadStatus.CONTACTED):
                lead.status = LeadStatus.QUALIFIED
                db.add(lead)
            hot_lead_id = lead.id

    if hot_lead_id is not None:
        push_hot_lead.delay(hot_lead_id)
        logger.info("Лид id=%s горячий — передаю в CRM/менеджеру", hot_lead_id)

    # TODO(AI Core): вызвать AI Core API.
    #   POST {AI_CORE_URL}/api/chat
    #   payload = {"lead_id": message.lead_id, "text": message.text, ...}
    #   reply = response.json()["reply"]
    #   send_outgoing_message.delay(lead_id=message.lead_id, text=reply)
    logger.info("would call AI core here (message_id=%s)", message_id)


# =============================================================================
# push_hot_lead — «горячий» лид в AmoCRM + уведомление менеджера (Спринт 3)
# =============================================================================
@celery_app.task(
    bind=True,
    name="parser.push_hot_lead",
    max_retries=5,
    default_retry_delay=60,
)
def push_hot_lead(self, lead_id: int) -> None:
    """
    Передать «горячий» лид: сделка+контакт в AmoCRM (с историей диалога
    примечанием) и сообщение менеджеру через бот-нотификатор.

    Если AmoCRM не настроен в .env — CRM-шаг пропускается (менеджер всё
    равно уведомляется). Сетевые сбои amo -> retry задачи.
    """
    import requests as _requests

    from parser_service.crm import amocrm, notify

    with get_session() as db:
        lead = db.get(Lead, lead_id)
        if lead is None:
            logger.warning("push_hot_lead: лид id=%s не найден", lead_id)
            return
        campaign = db.get(Campaign, lead.campaign_id)
        messages = db.exec(
            select(Message)
            .where(Message.lead_id == lead_id)
            .order_by(Message.timestamp)  # type: ignore[arg-type]
        ).all()

        niche = campaign.niche_type if campaign else "unknown"
        platform = (lead.source or "unknown").split(":", 1)[0]
        contact = lead.contact or "не определён"
        already_in_crm = lead.crm_lead_id is not None
        score = lead.score
        source = lead.source or "-"
        dialog = "\n".join(
            f"[{m.direction.value}] {m.text}" for m in messages
        )

    crm_lead_id: int | None = None
    if already_in_crm:
        logger.info("push_hot_lead: лид id=%s уже в CRM — пропускаю", lead_id)
    elif not amocrm.is_configured():
        logger.warning(
            "AmoCRM не настроен (.env AMOCRM_*) — лид id=%s в CRM не передан.",
            lead_id,
        )
    else:
        try:
            crm_lead_id = amocrm.create_lead(
                name=f"[{niche}] лид #{lead_id} ({platform})",
                contact_name=contact,
                tags=[niche, platform, "auto-parser"],
            )
            amocrm.add_note(
                crm_lead_id,
                f"Источник: {source}\nКонтакт: {contact}\nScore: {score}\n\n"
                f"Диалог:\n{dialog}",
            )
        except _requests.RequestException as exc:
            logger.warning("AmoCRM недоступен (%s) — retry задачи", exc)
            raise self.retry(exc=exc)

        with get_session() as db:
            lead = db.get(Lead, lead_id)
            if lead is not None:
                lead.crm_lead_id = crm_lead_id
                db.add(lead)

    # Уведомление менеджера — best-effort: его сбой не должен ронять/
    # ретраить CRM-шаг (сделка уже создана).
    if notify.is_configured():
        text = (
            f"🔥 Горячий лид #{lead_id} (score {score})\n"
            f"Ниша: {niche}\nИсточник: {source}\nКонтакт: {contact}"
        )
        if crm_lead_id and settings.amocrm_base_url:
            text += f"\nCRM: {settings.amocrm_base_url.rstrip('/')}/leads/detail/{crm_lead_id}"
        try:
            notify.send_to_manager(text)
        except Exception:  # noqa: BLE001
            logger.exception("Не удалось уведомить менеджера о лиде id=%s", lead_id)
    else:
        logger.info(
            "Бот-нотификатор не настроен (MANAGER_BOT_TOKEN/MANAGER_CHAT_ID) — "
            "уведомление менеджеру пропущено."
        )


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

    with get_session() as db:
        lead = db.get(Lead, lead_id)
        if lead is None:
            logger.warning("Lead id=%s не найден, отправку пропускаю", lead_id)
            return
        contact = lead.contact
        source = lead.source or ""

    # --- Лимит не превышен: отправляем ---
    if settings.telegram_send_enabled:
        # Реальная отправка (Спринт 3) — пока только Telegram-лиды.
        # Instagram/Avito: отправка через API площадок — задел на будущее.
        if not source.startswith("telegram:"):
            logger.info(
                "Лид id=%s из %s — исходящие поддержаны только для Telegram, "
                "пропускаю отправку.",
                lead_id,
                source,
            )
            return
        if not contact:
            logger.warning("У лида id=%s нет контакта — отправить некому.", lead_id)
            return

        from parser_service.telegram.sender import (
            FloodWaitError,
            PeerNotResolvable,
            SenderNotAuthorized,
            send_message_sync,
        )

        try:
            send_message_sync(contact, text)
        except FloodWaitError as e:
            logger.warning("FloodWait при отправке lead_id=%s — retry через %s сек", lead_id, e.seconds)
            raise self.retry(countdown=e.seconds + 5)
        except (SenderNotAuthorized, PeerNotResolvable) as e:
            # Ретрай не поможет: нужен логин сессии или у лида нет username.
            # Лид не потерян — он в БД/CRM, менеджер свяжется вручную.
            logger.error("Отправка lead_id=%s невозможна: %s", lead_id, e)
            return
    else:
        logger.info(
            "Отправка (заглушка, TELEGRAM_SEND_ENABLED=false) lead_id=%s: %r",
            lead_id,
            text,
        )

    with get_session() as db:
        lead = db.get(Lead, lead_id)
        if lead is None:
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


# =============================================================================
# Поллинг веб-источников: Avito / Instagram (Спринт 2)
# =============================================================================
def _load_sources(platform: SourcePlatform) -> list[tuple[MonitoredSource, Campaign]]:
    """Активные источники платформы вместе с их кампаниями (для триггеров)."""
    with get_session() as db:
        rows = db.exec(
            select(MonitoredSource, Campaign)
            .join(Campaign, Campaign.id == MonitoredSource.campaign_id)  # type: ignore[arg-type]
            .where(
                MonitoredSource.platform == platform,
                MonitoredSource.is_active == True,  # noqa: E712
            )
        ).all()
        return [(source, campaign) for source, campaign in rows]


@celery_app.task(name="parser.poll_avito", ignore_result=True)
def poll_avito() -> None:
    """Обойти активные источники Avito (запускается по расписанию beat)."""
    # Импорт внутри задачи: parsers.base импортирует tasks (циклический импорт),
    # плюс Playwright не нужен воркеру, который эти задачи не выполняет.
    from parser_service.parsers.avito import poll_sources

    sources = _load_sources(SourcePlatform.AVITO)
    if not sources:
        logger.info("poll_avito: активных источников нет")
        return
    total = poll_sources(sources)
    logger.info("poll_avito: источников=%d, новых лидов=%d", len(sources), total)


@celery_app.task(name="parser.poll_instagram", ignore_result=True)
def poll_instagram() -> None:
    """Обойти активные источники Instagram (запускается по расписанию beat)."""
    from parser_service.parsers.instagram import poll_sources

    sources = _load_sources(SourcePlatform.INSTAGRAM)
    if not sources:
        logger.info("poll_instagram: активных источников нет")
        return
    total = poll_sources(sources)
    logger.info("poll_instagram: источников=%d, новых лидов=%d", len(sources), total)
