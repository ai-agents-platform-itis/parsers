"""
Базовый каркас поллинг-парсеров (Avito / Instagram).

Общий пайплайн для всех платформ (то же, что делает Telegram-listener,
но для источников с периодическим поллингом):

    fetch_items(source)                    # платформо-специфично (подкласс)
      -> дедуп по Redis (уже видели?)      # чтобы не плодить лидов повторно
      -> триггеры кампании (per-campaign)  # parser_service.triggers
      -> Lead + Message в БД
      -> задача process_incoming_message в Celery

Парсеры вызываются из Celery-задач (beat -> poll_avito / poll_instagram),
поэтому весь код синхронный (sync_playwright).
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import ClassVar

import redis

from parser_service.celery_app.tasks import process_incoming_message
from parser_service.config import settings
from parser_service.db.models import (
    Campaign,
    Lead,
    LeadStatus,
    Message,
    MessageDirection,
    MonitoredSource,
)
from parser_service.db.session import get_session
from parser_service.triggers import ruleset_for_campaign

logger = logging.getLogger(__name__)

# Отдельный клиент Redis для дедупа обработанных элементов.
_redis = redis.Redis.from_url(settings.redis_url, decode_responses=True)


@dataclass(frozen=True)
class ParsedItem:
    """
    Единица контента с площадки: объявление Avito или комментарий Instagram.

    external_id — стабильный id элемента на площадке (для дедупа).
    contact     — то, по чему потом можно связаться (username/ссылка на профиль).
    """

    external_id: str
    text: str
    url: str | None = None
    contact: str | None = None


class BaseParser(ABC):
    """Общий пайплайн; подклассы реализуют только fetch_items()."""

    # Имя платформы — для ключей Redis и Lead.source (напр. "avito").
    platform: ClassVar[str]

    @abstractmethod
    def fetch_items(self, source: MonitoredSource) -> list[ParsedItem]:
        """Собрать свежие элементы источника. Платформо-специфично."""

    # -------------------------------------------------------------------
    # Дедуп: Redis-ключ на каждый обработанный элемент с TTL.
    # Дата не нужна — TTL сам подчищает старое; повторная обработка после
    # истечения TTL безвредна (лид просто продублируется через N дней,
    # к тому времени объявление обычно уже неактуально).
    # -------------------------------------------------------------------
    def _is_new(self, source: MonitoredSource, item: ParsedItem) -> bool:
        key = f"seen:{self.platform}:{source.id}:{item.external_id}"
        ttl = settings.seen_items_ttl_days * 24 * 60 * 60
        # SET NX: True — ключа не было (элемент новый), None — уже видели.
        return bool(_redis.set(key, "1", nx=True, ex=ttl))

    def _persist_lead(
        self, source: MonitoredSource, item: ParsedItem
    ) -> int | None:
        """Создать Lead + входящий Message. Вернуть message_id."""
        with get_session() as db:
            lead = Lead(
                campaign_id=source.campaign_id,
                source=f"{self.platform}:{source.identifier}",
                contact=item.contact or item.url,
                status=LeadStatus.NEW,
            )
            db.add(lead)
            db.flush()

            message = Message(
                lead_id=lead.id,
                text=item.text,
                direction=MessageDirection.INCOMING,
            )
            db.add(message)
            db.flush()
            return message.id

    def process_source(self, source: MonitoredSource, campaign: Campaign) -> int:
        """
        Обработать один источник: собрать элементы, отфильтровать по триггерам
        кампании, завести лидов. Возвращает число новых лидов.
        """
        try:
            items = self.fetch_items(source)
        except Exception:  # noqa: BLE001 — одна битая выдача не роняет весь поллинг
            logger.exception(
                "[%s] Не удалось собрать источник id=%s (%s)",
                self.platform,
                source.id,
                source.identifier,
            )
            return 0

        ruleset = ruleset_for_campaign(campaign.id, campaign.settings)
        new_leads = 0

        for item in items:
            matches = ruleset.match(item.text)
            if not matches:
                continue
            if not self._is_new(source, item):
                continue  # уже обрабатывали — лид есть

            keywords = ", ".join(m.keyword for m in matches)
            logger.info(
                "[%s] Триггер [%s] в источнике %s: %r",
                self.platform,
                keywords,
                source.identifier,
                item.text[:120],
            )
            try:
                message_id = self._persist_lead(source, item)
            except Exception:  # noqa: BLE001
                logger.exception("[%s] Не удалось сохранить лид в БД", self.platform)
                continue

            if message_id is not None:
                process_incoming_message.delay(message_id)
                new_leads += 1

        logger.info(
            "[%s] Источник %s: элементов=%d, новых лидов=%d",
            self.platform,
            source.identifier,
            len(items),
            new_leads,
        )
        return new_leads
