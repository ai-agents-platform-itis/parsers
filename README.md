# parser_service — парсинг Telegram/Avito/Instagram, CRM и очередь задач (Спринты 1-4)

Часть мультиагентной системы поиска клиентов (зона **Integration & Parsing**).
Сервис мониторит источники (Telegram-чаты, выдачи Avito, посты Instagram) по
триггерным словам своей ниши, создаёт лидов, скорит их и передаёт «горячих»
в AmoCRM + менеджеру. Запросы к площадкам раскидываются по ротируемому пулу
прокси. Ответы от AI Core — точка интеграции (TODO).

## Что уже есть

Спринт 1:
- **Мультитенантность**: кампании (ниши) → свои чаты, лиды, настройки.
- **Список чатов в БД** (`MonitoredChat`), а не в хардкоде — под будущую админку.
- **Обработка `FloodWaitError`**: слушатель спит нужное время, а не падает.
- **Опциональный прокси** в Telethon — параметр-заглушка, включается из `.env`.
- **Суточный лимит исходящих** (25/аккаунт) на счётчике в Redis.

Спринт 2:
- **Триггеры per-ниша**: правила переехали из хардкода в `Campaign.settings`
  (JSONB) — keywords + regex + карта действий («бонус» → `send_link`,
  «кадастровый номер» → `send_map`). Старые кампании работают по legacy-набору.
- **Пресеты ниш** (`niches.py`): `realty` (участки), `gambling`, `services` —
  шаблон копируется в кампанию при создании (`scripts/seed.py --preset ...`).
- **Парсер Avito**: поллинг поисковых выдач через Playwright, антибот-детект.
- **Парсер Instagram**: мониторинг комментариев постов (нужна кука
  `INSTAGRAM_SESSIONID`), best-effort селекторы.
- **Celery beat**: `poll_avito` / `poll_instagram` по расписанию, дедуп
  обработанных элементов в Redis (TTL `SEEN_ITEMS_TTL_DAYS`).

Спринт 3:
- **AmoCRM** (`crm/amocrm.py`): «горячий» лид уходит сделкой+контактом
  (complex-эндпоинт api/v4, долгосрочный токен), история диалога —
  примечанием, ниша/платформа — тегами. `Lead.crm_lead_id` — связь с amo.
- **Скоринг и порог** (`scoring.py`): интерим-эвристика 0..100 (до
  Агента-Квалификатора из LangGraph); `score >= HOT_LEAD_THRESHOLD` →
  статус `qualified` → задача `push_hot_lead`.
- **Уведомление менеджера** (`crm/notify.py`): пинг в Telegram через обычный
  Bot API (токен `MANAGER_BOT_TOKEN`, чат `MANAGER_CHAT_ID`) со ссылкой на
  сделку в CRM.
- **Реальная отправка в Telegram** (`telegram/sender.py`): включается
  `TELEGRAM_SEND_ENABLED=true`, работает через отдельную сессию Telethon
  (одну SQLite-сессию нельзя делить между listener и воркером). Listener
  теперь сохраняет `@username` лида — по нему воркер может написать.

Спринт 4:
- **Ротация прокси** (`proxy_pool.py`): заглушка Спринта 1 заменена реальным
  пулом. Список из `PROXY_LIST`/`PROXY_FILE`, round-robin через общий счётчик
  в Redis (listener и воркер делят ротацию), «сгоревший» прокси уходит на
  кулдаун (`PROXY_COOLDOWN_SECONDS`) при ошибке подключения Telethon или
  антибот-странице Avito/Instagram. Работает и для Telethon (python-socks),
  и для Playwright.
- **Стресс-режим 3 ниши** (`scripts/stress_seed.py`): поднимает кампании
  Участки/Гемблинг/Услуги разом для нагрузочного теста.

## Структура

```
parser_service/
├── config.py              # чтение .env: парсеры, AmoCRM, горячие лиды, прокси
├── niches.py              # пресеты ниш: триггеры/regex/действия (Спринт 2)
├── triggers.py            # per-campaign правила из Campaign.settings (Спринт 2)
├── scoring.py             # интерим-скоринг лида 0..100 (Спринт 3)
├── proxy_pool.py          # пул прокси: ротация + кулдаун битых (Спринт 4)
├── db/
│   ├── models.py          # Campaign, MonitoredChat, MonitoredSource, Lead, Message
│   └── session.py         # engine + сессии, init_db()
├── telegram/
│   ├── client.py          # TelegramClient (Telethon) + proxy-заглушка
│   ├── listener.py        # мониторинг чатов, FloodWaitError, постановка в Celery
│   ├── sender.py          # реальная отправка из воркера (отдельная сессия)
│   └── triggers.py        # шим: реэкспорт из parser_service.triggers
├── parsers/
│   ├── base.py            # общий пайплайн: fetch → дедуп → триггер → Lead → Celery
│   ├── browser.py         # общий Playwright-контекст (UA, headless из .env)
│   ├── avito.py           # поллинг выдач Avito (data-marker селекторы)
│   └── instagram.py       # комментарии постов IG (sessionid-кука)
├── crm/
│   ├── amocrm.py          # api/v4: сделка+контакт, примечания, теги (Спринт 3)
│   └── notify.py          # пинг менеджеру через Telegram Bot API (Спринт 3)
└── celery_app/
    ├── celery_config.py   # инстанс Celery + beat_schedule поллеров
    └── tasks.py           # process/send/push_hot_lead + поллеры
scripts/seed.py            # кампания из пресета + чат/источник в БД
scripts/stress_seed.py     # 3 ниши разом для стресс-теста (Спринт 4)
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

# браузер для веб-парсеров Avito/Instagram (Спринт 2, ~150 МБ, один раз)
uv run playwright install chromium
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

## 4. Инициализация БД: кампании и источники

Таблицы создаются автоматически при старте (`init_db()`). Кампании создаются
из пресетов ниш (`--preset realty | gambling | services`) — пресет копируется
в `Campaign.settings`, дальше триггеры можно править прямо в БД/админке:

```bash
# Telegram-чат в кампанию «участки» (замени на чат, где ты состоишь)
uv run python -m scripts.seed telegram @your_test_chat --preset realty

# Поисковая выдача Avito (URL со всеми фильтрами, сортировка «по дате»)
uv run python -m scripts.seed avito "https://www.avito.ru/moskva/zemelnye_uchastki?s=104" --preset realty

# Пост Instagram — мониторим комментарии под ним
uv run python -m scripts.seed instagram "https://www.instagram.com/p/XXXX/" --preset gambling

# legacy-режим Спринта 1 (demo-кампания, триггеры из config):
uv run python -m scripts.seed @your_test_chat
```

## 5. Запуск Celery worker (+ beat для поллеров Avito/IG)

```bash
# -B — встроенный beat: раз в AVITO_POLL_INTERVAL / INSTAGRAM_POLL_INTERVAL
# секунд запускает poll_avito / poll_instagram
uv run celery -A parser_service.celery_app.celery_config:celery_app worker -B --loglevel=info
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

## Триггерные слова (Спринт 2: per-ниша)

Правила живут в `Campaign.settings["triggers"]` (JSONB), формат:

```json
{
  "keywords": ["бонус", "промокод"],
  "regex": ["бонус\\s+за\\s+регистрац"],
  "actions": {"бонус": "send_link"},
  "default_action": "send_link"
}
```

Действия (задача 2.4 ТЗ): `send_link` — прислать (реф.) ссылку,
`send_map` — карту участка, `send_price` — прайс, `generic` — обычный ответ.
Подсказка действия уходит в AI Core вместе с текстом (точка интеграции).

Кампании без `triggers` (Спринт 1) работают по legacy-набору
`config.TRIGGER_KEYWORDS`. Шаблоны под ниши — `parser_service/niches.py`.

## Парсеры Avito / Instagram (Спринт 2)

- Запускаются Celery beat'ом (`worker -B`), интервалы — в `.env`.
- **Дедуп**: обработанные объявления/комментарии помнит Redis
  (`seen:{platform}:{source_id}:{external_id}`, TTL `SEEN_ITEMS_TTL_DAYS` дней) —
  лиды не дублируются между прогонами.
- **Avito**: селекторы на `data-marker` атрибутах (стабильнее CSS-классов).
  При антибот-странице прогон пропускается с warning — уменьшай частоту
  поллинга; ротация прокси по roadmap — Спринт 4.
- **Instagram**: без куки `INSTAGRAM_SESSIONID` — стена логина. Разметка IG
  обфусцирована: селекторы best-effort, при поломке — warning в лог, запасной
  вариант по ТЗ — бесплатный тариф Apify (instagram-comment-scraper).
  Соблюдаем правила площадок (ТЗ, раздел 3): редкий поллинг, паузы между
  источниками.

## Горячие лиды → AmoCRM → менеджер (Спринт 3)

Воронка: входящее сообщение → интерим-скоринг (`scoring.py`) → при
`score >= HOT_LEAD_THRESHOLD` лид получает статус `qualified` и задача
`push_hot_lead` создаёт сделку в AmoCRM (диалог — примечанием, ниша и
платформа — тегами) + отправляет пинг менеджеру в Telegram.

Настройка:

1. **AmoCRM**: Настройки → Интеграции → создать свою интеграцию → «Ключи и
   доступы» → долгосрочный токен. В `.env`: `AMOCRM_BASE_URL`
   (https://yourcompany.amocrm.ru) и `AMOCRM_ACCESS_TOKEN`. Без них CRM-шаг
   мягко пропускается (менеджер всё равно уведомляется).
2. **Бот-нотификатор**: создать бота у @BotFather → `MANAGER_BOT_TOKEN`;
   chat_id менеджера узнать у @userinfobot → `MANAGER_CHAT_ID`; менеджер
   должен один раз нажать Start у бота.
3. **Реальная отправка лидам** (опционально): `TELEGRAM_SEND_ENABLED=true` и
   разовая авторизация отдельной сессии отправителя:
   `uv run python -m parser_service.telegram.sender`. Отправка идёт по
   `@username` лида; лиды без username остаются менеджеру (контакт в CRM).

> **Живая БД со Спринтов 1-2**: `init_db()` не добавляет колонки в
> существующие таблицы — выполни один раз:
> `ALTER TABLE leads ADD COLUMN IF NOT EXISTS crm_lead_id INTEGER;`
> (нормальные миграции — Alembic, зона Core Backend).

## Ротация прокси и стресс-тест (Спринт 4)

Включение — одной строкой: `PROXY_ENABLED=true` + список в `PROXY_LIST`:

```bash
PROXY_ENABLED=true
PROXY_LIST=socks5://user:pass@1.2.3.4:1080, http://5.6.7.8:8080, socks5://9.10.11.12:1080
# либо длинный список файлом (по одному прокси на строку):
PROXY_FILE=proxies.txt
```

Как работает пул (`proxy_pool.py`):
- **Ротация** — round-robin через общий счётчик в Redis, поэтому listener,
  воркер и поллеры Avito/IG вместе равномерно раскидывают запросы по IP.
- **Health** — при ошибке подключения Telethon или антибот-странице
  (Avito «доступ ограничен», IG стена логина) прокси уходит на кулдаун
  `PROXY_COOLDOWN_SECONDS` и не выдаётся, пока не «остынет». Если остыли все —
  парсер временно работает напрямую, а не падает.
- Форматы: `socks5://`, `socks4://`, `http://`, `https://`, с авторизацией
  и без. Telethon ходит через `python-socks`, Playwright — через свой
  `proxy=`.

Нагрузочный тест на трёх нишах разом:

```bash
uv run python -m scripts.stress_seed                # демо-набор
uv run python -m scripts.stress_seed --config sources.txt
# затем при включённых прокси — listener + worker -B (см. выше)
```

## Точки интеграции (TODO для других участников)

- **AI Core** (`triggers.build_reply_placeholder`, `tasks.process_incoming_message`):
  вызов `POST /api/chat` вернёт реальный текст ответа с учётом RAG кампании и
  score от Агента-Квалификатора (заменит интерим-эвристику `scoring.py`).
- **Прокси** (`client._build_proxy`): включается через `PROXY_*` в `.env`;
  ротация прокси — Спринт 4.

## Заметки по лимитам

- Исходящие: не более `DAILY_OUTBOX_LIMIT` (по умолчанию 25) на аккаунт в сутки.
  Счётчик — в Redis, ключ `outbox_count:{account}:{YYYY-MM-DD}` с TTL 24ч.
  При достижении лимита задача откладывается (`retry`), а не теряется.
- Чтение: можно мониторить 50–100 чатов одновременно.
