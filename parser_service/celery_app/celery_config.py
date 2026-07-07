"""
Конфигурация Celery-приложения.

Брокер и backend — Redis (URL из .env). Здесь же создаётся инстанс `celery_app`,
который импортируют задачи и воркер.
"""

from __future__ import annotations

from celery import Celery

from parser_service.config import settings

celery_app = Celery(
    "parser_service",
    broker=settings.redis_url,
    backend=settings.redis_url,
    # Явно указываем модуль с задачами, чтобы воркер их зарегистрировал.
    include=["parser_service.celery_app.tasks"],
)

celery_app.conf.update(
    # Сериализация — JSON (безопасно и переносимо).
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    # Часовой пояс: UTC везде, чтобы суточные лимиты считались однозначно.
    timezone="UTC",
    enable_utc=True,
    # Подтверждать задачу только после выполнения (устойчивость к падению воркера).
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    # Ограничим предвыборку, чтобы задачи с отложенным retry не копились в воркере.
    worker_prefetch_multiplier=1,
)
