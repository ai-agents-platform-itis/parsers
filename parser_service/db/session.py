"""
Инициализация подключения к БД: engine + фабрика сессий.

Используем синхронный engine (psycopg2). Парсер работает в asyncio,
но обращения к БД короткие, поэтому для Спринта 1 держим их синхронными
и вызываем в контекст-менеджере. При росте нагрузки можно перейти на
async-engine (asyncpg) — модели SQLModel останутся теми же.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlmodel import Session, SQLModel, create_engine

from parser_service.config import settings

# echo=False, чтобы не засорять логи SQL-ом; pool_pre_ping — переподключение
# после простоя (Postgres рвёт неактивные коннекты).
engine = create_engine(
    settings.database_url,
    echo=False,
    pool_pre_ping=True,
)


def init_db() -> None:
    """
    Создать все таблицы, которых ещё нет.

    Для Спринта 1 этого достаточно. В проде схему будет катить Alembic
    (миграции ведёт участник, отвечающий за БД).
    """
    # Импорт моделей нужен, чтобы они зарегистрировались в SQLModel.metadata.
    from parser_service.db import models  # noqa: F401

    SQLModel.metadata.create_all(engine)


@contextmanager
def get_session() -> Iterator[Session]:
    """
    Контекст-менеджер сессии с автокоммитом/откатом.

    Пример:
        with get_session() as db:
            db.add(obj)
    """
    session = Session(engine)
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
