from __future__ import annotations

import threading
import time
import random
from datetime import timedelta

from airmoney.steam.browser import SteamAccessLimited
from airmoney.steam.scanner import ScanResult, scan_once
from airmoney.config.models import parse_dt, utc_now, utc_now_iso
from airmoney.storage.repositories import Repository
from airmoney.telegram.notifier import TelegramNotifier


_SCAN_LOCK = threading.Lock()


def scan_is_running() -> bool:
    return _SCAN_LOCK.locked()


def _legacy_run_scan_cycle(
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
        settings = repository.get_settings()
        cooldown_until = _active_cooldown_until(repository, settings)
        if cooldown_until:
            message = "Steam cooldown active"
            result = ScanResult(
                message=message,
                steam_cooldown_active=True,
                steam_cooldown_until=cooldown_until,
            )
            repository.finish_scan_run(
                run_id,
                "skipped",
                error=message,
                steam_cooldown_active=True,
                steam_cooldown_until=cooldown_until,
            )
            return result

        def progress(**payload) -> None:
            repository.update_scan_run_progress(run_id, **payload)

        result = scan_once(
            repository,
            collection_id=collection_id,
            item_id=item_id,
            progress=progress,
            run_id=run_id,
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
            selected_targets_count=result.selected_targets_count,
            skipped_by_queue_count=result.skipped_by_queue_count,
            skipped_by_item_cooldown_count=result.skipped_by_item_cooldown_count,
            skipped_by_collection_cooldown_count=result.skipped_by_collection_cooldown_count,
            early_stop_count=result.early_stop_count,
            resource_blocked_count=result.resource_blocked_count,
            shallow_skipped_count=result.shallow_skipped_count,
            deep_scan_count=result.deep_scan_count,
            steam_cooldown_active=result.steam_cooldown_active,
            steam_cooldown_until=result.steam_cooldown_until,
        )
        return result
    except SteamAccessLimited as error:
        settings = repository.get_settings()
        cooldown_until = _set_steam_cooldown(repository, settings, str(error))
        repository.finish_scan_run(
            run_id,
            "error",
            error=str(error),
            steam_cooldown_active=bool(cooldown_until),
            steam_cooldown_until=cooldown_until,
        )
        raise
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


def _active_cooldown_until(repository: Repository, settings) -> str:
    guard = settings.steam_guard_settings
    if not guard.enabled:
        return ""
    state = repository.get_steam_guard_state()
    cooldown_until = parse_dt(state.get("steam_cooldown_until"))
    if cooldown_until and utc_now() < cooldown_until:
        return str(state.get("steam_cooldown_until") or "")
    return ""


def _set_steam_cooldown(repository: Repository, settings, reason: str) -> str:
    guard = settings.steam_guard_settings
    if not guard.enabled:
        return ""
    state = repository.get_steam_guard_state()
    consecutive_blocks = int(state.get("steam_consecutive_blocks") or 0) + 1
    seconds = guard.cooldown_on_limit_seconds * (guard.backoff_multiplier ** max(0, consecutive_blocks - 1))
    seconds = min(guard.max_cooldown_seconds, int(seconds))
    if guard.jitter_percent > 0:
        jitter = guard.jitter_percent / 100
        seconds = int(seconds * random.uniform(1 - jitter, 1 + jitter))
        seconds = max(1, min(guard.max_cooldown_seconds, seconds))
    cooldown_until = (utc_now() + timedelta(seconds=seconds)).replace(microsecond=0).isoformat()
    repository.set_steam_guard_state(
        cooldown_until=cooldown_until,
        reason=reason,
        consecutive_blocks=consecutive_blocks,
        last_error_at=utc_now_iso(),
    )
    return cooldown_until
