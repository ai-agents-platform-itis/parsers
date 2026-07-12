"""
Парсер Avito (Спринт 2, задача 2.1 ТЗ).

Мониторим поисковые выдачи: identifier источника — полный URL выдачи со всеми
фильтрами (регион, категория, запрос, сортировка «по дате»). Каждый прогон
собираем карточки объявлений с первой страницы, триггеры и дедуп — в base.py.

Селекторы построены на data-marker атрибутах Avito — они стабильнее CSS-классов
(классы у Avito генерируются и меняются в каждом релизе). Если Avito поменяет
разметку, парсер залогирует «карточки не найдены», а не упадёт.

Антибот: Avito агрессивно банит по IP. Меры Спринта 2 — человеческий UA,
консервативный интервал поллинга, пауза между источниками. Ротация прокси —
Спринт 4 (по roadmap ТЗ).
"""

from __future__ import annotations

import logging
import time

from playwright.sync_api import Page

from parser_service.db.models import MonitoredSource
from parser_service.parsers.base import BaseParser, ParsedItem
from parser_service.parsers.browser import browser_context

logger = logging.getLogger(__name__)

# Пауза между источниками, сек (не долбим Avito пачкой запросов подряд).
_DELAY_BETWEEN_SOURCES = 5.0


def _looks_blocked(page: Page) -> bool:
    """Определить страницу антибот-проверки / блокировки по IP."""
    title = (page.title() or "").lower()
    return "доступ ограничен" in title or "проверка" in title or "captcha" in title


def _extract_items(page: Page) -> list[ParsedItem]:
    """Вытащить карточки объявлений из загруженной выдачи."""
    items: list[ParsedItem] = []
    for card in page.query_selector_all('[data-marker="item"]'):
        # data-item-id — стабильный числовой id объявления (основа дедупа).
        external_id = card.get_attribute("data-item-id")
        if not external_id:
            continue

        title_el = card.query_selector('[itemprop="name"]')
        link_el = card.query_selector('a[data-marker="item-title"]')
        desc_el = card.query_selector('[data-marker="item-descr"]') or card.query_selector(
            'meta[itemprop="description"]'
        )
        price_el = card.query_selector('meta[itemprop="price"]')

        title = title_el.inner_text().strip() if title_el else ""
        description = ""
        if desc_el:
            description = (
                desc_el.get_attribute("content")
                if desc_el.get_attribute("content")
                else desc_el.inner_text()
            ).strip()
        price = price_el.get_attribute("content") if price_el else None

        href = link_el.get_attribute("href") if link_el else None
        url = f"https://www.avito.ru{href}" if href and href.startswith("/") else href

        # Текст для триггеров: заголовок + описание (+ цена для контекста).
        text = "\n".join(part for part in (title, description) if part)
        if price:
            text += f"\nЦена: {price}"
        if not text:
            continue

        items.append(
            ParsedItem(
                external_id=external_id,
                text=text,
                url=url,
                contact=url,  # контакт продавца доступен только внутри объявления
            )
        )
    return items


class AvitoParser(BaseParser):
    """Поллинг поисковых выдач Avito через Playwright (sync)."""

    platform = "avito"

    def fetch_items(self, source: MonitoredSource) -> list[ParsedItem]:
        with browser_context() as (ctx, proxy):
            page = ctx.new_page()
            try:
                page.goto(source.identifier, wait_until="domcontentloaded")
                if _looks_blocked(page):
                    # Антибот сработал — вероятно, сгорел IP этого прокси.
                    if proxy is not None:
                        from parser_service.proxy_pool import get_pool

                        get_pool().mark_bad(proxy)
                    logger.warning(
                        "[avito] Антибот-страница для %s — пропускаю прогон "
                        "(прокси %s на кулдаун). Следующий прогон — с другого IP.",
                        source.identifier,
                        proxy.identity if proxy else "off",
                    )
                    return []
                try:
                    page.wait_for_selector('[data-marker="item"]', timeout=15_000)
                except Exception:
                    # Таймаут: пустая выдача или сменилась разметка — не фатал.
                    logger.warning(
                        "[avito] Карточки не найдены на %s (пустая выдача или "
                        "изменилась разметка).",
                        source.identifier,
                    )
                    return []
                return _extract_items(page)
            finally:
                page.close()


def poll_sources(sources_with_campaigns) -> int:
    """
    Обработать пачку источников Avito. Вызывается из Celery-задачи.

    sources_with_campaigns: список (MonitoredSource, Campaign).
    """
    parser = AvitoParser()
    total = 0
    for i, (source, campaign) in enumerate(sources_with_campaigns):
        if i > 0:
            time.sleep(_DELAY_BETWEEN_SOURCES)
        total += parser.process_source(source, campaign)
    return total
