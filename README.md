# parser_service — Telegram-парсинг + очередь задач (Спринт 1)

Часть мультиагентной системы поиска клиентов (зона **Integration & Parsing**).
Сервис мониторит Telegram-чаты из БД по триггерным словам, создаёт лидов и
ставит обработку в очередь Celery. Ответы от AI Core — точка интеграции (TODO).

## Что уже есть (Спринт 1)

- **Мультитенантность**: кампании (ниши) → свои чаты, лиды, настройки.
- **Список чатов в БД** (`MonitoredChat`), а не в хардкоде — под будущую админку.
- **Обработка `FloodWaitError`**: слушатель спит нужное время, а не падает.
- **Опциональный прокси** в Telethon — параметр-заглушка, включается из `.env`.
- **Суточный лимит исходящих** (25/аккаунт) на счётчике в Redis.

## Структура

```
parser_service/
├── config.py              # чтение .env, триггерные слова (пока хардкод)
├── db/
│   ├── models.py          # SQLModel: Campaign, MonitoredChat, Lead, Message
│   └── session.py         # engine + сессии, init_db()
├── telegram/
│   ├── client.py          # TelegramClient (Telethon) + proxy-заглушка
│   ├── listener.py        # мониторинг чатов, FloodWaitError, постановка в Celery
│   └── triggers.py        # keyword/regex-матч + точка интеграции AI Core
└── celery_app/
    ├── celery_config.py   # инстанс Celery (брокер/бэкенд = Redis)
    └── tasks.py           # process_incoming_message, send_outgoing_message
scripts/seed.py            # завести тестовую кампанию + чат в БД
docker-compose.yml         # Postgres + Redis для локальной разработки
```

## Требования

- Python **3.11+**
- [`uv`](https://docs.astral.sh/uv/) (менеджер зависимостей) — рекомендуется
- Docker (для локальных Postgres + Redis) — опционально

## 1. Установка

```bash
# с uv (рекомендуется) — создаст .venv и поставит всё из uv.lock
uv sync

# либо классически через pip
pip install -r requirements.txt
```

## 2. Получение API_ID / API_HASH

1. Зайди на **https://my.telegram.org** под своим номером Telegram.
2. Открой **API development tools**.
3. Создай приложение (App title / Short name — любые).
4. Скопируй **App api_id** и **App api_hash**.
5. `cp .env.example .env` и впиши `API_ID`, `API_HASH`, `SESSION_NAME`.

> При первом запуске Telethon попросит номер телефона и код из Telegram —
> после этого создастся файл сессии `parser_session.session`, и повторная
> авторизация не потребуется.

## 3. Запуск Redis и Postgres локально

```bash
# оба сразу через docker compose
docker compose up -d

# проверить
docker compose ps
```

Redis поднимется на `localhost:6379`, Postgres — на `localhost:5432`
(бд `parser_db`, пользователь/пароль `parser` / `parser` — совпадает с `.env.example`).

Если Redis уже стоит локально — можно без Docker, просто укажи `REDIS_URL` в `.env`.

## 4. Инициализация БД и тестовый чат

Таблицы создаются автоматически при старте (`init_db()`), но для первого
прогона заведём тестовую кампанию и чат:

```bash
# замени @your_test_chat на чат/канал, где ты состоишь
uv run python -m scripts.seed @your_test_chat
```

## 5. Запуск Celery worker

```bash
uv run celery -A parser_service.celery_app.celery_config:celery_app worker --loglevel=info
# на Windows при проблемах с пулом добавь:  --pool=solo
```

## 6. Запуск слушателя (первый тестовый прогон)

В отдельном терминале:

```bash
uv run python -m parser_service.telegram.listener
```

Теперь напиши в тестовый чат сообщение с триггерным словом (например «бонус»
или «кадастровый номер»). Ожидаемо:

1. `listener` залогирует срабатывание триггера и создаст `Lead` + `Message`.
2. Поставит задачу `process_incoming_message` в Celery.
3. Celery-worker залогирует `would call AI core here` (заглушка AI Core).

## Триггерные слова

Пока хардкодятся в `parser_service/config.py` → `TRIGGER_KEYWORDS`.
**TODO:** переедут в `Campaign.settings` (JSONB), чтобы у каждой ниши был
свой набор, редактируемый из админки.

## Точки интеграции (TODO для других участников)

- **AI Core** (`triggers.build_reply_placeholder`, `tasks.process_incoming_message`):
  вызов `POST /api/chat` вернёт реальный текст ответа с учётом RAG кампании.
- **Отправка в Telegram** (`tasks.send_outgoing_message`): сейчас заглушка +
  учёт лимита; реальная отправка пойдёт через `TelegramClient`.
- **Прокси** (`client._build_proxy`): включается через `PROXY_*` в `.env`.

## Заметки по лимитам

- Исходящие: не более `DAILY_OUTBOX_LIMIT` (по умолчанию 25) на аккаунт в сутки.
  Счётчик — в Redis, ключ `outbox_count:{account}:{YYYY-MM-DD}` с TTL 24ч.
  При достижении лимита задача откладывается (`retry`), а не теряется.
- Чтение: можно мониторить 50–100 чатов одновременно.
