from __future__ import annotations

import threading
import time

from airmoney.steam.browser import SteamAccessLimited
from airmoney.steam.scanner import ScanResult, scan_once
from airmoney.storage.repositories import Repository
from airmoney.telegram.notifier import TelegramNotifier


_SCAN_LOCK = threading.Lock()


def scan_is_running() -> bool:
    return _SCAN_LOCK.locked()


def run_scan_cycle(
    repo: Repository | None = None,
    collection_id: str | None = None,
    item_id: str | None = None,
    trigger: str = "manual",
) -> ScanResult:
    repository = repo or Repository()
    if not _SCAN_LOCK.acquire(blocking=False):
        run_id = repository.start_scan_run(trigger, collection_id=collection_id, item_id=item_id)
        repository.finish_scan_run(run_id, "skipped", error="Другой скан уже выполняется.")
        raise RuntimeError("Другой скан уже выполняется.")
    run_id = repository.start_scan_run(trigger, collection_id=collection_id, item_id=item_id)
    try:
        def progress(**payload) -> None:
            repository.update_scan_run_progress(run_id, **payload)

        result = scan_once(
            repository,
            collection_id=collection_id,
            item_id=item_id,
            progress=progress,
        )
        settings = repository.get_settings()
        skipped = bool(result.message and result.scanned_items == 0)
        alerts_sent = 0 if skipped else TelegramNotifier(repository).send_pending_alerts(settings, force_batch=True)
        repository.finish_scan_run(
            run_id,
            "skipped" if skipped else "success",
            scanned_items=result.scanned_items,
            listings_saved=result.listings_saved,
            candidates_saved=result.candidates_saved,
            alerts_sent=alerts_sent,
            error=result.message,
        )
        return result
    except Exception as error:
        repository.finish_scan_run(run_id, "error", error=str(error))
        raise
    finally:
        _SCAN_LOCK.release()


def monitor(repo: Repository | None = None) -> None:
    repository = repo or Repository()
    while True:
        settings = repository.get_settings()
        if settings.enabled:
            try:
                run_scan_cycle(repository, trigger="monitor")
            except SteamAccessLimited:
                time.sleep(settings.steam_block_pause_seconds)
                continue
        time.sleep(settings.check_interval_seconds)
