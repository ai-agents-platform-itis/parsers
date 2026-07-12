"""
ИНТЕРИМ-скоринг лидов (Спринт 3).

Настоящий скоринг — зона AI Core: Агент-Квалификатор в LangGraph (Ильмир)
будет возвращать score вместе с ответом из POST /api/chat. Пока его нет,
считаем грубую эвристику по триггерам и тексту, чтобы воронка
«горячий лид -> CRM -> менеджер» была сквозной уже сейчас.

Шкала 0..100; порог «горячести» — settings.hot_lead_threshold (.env).
"""

from __future__ import annotations

import re

from parser_service.triggers import TriggerMatch

# Целевые действия «горячее» обычного вопроса: человек уже просит
# ссылку/карту/прайс, а не просто упоминает тему.
_HOT_ACTIONS = {"send_link", "send_map", "send_price"}

# Признаки намерения купить/бюджета: явные суммы, «готов», «куплю» и т.п.
_INTENT_PATTERNS = [
    re.compile(r"\d[\d\s]*(?:т\.?р|тыс|руб|₽|млн|\$)", re.IGNORECASE),
    re.compile(r"\b(куплю|готов|срочно|сегодня|сейчас|бюджет)\b", re.IGNORECASE),
]


def estimate_score(text: str, matches: list[TriggerMatch]) -> int:
    """
    Оценить «горячесть» сообщения (0..100).

    Слагаемые: сработавшие триггеры (основной сигнал), «горячее» действие,
    вопрос в тексте, признаки бюджета/намерения.
    """
    if not matches:
        return 0

    score = 30 + 10 * min(len(matches) - 1, 2)  # 1 триггер=30, каждый след. +10 (макс 50)
    if any(m.action_hint in _HOT_ACTIONS for m in matches):
        score += 20
    if "?" in text:
        score += 10
    if any(p.search(text) for p in _INTENT_PATTERNS):
        score += 20
    return min(score, 100)
