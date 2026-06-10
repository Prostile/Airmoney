from __future__ import annotations

from pathlib import Path


PACKAGE_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = PACKAGE_ROOT.parents[1]
CONFIG_DIR = PROJECT_ROOT / "config"
DATA_DIR = PROJECT_ROOT / "data"
DEFAULT_SETTINGS_PATH = CONFIG_DIR / "default_settings.yaml"
DEFAULT_DB_PATH = DATA_DIR / "airmoney.sqlite3"
CURRENCY_CACHE_PATH = DATA_DIR / "currency_cache.json"
