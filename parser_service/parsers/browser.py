"""
Общий помощник Playwright для веб-парсеров.

Один браузер на весь прогон поллинга (страница на источник) — экономим
память/время старта. Headless и таймауты — из .env.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager

from playwright.sync_api import BrowserContext, sync_playwright

from parser_service.config import settings

logger = logging.getLogger(__name__)

# Обычный десктопный UA: headless-дефолт Playwright («HeadlessChrome»)
# антиботы режут сразу.
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)


@contextmanager
def browser_context(cookies: list[dict] | None = None) -> Iterator[BrowserContext]:
    """
    Контекст-менеджер: chromium + новый контекст с человеческим UA.

    cookies — опциональные куки (например, sessionid Instagram).
    """
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=settings.playwright_headless)
        context = browser.new_context(
            user_agent=_USER_AGENT,
            viewport={"width": 1366, "height": 900},
            locale="ru-RU",
        )
        context.set_default_timeout(settings.page_timeout_ms)
        if cookies:
            context.add_cookies(cookies)
        try:
            yield context
        finally:
            browser.close()
