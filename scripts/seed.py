"""
Сид-скрипт: создаёт тестовую кампанию и один мониторимый чат в БД.

Запуск:
    uv run python -m scripts.seed @your_test_chat

Если аргумент не передан — используется @durov (публичный, для демонстрации;
замени на свой тестовый чат/канал, где ты состоишь).
"""

from __future__ import annotations

import sys

from sqlmodel import select

from parser_service.db.models import Campaign, MonitoredChat
from parser_service.db.session import get_session, init_db


def main() -> None:
    chat_identifier = sys.argv[1] if len(sys.argv) > 1 else "@durov"

    init_db()

    with get_session() as db:
        # Кампания: не плодим дубли — ищем по niche_type.
        campaign = db.exec(
            select(Campaign).where(Campaign.niche_type == "demo")
        ).first()
        if campaign is None:
            campaign = Campaign(
                niche_type="demo",
                settings={"note": "тестовая кампания для Спринта 1"},
                rag_folder="rag/demo",
            )
            db.add(campaign)
            db.flush()
            print(f"[+] Создана кампания id={campaign.id} (niche=demo)")
        else:
            print(f"[=] Кампания уже есть: id={campaign.id}")

        # Мониторимый чат.
        existing = db.exec(
            select(MonitoredChat).where(
                MonitoredChat.chat_identifier == chat_identifier
            )
        ).first()
        if existing is None:
            chat = MonitoredChat(
                campaign_id=campaign.id,
                chat_identifier=chat_identifier,
                chat_title="Тестовый чат",
                is_active=True,
            )
            db.add(chat)
            print(f"[+] Добавлен чат {chat_identifier} (active) -> campaign {campaign.id}")
        else:
            existing.is_active = True
            db.add(existing)
            print(f"[=] Чат {chat_identifier} уже есть — включил is_active=True")

    print("Готово. Запусти listener: uv run python -m parser_service.telegram.listener")


if __name__ == "__main__":
    main()
