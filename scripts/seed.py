"""
Сид-скрипт: кампании из пресетов ниш + источники парсинга (Спринт 2).

Примеры:
    # Telegram-чат в кампанию с пресетом "участки"
    uv run python -m scripts.seed telegram @land_chat --preset realty

    # Поисковая выдача Avito (URL с фильтрами) в кампанию "гемблинг"/"участки"
    uv run python -m scripts.seed avito "https://www.avito.ru/all/zemelnye_uchastki?q=..." --preset realty

    # Пост Instagram (мониторим комментарии) в кампанию "гемблинг"
    uv run python -m scripts.seed instagram "https://www.instagram.com/p/XXXX/" --preset gambling

Совместимость со Спринтом 1: `python -m scripts.seed @chat` == telegram-чат
в demo-кампанию (без пресета, триггеры legacy из config).
"""

from __future__ import annotations

import argparse
import sys

from sqlmodel import select

from parser_service.db.models import (
    Campaign,
    MonitoredChat,
    MonitoredSource,
    SourcePlatform,
)
from parser_service.db.session import get_session, init_db
from parser_service.niches import NICHE_PRESETS, build_campaign_settings


def _get_or_create_campaign(db, preset: str | None) -> Campaign:
    """
    Найти кампанию по нише (niche_type == пресет) или создать из пресета.

    preset=None — demo-кампания Спринта 1 (legacy-триггеры из config).
    """
    niche = preset or "demo"
    campaign = db.exec(select(Campaign).where(Campaign.niche_type == niche)).first()
    if campaign is not None:
        print(f"[=] Кампания уже есть: id={campaign.id} (niche={niche})")
        return campaign

    settings = (
        build_campaign_settings(preset)
        if preset
        else {"note": "тестовая кампания для Спринта 1"}
    )
    campaign = Campaign(
        niche_type=niche,
        settings=settings,
        rag_folder=f"rag/{niche}",
    )
    db.add(campaign)
    db.flush()
    print(f"[+] Создана кампания id={campaign.id} (niche={niche})")
    return campaign


def _add_telegram_chat(db, campaign: Campaign, identifier: str) -> None:
    existing = db.exec(
        select(MonitoredChat).where(MonitoredChat.chat_identifier == identifier)
    ).first()
    if existing is None:
        db.add(
            MonitoredChat(
                campaign_id=campaign.id,
                chat_identifier=identifier,
                chat_title="Тестовый чат",
                is_active=True,
            )
        )
        print(f"[+] Добавлен чат {identifier} (active) -> campaign {campaign.id}")
    else:
        existing.is_active = True
        db.add(existing)
        print(f"[=] Чат {identifier} уже есть — включил is_active=True")


def _add_source(
    db, campaign: Campaign, platform: SourcePlatform, identifier: str
) -> None:
    existing = db.exec(
        select(MonitoredSource).where(
            MonitoredSource.platform == platform,
            MonitoredSource.identifier == identifier,
        )
    ).first()
    if existing is None:
        db.add(
            MonitoredSource(
                campaign_id=campaign.id,
                platform=platform,
                identifier=identifier,
                is_active=True,
            )
        )
        print(
            f"[+] Добавлен источник {platform.value}: {identifier} "
            f"-> campaign {campaign.id}"
        )
    else:
        existing.is_active = True
        db.add(existing)
        print(f"[=] Источник уже есть — включил is_active=True")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Завести кампанию (из пресета ниши) и источник парсинга."
    )
    parser.add_argument(
        "platform",
        help="telegram | avito | instagram, либо сразу @chat (legacy-режим)",
    )
    parser.add_argument(
        "identifier",
        nargs="?",
        help="@chat / URL выдачи Avito / URL поста Instagram",
    )
    parser.add_argument(
        "--preset",
        choices=sorted(NICHE_PRESETS),
        help="Пресет ниши для кампании (realty | gambling | services)",
    )
    args = parser.parse_args()

    # Legacy-режим Спринта 1: единственный аргумент-@chat.
    platform = args.platform.lower()
    identifier = args.identifier
    if platform not in ("telegram", "avito", "instagram"):
        platform, identifier = "telegram", args.platform

    if identifier is None:
        print("Ошибка: не передан identifier (@chat или URL).", file=sys.stderr)
        sys.exit(2)

    init_db()

    with get_session() as db:
        campaign = _get_or_create_campaign(db, args.preset)
        if platform == "telegram":
            _add_telegram_chat(db, campaign, identifier)
        else:
            _add_source(db, campaign, SourcePlatform(platform), identifier)

    if platform == "telegram":
        print("Готово. Запусти listener: uv run python -m parser_service.telegram.listener")
    else:
        print(
            "Готово. Поллеры запускаются через Celery beat:\n"
            "  uv run celery -A parser_service.celery_app.celery_config:celery_app "
            "worker -B --loglevel=info"
        )


if __name__ == "__main__":
    main()
