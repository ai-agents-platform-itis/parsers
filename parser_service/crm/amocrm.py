"""
Клиент AmoCRM (Спринт 3, задача 2.3 ТЗ — передача лидов в AmoCRM).

Работаем через REST API v4 с долгосрочным токеном (бесплатный тариф amoCRM
это позволяет; полноценный OAuth-флоу с refresh-токенами не нужен).

Используем «комплексное создание» (POST /api/v4/leads/complex): сделка +
контакт одним запросом, без ручной связки сущностей. История диалога уходит
примечанием к сделке, ниша и платформа — тегами.

Сетевые ретраи — tenacity (3 попытки с экспоненциальной паузой); ошибки 4xx
не ретраим (это ошибка конфигурации/данных, а не сети).
"""

from __future__ import annotations

import logging
from typing import Any

import requests
from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

from parser_service.config import settings

logger = logging.getLogger(__name__)

_TIMEOUT = 15  # сек на HTTP-запрос


class AmoCRMNotConfigured(RuntimeError):
    """AmoCRM не настроен в .env (AMOCRM_BASE_URL / AMOCRM_ACCESS_TOKEN)."""


def is_configured() -> bool:
    """Проверить, задан ли AmoCRM в .env (для мягкого скипа в задачах)."""
    return bool(settings.amocrm_base_url and settings.amocrm_access_token)


def _should_retry(exc: BaseException) -> bool:
    """Ретраим сетевые сбои и 5xx; 4xx — нет (конфиг/данные, ретрай бесполезен)."""
    if isinstance(exc, requests.HTTPError) and exc.response is not None:
        return exc.response.status_code >= 500
    return isinstance(exc, requests.RequestException)


@retry(
    retry=retry_if_exception(_should_retry),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=2, max=30),
    reraise=True,
)
def _request(method: str, path: str, json: Any) -> Any:
    """Запрос к API v4 с Bearer-токеном и ретраями."""
    if not is_configured():
        raise AmoCRMNotConfigured(
            "Заполни AMOCRM_BASE_URL и AMOCRM_ACCESS_TOKEN в .env"
        )
    url = f"{settings.amocrm_base_url.rstrip('/')}{path}"
    resp = requests.request(
        method,
        url,
        json=json,
        headers={"Authorization": f"Bearer {settings.amocrm_access_token}"},
        timeout=_TIMEOUT,
    )
    resp.raise_for_status()
    # 204 No Content у amo не встречается на наших ручках, но подстрахуемся.
    return resp.json() if resp.content else None


def create_lead(
    name: str,
    contact_name: str,
    tags: list[str],
    price: int | None = None,
) -> int:
    """
    Создать сделку + контакт (complex-эндпоинт). Вернуть id сделки в amo.

    Опциональные поля воронки/статуса/ответственного берутся из .env —
    если не заданы, amo кладёт сделку в первый этап основной воронки.
    """
    lead: dict[str, Any] = {
        "name": name,
        "_embedded": {
            "contacts": [{"first_name": contact_name}],
            "tags": [{"name": t} for t in tags if t],
        },
    }
    if price is not None:
        lead["price"] = price
    if settings.amocrm_pipeline_id:
        lead["pipeline_id"] = settings.amocrm_pipeline_id
    if settings.amocrm_status_id:
        lead["status_id"] = settings.amocrm_status_id
    if settings.amocrm_responsible_user_id:
        lead["responsible_user_id"] = settings.amocrm_responsible_user_id

    data = _request("POST", "/api/v4/leads/complex", json=[lead])
    crm_lead_id = int(data[0]["id"])
    logger.info("AmoCRM: создана сделка id=%s (%s)", crm_lead_id, name)
    return crm_lead_id


def add_note(crm_lead_id: int, text: str) -> None:
    """Добавить текстовое примечание к сделке (история диалога, контакт)."""
    # amo ограничивает длину примечания; диалог длиннее — обрезаем с хвоста,
    # свежие сообщения важнее.
    payload = [
        {
            "entity_id": crm_lead_id,
            "note_type": "common",
            "params": {"text": text[-8000:]},
        }
    ]
    _request("POST", "/api/v4/leads/notes", json=payload)
    logger.info("AmoCRM: примечание добавлено к сделке id=%s", crm_lead_id)
