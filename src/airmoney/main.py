from __future__ import annotations

import argparse
import secrets
import sys
from pathlib import Path

from airmoney.config.catalog_import import import_catalog_text
from airmoney.config.import_export import export_config, import_config_file
from airmoney.currency.steam_currency import CurrencyService
from airmoney.paths import PROJECT_ROOT
from airmoney.reports.csv_export import export_candidates_csv
from airmoney.reports.html_export import export_candidates_html
from airmoney.reports.legacy_import import import_legacy_matches_csv
from airmoney.scheduler.monitor import monitor, run_scan_cycle
from airmoney.storage.db import initialize_database
from airmoney.storage.repositories import Repository


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="airmoney")
    subparsers = parser.add_subparsers(dest="command", required=True)

    scan = subparsers.add_parser("scan", help="Запустить один цикл сканирования")
    scan.add_argument("--collection-id")
    scan.add_argument("--item-id")
    subparsers.add_parser("monitor", help="Запустить постоянный мониторинг")

    web = subparsers.add_parser("web", help="Запустить веб-интерфейс")
    web.add_argument("--host", default="127.0.0.1")
    web.add_argument("--port", type=int, default=8000)

    export = subparsers.add_parser("export-config", help="Экспортировать YAML-конфиг")
    export.add_argument("--output", required=True)

    import_config = subparsers.add_parser("import-config", help="Импортировать YAML/JSON-конфиг")
    import_config.add_argument("--input", required=True)
    import_config.add_argument("--validate-only", action="store_true")

    subparsers.add_parser("init-db", help="Инициализировать SQLite")

    env = subparsers.add_parser("init-env", help="Создать локальный .env для веб-авторизации")
    env.add_argument("--user", default="admin")
    env.add_argument("--password")
    env.add_argument("--force", action="store_true")

    candidates_csv = subparsers.add_parser("export-candidates-csv", help="Экспортировать кандидатов в CSV")
    candidates_csv.add_argument("--output", required=True)

    candidates_html = subparsers.add_parser("export-candidates-html", help="Экспортировать кандидатов в HTML")
    candidates_html.add_argument("--output", required=True)

    legacy = subparsers.add_parser("import-legacy-csv", help="Импортировать старый steam_market_matches.csv в SQLite")
    legacy.add_argument("--input", default="steam_market_matches.csv")
    legacy.add_argument("--collection-id", default="legacy_csv")

    catalog = subparsers.add_parser("import-catalog", help="Добавить каталог коллекций/предметов без замены конфига")
    catalog.add_argument("--input", required=True)
    catalog.add_argument("--validate-only", action="store_true")

    subparsers.add_parser("refresh-currency", help="Принудительно обновить курсы валют")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    repo = Repository()

    if args.command == "init-db":
        initialize_database()
        print("SQLite готова.")
        return 0

    if args.command == "init-env":
        env_path = PROJECT_ROOT / ".env"
        if env_path.exists() and not args.force:
            print(f".env уже существует: {env_path}")
            return 0
        password = args.password or secrets.token_urlsafe(18)
        env_path.write_text(
            "\n".join(
                [
                    f"AIRMONEY_WEB_USER={args.user}",
                    f"AIRMONEY_WEB_PASSWORD={password}",
                    "AIRMONEY_SITE_URL=http://127.0.0.1:8000",
                    "",
                    "TELEGRAM_BOT_TOKEN=",
                    "TELEGRAM_CHAT_ID=",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        print(f".env создан: {env_path}")
        print(f"Логин: {args.user}")
        print(f"Пароль: {password}")
        return 0

    if args.command == "scan":
        result = run_scan_cycle(
            repo,
            collection_id=args.collection_id,
            item_id=args.item_id,
            trigger="cli",
        )
        if result.message:
            print(f"Скан пропущен: {result.message}")
        else:
            print(
                f"Скан завершён: предметов {result.scanned_items}, "
                f"лотов {result.listings_saved}, кандидатов {result.candidates_saved}."
            )
        return 0

    if args.command == "monitor":
        monitor(repo)
        return 0

    if args.command == "web":
        try:
            import uvicorn
        except ImportError:
            print("Для веб-интерфейса нужен uvicorn. Установи зависимости из requirements.txt.", file=sys.stderr)
            return 2
        uvicorn.run("airmoney.web.app:app", host=args.host, port=args.port, reload=False)
        return 0

    if args.command == "export-config":
        Path(args.output).write_text(export_config(repo), encoding="utf-8")
        print(f"Конфиг экспортирован: {args.output}")
        return 0

    if args.command == "import-config":
        result = import_config_file(repo, args.input, apply=not args.validate_only)
        if not result.valid:
            print("Конфиг невалиден:")
            for error in result.errors:
                print(f"- {error}")
            return 1
        if args.validate_only:
            print("Конфиг валиден. Изменения не применялись.")
        else:
            print("Конфиг импортирован.")
        return 0

    if args.command == "export-candidates-csv":
        output = export_candidates_csv(args.output, repo)
        print(f"CSV экспортирован: {output}")
        return 0

    if args.command == "export-candidates-html":
        output = export_candidates_html(args.output, repo)
        print(f"HTML экспортирован: {output}")
        return 0

    if args.command == "import-legacy-csv":
        count = import_legacy_matches_csv(args.input, repo, collection_id=args.collection_id)
        print(f"Импортировано строк: {count}")
        return 0

    if args.command == "import-catalog":
        text = Path(args.input).read_text(encoding="utf-8")
        result = import_catalog_text(repo, text, apply=not args.validate_only)
        if not result.valid:
            print("Каталог невалиден:")
            for error in result.errors:
                print(f"- {error}")
            return 1
        action = "валиден" if args.validate_only else "импортирован"
        print(
            f"Каталог {action}: коллекций {result.collections_count}, "
            f"предметов {result.items_count}, правил {result.rules_count}."
        )
        return 0

    if args.command == "refresh-currency":
        settings = repo.get_settings()
        rates = CurrencyService(settings).get_rates(force_refresh=True)
        repo.save_currency_rate(
            rates.usd_to_rub,
            rates.eur_to_rub,
            rates.source,
            rates.fetched_at_iso,
            rates.is_fallback,
        )
        print(
            f"USD/RUB={rates.usd_to_rub:.2f}; "
            f"EUR/RUB={rates.eur_to_rub:.2f}; "
            f"source={rates.source}; fetched_at={rates.fetched_at_iso}"
        )
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
