"""
Пул прокси с ротацией и health-tracking (Спринт 4, задача «ротация прокси»).

Зачем: при стресс-тесте на трёх нишах одновременно (Участки/Гемблинг/Услуги)
парсеры Telegram/Avito/Instagram бьют по площадкам с одного IP и ловят баны.
Ротация раскидывает запросы по нескольким прокси и выводит «сгоревшие» из
оборота на время.

Как работает:
  * Список прокси берётся из settings.proxy.urls (PROXY_LIST / PROXY_FILE /
    legacy PROXY_*), каждый парсится в Proxy.
  * next_proxy() отдаёт прокси round-robin (общий счётчик в Redis, чтобы
    воркер и listener делили ротацию), пропуская те, что «на кулдауне».
  * mark_bad(proxy) кладёт прокси на кулдаун (settings.proxy.cooldown_seconds)
    — ключ в Redis с TTL, по истечении прокси снова в игре.

Форматы одного прокси (URL): socks5://user:pass@host:port, http://host:port.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from urllib.parse import urlparse

import redis

from parser_service.config import settings

logger = logging.getLogger(__name__)

_redis = redis.Redis.from_url(settings.redis_url, decode_responses=True)

# Ключи Redis.
_RR_KEY = "proxy:rr"                  # round-robin счётчик (INCR)
_COOLDOWN_KEY = "proxy:cooldown:{}"   # {} = identity прокси; наличие ключа = бан

# Схемы, которые понимает и Telethon (python-socks), и Playwright.
_ALLOWED_SCHEMES = {"socks5", "socks4", "http", "https"}


@dataclass(frozen=True)
class Proxy:
    """Один прокси, распарсенный из URL."""

    scheme: str
    host: str
    port: int
    username: str | None = None
    password: str | None = None

    @classmethod
    def from_url(cls, url: str) -> "Proxy":
        parsed = urlparse(url)
        scheme = (parsed.scheme or "socks5").lower()
        if scheme not in _ALLOWED_SCHEMES:
            raise ValueError(f"Неподдерживаемая схема прокси: {scheme!r} в {url!r}")
        if not parsed.hostname or not parsed.port:
            raise ValueError(f"В прокси не указан host:port — {url!r}")
        return cls(
            scheme=scheme,
            host=parsed.hostname,
            port=parsed.port,
            username=parsed.username or None,
            password=parsed.password or None,
        )

    @property
    def identity(self) -> str:
        """Стабильный ключ прокси (host:port) — для кулдауна и логов."""
        return f"{self.host}:{self.port}"

    def telethon_proxy(self):
        """
        Кортеж для Telethon (движок python-socks):
            (scheme, host, port, rdns, username, password)
        rdns=True — резолвим DNS на стороне прокси (не палим DNS-запросы).
        """
        return (
            self.scheme,
            self.host,
            self.port,
            True,
            self.username,
            self.password,
        )

    def playwright_proxy(self) -> dict:
        """Словарь для Playwright launch(proxy=...)."""
        server = f"{self.scheme}://{self.host}:{self.port}"
        proxy: dict = {"server": server}
        if self.username and self.password:
            proxy["username"] = self.username
            proxy["password"] = self.password
        return proxy


class ProxyPool:
    """Пул прокси с round-robin ротацией и кулдауном битых."""

    def __init__(self, urls: list[str] | None = None, cooldown: int | None = None):
        raw_urls = urls if urls is not None else list(settings.proxy.urls)
        self.cooldown = (
            cooldown if cooldown is not None else settings.proxy.cooldown_seconds
        )
        self._proxies: list[Proxy] = []
        for url in raw_urls:
            try:
                self._proxies.append(Proxy.from_url(url))
            except ValueError as exc:
                # Кривой прокси в списке не должен ронять весь пул.
                logger.warning("Пропускаю прокси: %s", exc)

    @property
    def size(self) -> int:
        return len(self._proxies)

    def _is_on_cooldown(self, proxy: Proxy) -> bool:
        return bool(_redis.exists(_COOLDOWN_KEY.format(proxy.identity)))

    def mark_bad(self, proxy: Proxy) -> None:
        """Отправить прокси на кулдаун после ошибки/бана."""
        _redis.set(_COOLDOWN_KEY.format(proxy.identity), "1", ex=self.cooldown)
        logger.warning(
            "Прокси %s на кулдауне %d сек (ошибка/бан)", proxy.identity, self.cooldown
        )

    def next_proxy(self) -> Proxy | None:
        """
        Вернуть следующий рабочий прокси (round-robin), пропуская кулдаунные.

        None — пул пуст или все прокси сейчас на кулдауне (вызывающий код
        решает: работать без прокси или подождать).
        """
        if not self._proxies:
            return None

        n = len(self._proxies)
        # Стартовое смещение — общий счётчик в Redis (ротация между процессами).
        try:
            start = int(_redis.incr(_RR_KEY))
        except redis.RedisError:
            start = 0  # Redis недоступен — берём с начала, лишь бы работать

        for i in range(n):
            proxy = self._proxies[(start + i) % n]
            if not self._is_on_cooldown(proxy):
                return proxy

        logger.warning(
            "Все %d прокси на кулдауне — временно работаю без прокси", n
        )
        return None


# Единый общий пул (ленивая инициализация, чтобы не читать Redis при импорте).
_pool: ProxyPool | None = None


def get_pool() -> ProxyPool:
    """Вернуть общий инстанс пула."""
    global _pool
    if _pool is None:
        _pool = ProxyPool()
    return _pool


def next_proxy() -> Proxy | None:
    """
    Следующий прокси из общего пула — или None, если прокси выключены/пусты.

    Уважает settings.proxy.enabled: при PROXY_ENABLED=false всегда None
    (работаем напрямую), чтобы включение/выключение ротации было одной строкой.
    """
    if not settings.proxy.enabled:
        return None
    return get_pool().next_proxy()
