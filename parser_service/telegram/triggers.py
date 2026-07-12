"""
Шим совместимости (Спринт 2): логика триггеров переехала в
parser_service.triggers — теперь она общая для Telegram / Avito / Instagram
и поддерживает per-campaign правила из Campaign.settings.

Старые импорты из этого модуля продолжают работать. Новый код должен
импортировать напрямую из parser_service.triggers.
"""

from __future__ import annotations

from parser_service.triggers import (  # noqa: F401
    TriggerMatch,
    TriggerRuleSet,
    build_reply_placeholder,
    has_trigger,
    match_triggers,
    ruleset_for_campaign,
)
