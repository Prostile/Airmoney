from __future__ import annotations

import threading
from dataclasses import dataclass

from airmoney.steam.browser import SteamAccessLimited
from airmoney.scheduler.monitor import run_scan_cycle, scan_is_running
from airmoney.storage.repositories import Repository
from airmoney.config.models import utc_now_iso


@dataclass
class MonitorSnapshot:
    thread_alive: bool
    stop_requested: bool
    scan_running: bool
    last_loop_at: str
    last_error: str


class BackgroundMonitor:
    def __init__(self, repo: Repository):
        self.repo = repo
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_loop_at = ""
        self._last_error = ""

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="airmoney-monitor", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=10)

    def snapshot(self) -> MonitorSnapshot:
        return MonitorSnapshot(
            thread_alive=bool(self._thread and self._thread.is_alive()),
            stop_requested=self._stop.is_set(),
            scan_running=scan_is_running(),
            last_loop_at=self._last_loop_at,
            last_error=self._last_error,
        )

    def _loop(self) -> None:
        while not self._stop.is_set():
            settings = self.repo.get_settings()
            self._last_loop_at = utc_now_iso()
            wait_seconds = max(1, int(settings.check_interval_seconds))
            if not settings.enabled:
                self._stop.wait(min(5, wait_seconds))
                continue

            try:
                run_scan_cycle(self.repo, trigger="web_monitor")
                self._last_error = ""
                wait_seconds = max(1, int(self.repo.get_settings().check_interval_seconds))
            except SteamAccessLimited as error:
                self._last_error = str(error)
                wait_seconds = max(1, int(self.repo.get_settings().steam_block_pause_seconds))
            except Exception as error:
                self._last_error = str(error)
                wait_seconds = min(300, max(5, wait_seconds))

            self._stop.wait(wait_seconds)
