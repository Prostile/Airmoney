from airmoney.scheduler.service import BackgroundMonitor
from airmoney.storage.repositories import Repository


def test_background_monitor_starts_and_stops_when_disabled(tmp_path):
    repo = Repository(tmp_path / "test.sqlite3")
    monitor = BackgroundMonitor(repo)
    monitor.start()
    snapshot = monitor.snapshot()
    assert snapshot.thread_alive
    assert not snapshot.scan_running
    monitor.stop()
    assert not monitor.snapshot().thread_alive
