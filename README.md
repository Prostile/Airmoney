# Airmoney

Локальное веб-приложение для полуавтоматического поиска кандидатов на ручную покупку CS-скинов в Steam Market.

Приложение не выполняет автопокупку, не логинится в Steam, не хранит Steam-сессии и не работает со Steam Guard. Оно только сканирует публичные страницы Market, считает потенциальную прибыль, показывает таблицы и отправляет короткие Telegram-алерты.

## Быстрый старт

```bash
python -m pip install -r requirements.txt
python -m playwright install chromium
python -m airmoney init-db
python -m airmoney init-env --user admin
python -m airmoney web
```

Открой:

```text
http://127.0.0.1:8000
```

Логин и пароль лежат в `.env`. Файл `.env` добавлен в `.gitignore`.

## Основные команды

```bash
python -m airmoney scan
python -m airmoney scan --collection-id active_drop
python -m airmoney scan --item-id some_item
python -m airmoney monitor
python -m airmoney web
python -m airmoney export-config --output config.yaml
python -m airmoney import-config --input config.yaml
python -m airmoney import-config --input config.yaml --validate-only
python -m airmoney import-catalog --input examples/catalog.example.yaml
python -m airmoney import-catalog --input examples/catalog.example.yaml --validate-only
python -m airmoney import-legacy-csv --input steam_market_matches.csv
python -m airmoney export-candidates-csv --output data/candidates.csv
python -m airmoney export-candidates-html --output data/candidates.html
```

## Веб-страницы

- `/dashboard` - сводка, статус фонового монитора, курсы валют, последние запуски.
- `/settings` - интервалы, задержки, ROI, комиссия, состояния предметов, Telegram-флаг.
- `/collections` - коллекции, включение/выключение, запуск скана коллекции.
- `/items` - предметы, массовое создание состояний, индивидуальный ROI и правила.
- `/listings` - все найденные лоты Steam Market.
- `/candidates` - рабочая таблица кандидатов с фильтрами, сортировками и статусами.
- `/scan-runs` - история запусков сканера.
- `/import-export` - полный YAML-конфиг и добавочный импорт каталога.

Все рабочие страницы и API защищены Basic Auth. Публичные FastAPI `/docs` и `/openapi.json` отключены.

## Telegram

Telegram настраивается через `.env`:

```text
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
AIRMONEY_SITE_URL=http://127.0.0.1:8000
```

Telegram используется только для коротких алертов по `critical` и `good` кандидатам. Управления покупкой через Telegram нет.

## Каталог предметов

Каталог добавляет коллекции и предметы без замены текущих настроек:

```bash
python -m airmoney import-catalog --input examples/catalog.example.yaml --validate-only
python -m airmoney import-catalog --input examples/catalog.example.yaml
```

Если в каталоге указать `base_name` и список `exteriors`, приложение создаст отдельный `ItemDefinition` на каждое состояние.

## Безопасные границы

В проекте намеренно нет:

- автопокупки;
- Steam-логина;
- хранения Steam-сессии;
- Steam Guard;
- оплаты;
- обхода ограничений Steam;
- скрытой автоматизации покупки.

Покупка остаётся ручной: пользователь сам открывает Steam и принимает решение.
