"""
Стресс-сид (Спринт 4): поднять три ниши одновременно для нагрузочного теста.

По roadmap ТЗ команда тестирует систему на трёх нишах разом (Участки,
Гемблинг, Услуги). Скрипт заводит по кампании на каждый пресет и цепляет к
ним источники из файла-конфига (по умолчанию — демо-набор ниже).

Запуск:
    uv run python -m scripts.stress_seed
    uv run python -m scripts.stress_seed --config sources.txt

Формат config-файла (строки, '#'-комментарии игнорируются):
    realty telegram @some_land_chat
    gambling avito https://www.avito.ru/...
    services instagram https://www.instagram.com/p/XXXX/
"""

from __future__ import annotations

import argparse

from sqlmodel import select

from parser_service.db.models import (
    Campaign,
    MonitoredChat,
    MonitoredSource,
    SourcePlatform,
)
from parser_service.db.session import get_session, init_db
from parser_service.niches import build_campaign_settings

# Демо-набор источников по нишам (замени на реальные чаты/выдачи/посты).
# Публичные заглушки — чтобы скрипт отработал сквозняком без файла-конфига.
_DEMO_SOURCES: list[tuple[str, str, str]] = [
    ("realty", "telegram", "@land_deals_demo"),
    ("gambling", "telegram", "@casino_talk_demo"),
    ("services", "telegram", "@services_market_demo"),
]

_PRESETS = ("realty", "gambling", "services")


def _parse_config(path: str) -> list[tuple[str, str, str]]:
    rows: list[tuple[str, str, str]] = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split(maxsplit=2)
            if len(parts) != 3:
                raise SystemExit(f"Строка не в формате '<niche> <platform> <id>': {line!r}")
            rows.append((parts[0], parts[1], parts[2]))
    return rows


def _campaign_for(db, niche: str) -> Campaign:
    camp = db.exec(select(Campaign).where(Campaign.niche_type == niche)).first()
    if camp is None:
        camp = Campaign(
            niche_type=niche,
            settings=build_campaign_settings(niche),
            rag_folder=f"rag/{niche}",
        )
        db.add(camp)
        db.flush()
        print(f"[+] Кампания {niche} (id={camp.id})")
    return camp


def _attach(db, camp: Campaign, platform: str, identifier: str) -> None:
    if platform == "telegram":
        exists = db.exec(
            select(MonitoredChat).where(MonitoredChat.chat_identifier == identifier)
        ).first()
        if exists is None:
            db.add(
                MonitoredChat(
                    campaign_id=camp.id, chat_identifier=identifier, is_active=True
                )
            )
            print(f"    + telegram {identifier}")
    else:
        plat = SourcePlatform(platform)
        exists = db.exec(
            select(MonitoredSource).where(
                MonitoredSource.platform == plat,
                MonitoredSource.identifier == identifier,
            )
        ).first()
        if exists is None:
            db.add(
                MonitoredSource(
                    campaign_id=camp.id,
                    platform=plat,
                    identifier=identifier,
                    is_active=True,
                )
            )
            print(f"    + {platform} {identifier}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Поднять 3 ниши для стресс-теста.")
    ap.add_argument("--config", help="файл со строками '<niche> <platform> <id>'")
    args = ap.parse_args()

    rows = _parse_config(args.config) if args.config else _DEMO_SOURCES

    # Гарантируем, что все три пресет-кампании существуют, даже если в конфиге
    # источники не на каждую нишу.
    init_db()
    with get_session() as db:
        for niche in _PRESETS:
            _campaign_for(db, niche)
        for niche, platform, identifier in rows:
            if niche not in _PRESETS:
                print(f"[!] Пропускаю неизвестную нишу: {niche}")
                continue
            camp = _campaign_for(db, niche)
            _attach(db, camp, platform, identifier)

    print(
        "\nГотово. Для стресс-теста включи ротацию прокси (PROXY_ENABLED=true,"
        " PROXY_LIST=...) и запусти listener + worker -B."
    )


if __name__ == "__main__":
    main()
