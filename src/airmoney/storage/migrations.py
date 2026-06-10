from __future__ import annotations

from pathlib import Path

from airmoney.storage.db import initialize_database


def migrate(db_path: str | Path | None = None) -> None:
    initialize_database(db_path)
