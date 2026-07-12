"""
SQLModel-модели под схему БД проекта.

Схему Postgres проектирует другой участник команды, но нам (парсеру) нужны
рабочие таблицы уже сейчас. Модели описаны строго под согласованную схему:
    Campaigns, Leads, Messages  — общие таблицы проекта.
    MonitoredChats              — наша дополнительная таблица (список чатов в БД).

ВЫБОР ТИПА ID:
    Используем int (autoincrement PRIMARY KEY), а не UUID.
    Обоснование для Спринта 1:
      * Проще читать/дебажить в логах и psql (Lead #42 нагляднее UUID).
      * Дешевле индексы и JOIN-ы (int 4-8 байт против 16 байт UUID).
      * Внешние идентификаторы (Telegram chat_id/user_id) всё равно храним
        отдельными полями, так что глобальная уникальность PK не требуется.
    Если позже понадобится распределённая генерация ключей (шардинг, слияние
    БД нескольких инстансов) — тип PK меняется на UUID точечно, модели к этому
    готовы (FK ссылаются на .id, а не на конкретный тип).
ПРИМЕЧАНИЕ: в этом модуле НЕЛЬЗЯ включать `from __future__ import annotations` —
он превращает все аннотации в строки, и SQLModel передаёт в relationship()
сырую строку "list['MonitoredChat']", которую SQLAlchemy не резолвит
(InvalidRequestError при первом обращении к модели). Форвард-ссылки в
кавычках (list["MonitoredChat"]) работают и без future-импорта.
"""

from datetime import datetime
from enum import Enum
from typing import Optional

from sqlalchemy import Column
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, Relationship, SQLModel


# =============================================================================
# Перечисления (enum-ы для читаемых статусов и направлений)
# =============================================================================
class LeadStatus(str, Enum):
    """Статус лида в воронке."""

    NEW = "new"                # только что найден по триггеру
    CONTACTED = "contacted"    # отправлено первое сообщение
    QUALIFIED = "qualified"    # квалифицирован AI-агентом
    CONVERTED = "converted"    # стал клиентом
    REJECTED = "rejected"      # не целевой / отказ


class MessageDirection(str, Enum):
    """Направление сообщения относительно нашего аккаунта."""

    INCOMING = "incoming"  # от пользователя к нам (то, что поймал парсер)
    OUTGOING = "outgoing"  # от нас к пользователю (ответ AI/менеджера)


class SourcePlatform(str, Enum):
    """Платформа веб-парсера (Спринт 2). Telegram живёт в MonitoredChat."""

    AVITO = "avito"
    INSTAGRAM = "instagram"


# =============================================================================
# Campaigns — кампании (ниши)
# =============================================================================
class Campaign(SQLModel, table=True):
    """
    Кампания = отдельная ниша со своим набором чатов, триггеров и пресетом.

    Мультитенантность строится вокруг campaign_id: у каждой кампании свои
    мониторируемые чаты и свои лиды.
    """

    __tablename__ = "campaigns"

    id: Optional[int] = Field(default=None, primary_key=True)
    # Тип ниши, напр. "realty", "gambling". Пока строка, позже возможен enum.
    niche_type: str = Field(index=True)
    # Произвольные настройки кампании в JSONB: пресеты, а В БУДУЩЕМ — и
    # триггерные слова (сейчас они в config.TRIGGER_KEYWORDS).
    settings: dict = Field(default_factory=dict, sa_column=Column(JSONB))
    # Папка с базой знаний (RAG) для AI Core этой кампании.
    rag_folder: Optional[str] = Field(default=None)

    # Связи (ORM-удобство, не создают отдельных колонок).
    monitored_chats: list["MonitoredChat"] = Relationship(back_populates="campaign")
    monitored_sources: list["MonitoredSource"] = Relationship(back_populates="campaign")
    leads: list["Lead"] = Relationship(back_populates="campaign")


# =============================================================================
# MonitoredChats — наша таблица со списком мониторимых чатов
# =============================================================================
class MonitoredChat(SQLModel, table=True):
    """
    Чат/канал, который парсер мониторит для конкретной кампании.

    Список чатов ЖИВЁТ В БД (а не в хардкоде), чтобы будущая админка могла
    добавлять/выключать чаты без деплоя. Читаем чаты с is_active=True.
    """

    __tablename__ = "monitored_chats"

    id: Optional[int] = Field(default=None, primary_key=True)
    campaign_id: int = Field(foreign_key="campaigns.id", index=True)
    # Идентификатор чата для Telethon: @username, t.me-ссылка или числовой id.
    # Строка — чтобы одинаково хранить любой из этих форматов.
    chat_identifier: str = Field(index=True)
    # Человекочитаемое название чата (для админки/логов), может обновляться.
    chat_title: Optional[str] = Field(default=None)
    # Флаг активности: парсер подписывается только на активные чаты.
    is_active: bool = Field(default=True, index=True)

    campaign: Optional[Campaign] = Relationship(back_populates="monitored_chats")


# =============================================================================
# MonitoredSources — источники веб-парсеров (Avito / Instagram), Спринт 2
# =============================================================================
class MonitoredSource(SQLModel, table=True):
    """
    Источник для поллинг-парсеров: поисковая выдача Avito или пост Instagram.

    Отдельная таблица (а не MonitoredChat), потому что семантика другая:
    Telegram — подписка на события в реальном времени, здесь — периодический
    поллинг URL. identifier:
        avito     — полный URL поисковой выдачи (с фильтрами/сортировкой)
        instagram — URL поста (мониторим комментарии под ним)
    """

    __tablename__ = "monitored_sources"

    id: Optional[int] = Field(default=None, primary_key=True)
    campaign_id: int = Field(foreign_key="campaigns.id", index=True)
    platform: SourcePlatform = Field(index=True)
    identifier: str = Field(index=True)
    # Человекочитаемое название источника (для админки/логов).
    title: Optional[str] = Field(default=None)
    is_active: bool = Field(default=True, index=True)

    campaign: Optional[Campaign] = Relationship(back_populates="monitored_sources")


# =============================================================================
# Leads — лиды
# =============================================================================
class Lead(SQLModel, table=True):
    """
    Лид — потенциальный клиент, найденный по триггеру в мониторимом чате.
    """

    __tablename__ = "leads"

    id: Optional[int] = Field(default=None, primary_key=True)
    campaign_id: int = Field(foreign_key="campaigns.id", index=True)
    # Источник: откуда пришёл лид (напр. "telegram:@some_chat").
    source: Optional[str] = Field(default=None)
    # Контакт: Telegram user_id/username для последующей связи.
    contact: Optional[str] = Field(default=None, index=True)
    status: LeadStatus = Field(default=LeadStatus.NEW, index=True)
    # Скоринг лида (0..100), проставляется AI-агентом позже.
    score: int = Field(default=0)
    created_at: datetime = Field(default_factory=datetime.utcnow)

    campaign: Optional[Campaign] = Relationship(back_populates="leads")
    messages: list["Message"] = Relationship(back_populates="lead")


# =============================================================================
# Messages — сообщения
# =============================================================================
class Message(SQLModel, table=True):
    """
    Сообщение, привязанное к лиду. Входящие ловит парсер, исходящие пишет
    Celery-таск при отправке ответа.
    """

    __tablename__ = "messages"

    id: Optional[int] = Field(default=None, primary_key=True)
    lead_id: int = Field(foreign_key="leads.id", index=True)
    text: str
    direction: MessageDirection = Field(index=True)
    timestamp: datetime = Field(default_factory=datetime.utcnow, index=True)

    lead: Optional[Lead] = Relationship(back_populates="messages")
