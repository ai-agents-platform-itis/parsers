"""
Парсер Instagram (Спринт 2, задача 2.1 ТЗ).

Мониторим КОММЕНТАРИИ под постами: identifier источника — URL поста
(https://www.instagram.com/p/XXXX/). Кто спрашивает в комментариях «где
бонус» / «сколько стоит» — тот и лид.

ВАЖНО (ограничения площадки):
  * Без логина Instagram прячет почти всё за стеной входа. Нужна кука
    sessionid залогиненного аккаунта — INSTAGRAM_SESSIONID в .env
    (DevTools -> Application -> Cookies -> instagram.com -> sessionid).
  * Разметка IG обфусцирована и меняется часто — селекторы ниже best-effort,
    при поломке парсер логирует «комментарии не найдены», а не падает.
    Запасной вариант по ТЗ — бесплатный тариф Apify (instagram-comment-scraper),
    интеграция при необходимости на Спринте 4.
  * Поллим редко (INSTAGRAM_POLL_INTERVAL, по умолчанию 10 мин) и с паузами,
    чтобы не словить чекпоинт на аккаунт.
"""

from __future__ import annotations

import hashlib
import logging
import re
import time

from playwright.sync_api import Page

from parser_service.config import settings
from parser_service.db.models import MonitoredSource
from parser_service.parsers.base import BaseParser, ParsedItem
from parser_service.parsers.browser import browser_context

logger = logging.getLogger(__name__)

# Пауза между источниками, сек.
_DELAY_BETWEEN_SOURCES = 8.0

# Ссылка на профиль автора комментария: /username/ (без служебных путей IG).
_PROFILE_HREF = re.compile(r"^/([A-Za-z0-9._]{1,30})/$")
_SERVICE_PATHS = {"p", "reel", "reels", "explore", "accounts", "stories", "direct"}


def _session_cookies() -> list[dict] | None:
    """Собрать куки авторизации из .env (если заданы)."""
    if not settings.instagram_sessionid:
        return None
    return [
        {
            "name": "sessionid",
            "value": settings.instagram_sessionid,
            "domain": ".instagram.com",
            "path": "/",
        }
    ]


def _hit_login_wall(page: Page) -> bool:
    """Понять, что нас редиректнуло на логин / показало стену входа."""
    if "/accounts/login" in page.url:
        return True
    title = (page.title() or "").lower()
    return "log in" in title or "вход" in title


def _extract_comments(page: Page) -> list[ParsedItem]:
    """
    Вытащить комментарии из DOM поста.

    Идём не от CSS-классов (обфусцированы), а от структуры: элементы списка
    внутри article/main, в которых есть ссылка на профиль автора + текст.
    external_id — хеш (автор + текст): стабильного id в DOM нет.
    """
    items: list[ParsedItem] = []
    seen_hashes: set[str] = set()

    for li in page.query_selector_all("article ul li, main ul li"):
        author = None
        for a in li.query_selector_all("a[href]"):
            m = _PROFILE_HREF.match(a.get_attribute("href") or "")
            if m and m.group(1) not in _SERVICE_PATHS:
                author = m.group(1)
                break
        if author is None:
            continue

        raw = (li.inner_text() or "").strip()
        if not raw:
            continue
        # inner_text даёт «username\nтекст\nлайки...» — убираем имя и служебные
        # строки («Нравится», timestamps), оставляем содержимое комментария.
        lines = [
            ln.strip()
            for ln in raw.splitlines()
            if ln.strip()
            and ln.strip() != author
            and not ln.strip().lower().startswith(("нравится", "ответить", "reply", "like"))
        ]
        text = " ".join(lines)
        if not text:
            continue

        digest = hashlib.md5(f"{author}:{text}".encode()).hexdigest()
        if digest in seen_hashes:
            continue
        seen_hashes.add(digest)

        items.append(
            ParsedItem(
                external_id=digest,
                text=text,
                url=page.url,
                contact=f"instagram:@{author}",
            )
        )
    return items


class InstagramParser(BaseParser):
    """Поллинг комментариев постов Instagram через Playwright (sync)."""

    platform = "instagram"

    def fetch_items(self, source: MonitoredSource) -> list[ParsedItem]:
        cookies = _session_cookies()
        if cookies is None:
            logger.warning(
                "[instagram] INSTAGRAM_SESSIONID не задан — без авторизации IG "
                "почти наверняка покажет стену логина."
            )

        with browser_context(cookies=cookies) as ctx:
            page = ctx.new_page()
            try:
                page.goto(source.identifier, wait_until="domcontentloaded")
                # Даём подгрузиться клиентскому рендеру комментариев.
                page.wait_for_timeout(5_000)

                if _hit_login_wall(page):
                    logger.warning(
                        "[instagram] Стена логина для %s — проверь/обнови "
                        "INSTAGRAM_SESSIONID в .env.",
                        source.identifier,
                    )
                    return []

                items = _extract_comments(page)
                if not items:
                    logger.warning(
                        "[instagram] Комментарии не найдены на %s (нет комментариев "
                        "или изменилась разметка — см. примечание в модуле).",
                        source.identifier,
                    )
                return items
            finally:
                page.close()


def poll_sources(sources_with_campaigns) -> int:
    """
    Обработать пачку источников Instagram. Вызывается из Celery-задачи.

    sources_with_campaigns: список (MonitoredSource, Campaign).
    """
    parser = InstagramParser()
    total = 0
    for i, (source, campaign) in enumerate(sources_with_campaigns):
        if i > 0:
            time.sleep(_DELAY_BETWEEN_SOURCES)
        total += parser.process_source(source, campaign)
    return total
