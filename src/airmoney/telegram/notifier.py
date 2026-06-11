from __future__ import annotations

import os
import urllib.parse
import urllib.request
from pathlib import Path

from airmoney.config.models import ParserSettings
from airmoney.recommendation.scoring import should_alert
from airmoney.storage.repositories import Repository
from airmoney.telegram.templates import batch_alert, candidate_alert, should_send_immediate


def load_dotenv(path: str | Path = ".env") -> None:
    env_path = Path(path)
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def telegram_is_configured() -> bool:
    load_dotenv()
    return bool(os.getenv("TELEGRAM_BOT_TOKEN") and os.getenv("TELEGRAM_CHAT_ID"))


def send_telegram_message(text: str) -> tuple[bool, str]:
    load_dotenv()
    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        return False, "Telegram token/chat_id не настроены"

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "disable_web_page_preview": "true",
    }
    data = urllib.parse.urlencode(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            response_text = response.read().decode("utf-8", errors="replace")
        if '"ok":true' in response_text:
            return True, ""
        return False, response_text
    except Exception as error:
        return False, str(error)


class TelegramNotifier:
    def __init__(self, repo: Repository | None = None, site_url: str | None = None):
        self.repo = repo or Repository()
        load_dotenv()
        self.site_url = (site_url or os.getenv("AIRMONEY_SITE_URL") or "http://127.0.0.1:8000").rstrip("/")

    def send_pending_alerts(self, settings: ParserSettings, limit: int = 20) -> int:
        if not settings.telegram_alerts_enabled or not telegram_is_configured():
            return 0
        sent = 0
        rates = self.repo.latest_currency_rate()
        alert_settings = settings.telegram_alert_settings
        batch_candidates: list[dict] = []
        for candidate in self.repo.list_unsent_alert_candidates(limit=limit):
            if not should_alert(candidate["recommendation_level"], settings.telegram_min_alert_level):
                continue
            if rates:
                candidate["currency_rate_source"] = candidate.get("currency_source") or rates["source"]
                candidate["currency_fetched_at"] = candidate.get("currency_fetched_at") or rates["fetched_at"]
            if alert_settings.batch_alerts and not should_send_immediate(candidate):
                batch_candidates.append(candidate)
                continue
            ok, error = send_telegram_message(
                candidate_alert(
                    candidate,
                    f"{self.site_url}/candidates",
                    include_link=alert_settings.include_link,
                    include_pattern=alert_settings.include_pattern,
                    include_sample_stats=alert_settings.include_sample_stats,
                    include_reasons=alert_settings.include_reasons,
                )[: alert_settings.max_message_length]
            )
            self.repo.log_telegram_alert(candidate["id"], "sent" if ok else "error", error)
            if ok:
                sent += 1
        if batch_candidates:
            limited = batch_candidates[: alert_settings.max_alerts_per_message]
            ok, error = send_telegram_message(
                batch_alert(limited, f"{self.site_url}/candidates")[: alert_settings.max_message_length]
            )
            for candidate in limited:
                self.repo.log_telegram_alert(candidate["id"], "sent" if ok else "error", error)
            if ok:
                sent += len(limited)
        return sent
