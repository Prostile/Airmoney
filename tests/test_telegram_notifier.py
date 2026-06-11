from datetime import timedelta

from airmoney.config.models import ParserSettings, utc_now, utc_now_iso
from airmoney.telegram import notifier as notifier_module
from airmoney.telegram.notifier import TelegramNotifier


class FakeAlertRepo:
    def __init__(self, candidates):
        self.candidates = candidates
        self.logged = []

    def latest_currency_rate(self):
        return None

    def list_unsent_alert_candidates(self, limit=20):
        return self.candidates[:limit]

    def log_telegram_alert(self, candidate_id, status, error=""):
        self.logged.append((candidate_id, status, error))


def candidate(candidate_id, created_at, level="good"):
    return {
        "id": candidate_id,
        "recommendation_level": level,
        "skin_name": "Souvenir UMP-45 | Mechanism (Factory New)",
        "buy_price_rub": 472,
        "float_value": 0.0115,
        "estimated_profit_rub": 120,
        "estimated_roi_percent": 18,
        "recommendation_score": 75,
        "created_at": created_at,
    }


def telegram_settings(batch_interval_seconds=60, max_alerts_per_message=5):
    settings = ParserSettings(telegram_alerts_enabled=True, telegram_min_alert_level="good")
    alert_settings = settings.telegram_alert_settings
    alert_settings.batch_alerts = True
    alert_settings.batch_interval_seconds = batch_interval_seconds
    alert_settings.max_alerts_per_message = max_alerts_per_message
    settings.set_telegram_alert_settings(alert_settings)
    return settings


def test_telegram_batch_waits_until_interval(monkeypatch):
    sent_messages = []

    def fake_send(text):
        sent_messages.append(text)
        return True, ""

    repo = FakeAlertRepo([candidate("cand_1", utc_now_iso())])
    monkeypatch.setattr(notifier_module, "telegram_is_configured", lambda: True)
    monkeypatch.setattr(notifier_module, "send_telegram_message", fake_send)

    sent = TelegramNotifier(repo).send_pending_alerts(
        telegram_settings(batch_interval_seconds=3600),
    )

    assert sent == 0
    assert sent_messages == []
    assert repo.logged == []


def test_telegram_batch_sends_after_interval(monkeypatch):
    sent_messages = []

    def fake_send(text):
        sent_messages.append(text)
        return True, ""

    created_at = (utc_now() - timedelta(seconds=61)).replace(microsecond=0).isoformat()
    repo = FakeAlertRepo([candidate("cand_1", created_at)])
    monkeypatch.setattr(notifier_module, "telegram_is_configured", lambda: True)
    monkeypatch.setattr(notifier_module, "send_telegram_message", fake_send)

    sent = TelegramNotifier(repo).send_pending_alerts(
        telegram_settings(batch_interval_seconds=60),
    )

    assert sent == 1
    assert len(sent_messages) == 1
    assert repo.logged == [("cand_1", "sent", "")]


def test_telegram_batch_sends_when_max_alerts_reached(monkeypatch):
    sent_messages = []

    def fake_send(text):
        sent_messages.append(text)
        return True, ""

    repo = FakeAlertRepo(
        [
            candidate("cand_1", utc_now_iso()),
            candidate("cand_2", utc_now_iso()),
        ]
    )
    monkeypatch.setattr(notifier_module, "telegram_is_configured", lambda: True)
    monkeypatch.setattr(notifier_module, "send_telegram_message", fake_send)

    sent = TelegramNotifier(repo).send_pending_alerts(
        telegram_settings(batch_interval_seconds=3600, max_alerts_per_message=2),
    )

    assert sent == 2
    assert len(sent_messages) == 1
    assert repo.logged == [("cand_1", "sent", ""), ("cand_2", "sent", "")]
