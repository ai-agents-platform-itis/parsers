"""
Триггеры per-campaign (Спринт 2).

Правила теперь живут в Campaign.settings["triggers"] (JSONB), а не в хардкоде:
у каждой ниши свой набор ключевых слов, регулярок и действий, редактируемый
из будущей админки без деплоя. Формат settings описан в niches.py.

Модуль общий для ВСЕХ платформ (Telegram / Avito / Instagram) — парсеры
передают сюда settings кампании и получают список совпадений.

Совместимость: если у кампании триггеры не заданы (старые кампании Спринта 1),
работает фолбэк на config.TRIGGER_KEYWORDS.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any

from parser_service.config import TRIGGER_KEYWORDS

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TriggerMatch:
    """Результат срабатывания триггера на сообщении."""

    keyword: str        # какое ключевое слово / паттерн сработал
    action_hint: str    # подсказка о желаемом действии (для AI Core / логов)


# Фолбэк-действия для legacy-режима (кампании без settings["triggers"]).
_LEGACY_ACTION_HINTS: dict[str, str] = {
    "бонус": "send_link",
    "промокод": "send_link",
    "скидка": "send_link",
    "кадастровый номер": "send_map",
    "кадастр": "send_map",
}


class TriggerRuleSet:
    """
    Скомпилированный набор правил одной кампании.

    Компиляция регулярок — дорогая операция, поэтому правила компилируются
    один раз и кешируются (см. ruleset_for_campaign).
    """

    def __init__(
        self,
        keywords: list[str],
        regex: list[str],
        actions: dict[str, str],
        default_action: str = "generic",
    ) -> None:
        self._actions = actions
        self._default_action = default_action
        # Ключевые слова матчим по границам слов, регистронезависимо.
        self._compiled: list[tuple[str, re.Pattern[str]]] = [
            (kw, re.compile(rf"\b{re.escape(kw)}\b", re.IGNORECASE))
            for kw in keywords
        ]
        # Произвольные regex-триггеры (задача 2.1 ТЗ: «сколько стоит» и т.п.).
        for raw in regex:
            try:
                self._compiled.append((raw, re.compile(raw, re.IGNORECASE)))
            except re.error:
                # Кривая регулярка из админки не должна ронять парсер.
                logger.warning("Пропускаю некорректный regex-триггер: %r", raw)

    @classmethod
    def from_campaign_settings(cls, settings: dict[str, Any] | None) -> "TriggerRuleSet":
        """
        Построить правила из Campaign.settings.

        Если triggers не заданы — фолбэк на глобальный список Спринта 1,
        чтобы старые кампании продолжали работать.
        """
        triggers = (settings or {}).get("triggers") or {}
        if not triggers:
            return cls(
                keywords=list(TRIGGER_KEYWORDS),
                regex=[],
                actions=dict(_LEGACY_ACTION_HINTS),
            )
        return cls(
            keywords=list(triggers.get("keywords") or []),
            regex=list(triggers.get("regex") or []),
            actions=dict(triggers.get("actions") or {}),
            default_action=triggers.get("default_action", "generic"),
        )

    def match(self, text: str) -> list[TriggerMatch]:
        """
        Найти все сработавшие триггеры в тексте.

        Пустой список = сообщение не интересно, лид не создаём.
        """
        if not text:
            return []
        matches: list[TriggerMatch] = []
        for keyword, pattern in self._compiled:
            if pattern.search(text):
                action = self._actions.get(keyword, self._default_action)
                matches.append(TriggerMatch(keyword=keyword, action_hint=action))
        return matches

    def has_trigger(self, text: str) -> bool:
        """Быстрая проверка: есть ли хоть один триггер в тексте."""
        return bool(self.match(text))


# =============================================================================
# Кеш скомпилированных правил по кампаниям
# =============================================================================
# Ключ — (campaign_id, отпечаток settings): при правке триггеров из админки
# отпечаток меняется и правила перекомпилируются, без рестарта не обойтись
# только для listener Telegram (он держит подписки), поллеры подхватят сами.
_CACHE: dict[tuple[int, str], TriggerRuleSet] = {}


def ruleset_for_campaign(
    campaign_id: int, settings: dict[str, Any] | None
) -> TriggerRuleSet:
    """Вернуть (из кеша) скомпилированные правила кампании."""
    fingerprint = json.dumps(
        (settings or {}).get("triggers") or {}, sort_keys=True, ensure_ascii=False
    )
    key = (campaign_id, fingerprint)
    ruleset = _CACHE.get(key)
    if ruleset is None:
        ruleset = TriggerRuleSet.from_campaign_settings(settings)
        # Чистим устаревшие версии правил этой кампании, чтобы кеш не рос.
        for old_key in [k for k in _CACHE if k[0] == campaign_id]:
            del _CACHE[old_key]
        _CACHE[key] = ruleset
    return ruleset


# =============================================================================
# Legacy API (Спринт 1) — глобальные триггеры без кампании
# =============================================================================
_default_ruleset = TriggerRuleSet.from_campaign_settings(None)


def match_triggers(text: str) -> list[TriggerMatch]:
    """Матч по глобальному (legacy) набору триггеров Спринта 1."""
    return _default_ruleset.match(text)


def has_trigger(text: str) -> bool:
    """Быстрая проверка по глобальному (legacy) набору."""
    return _default_ruleset.has_trigger(text)


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
