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
from parser_service.proxy_pool import Proxy, next_proxy

logger = logging.getLogger(__name__)

# Обычный десктопный UA: headless-дефолт Playwright («HeadlessChrome»)
# антиботы режут сразу.
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)


@contextmanager
def browser_context(
    cookies: list[dict] | None = None,
) -> Iterator[tuple[BrowserContext, Proxy | None]]:
    """
    Контекст-менеджер: chromium + новый контекст с человеческим UA и прокси
    из пула (ротация, Спринт 4).

    Отдаёт (context, proxy): proxy нужен вызывающему парсеру, чтобы при
    антибот-странице пометить его битым (get_pool().mark_bad). proxy=None —
    работаем без прокси (пул пуст/выключен).

    cookies — опциональные куки (например, sessionid Instagram).
    """
    proxy = next_proxy()
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=settings.playwright_headless,
            proxy=proxy.playwright_proxy() if proxy else None,
        )
        context = browser.new_context(
            user_agent=_USER_AGENT,
            viewport={"width": 1366, "height": 900},
            locale="ru-RU",
        )
        context.set_default_timeout(settings.page_timeout_ms)
        if cookies:
            context.add_cookies(cookies)
        if proxy:
            logger.info("Playwright через прокси %s", proxy.identity)
        try:
            yield context, proxy
        finally:
            browser.close()
