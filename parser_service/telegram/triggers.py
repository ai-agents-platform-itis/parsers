"""
Правила триггеров: сопоставление входящего текста с ключевыми словами.

Пока это простой keyword/regex-матч + заглушки-действия. РЕАЛЬНЫЕ ответы
(текст сообщения клиенту) будут приходить от AI Core API — здесь только
точка интеграции (TODO) и предварительная категоризация триггера.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from parser_service.config import TRIGGER_KEYWORDS


@dataclass(frozen=True)
class TriggerMatch:
    """Результат срабатывания триггера на сообщении."""

    keyword: str        # какое ключевое слово сработало
    action_hint: str    # подсказка о желаемом действии (для AI Core / логов)


# Карта "ключевое слово -> подсказка действия".
# ВНИМАНИЕ: это заглушка-логика. Настоящий ответ формирует AI Core.
# TODO: перенести правила в Campaign.settings, чтобы у каждой ниши были свои.
_ACTION_HINTS: dict[str, str] = {
    "бонус": "send_link",             # "бонус" -> прислать ссылку
    "промокод": "send_link",
    "скидка": "send_link",
    "кадастровый номер": "send_map",  # "кадастровый номер" -> прислать карту
    "кадастр": "send_map",
}

# Заранее компилируем регулярки на границах слов для скорости и точности
# (регистронезависимо). Для многословных триггеров \b работает по краям фразы.
_COMPILED: list[tuple[str, re.Pattern[str]]] = [
    (kw, re.compile(rf"\b{re.escape(kw)}\b", re.IGNORECASE))
    for kw in TRIGGER_KEYWORDS
]


def match_triggers(text: str) -> list[TriggerMatch]:
    """
    Найти все сработавшие триггеры в тексте.

    Возвращает список совпадений (может быть пустым). Пустой список = сообщение
    не интересно, лид не создаём.
    """
    if not text:
        return []

    matches: list[TriggerMatch] = []
    for keyword, pattern in _COMPILED:
        if pattern.search(text):
            action_hint = _ACTION_HINTS.get(keyword, "generic")
            matches.append(TriggerMatch(keyword=keyword, action_hint=action_hint))
    return matches


def has_trigger(text: str) -> bool:
    """Быстрая проверка: есть ли хоть один триггер в тексте."""
    return bool(match_triggers(text))


# =============================================================================
# ТОЧКА ИНТЕГРАЦИИ С AI CORE
# =============================================================================
def build_reply_placeholder(text: str, match: TriggerMatch) -> str:
    """
    Заглушка формирования ответа клиенту.

    TODO(AI Core): здесь позже будет вызов AI Core API (POST /api/chat),
    который вернёт реальный текст ответа с учётом RAG-базы кампании.
    Сигнатура вызова (ориентировочно):
        reply = ai_core_client.chat(
            campaign_id=...,
            user_message=text,
            action_hint=match.action_hint,
        )
    Пока возвращаем плейсхолдер, чтобы пайплайн был сквозным.
    """
    return f"[PLACEHOLDER reply: keyword='{match.keyword}', action='{match.action_hint}']"
