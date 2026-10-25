from __future__ import annotations

import html
import time

import requests

from .models import Job


class TelegramError(RuntimeError):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status

    @property
    def is_rejection(self) -> bool:
        """Telegram refused this specific message (e.g. malformed); retrying it will never work."""
        return self.status == 400


def format_job(job: Job) -> str:
    esc = html.escape
    lines = [f"<b>{esc(job.title)}</b>"]
    meta = " | ".join(x for x in (esc(job.company), esc(job.location)) if x)
    if meta:
        lines.append(meta)
    if job.salary:
        lines.append(f"Salary: {esc(job.salary)}")
    if job.posted_at:
        lines.append(f"Posted: {job.posted_at:%Y-%m-%d}")
    lines.append(f'{esc(job.source)} - <a href="{esc(job.url, quote=True)}">Apply</a>')
    return "\n".join(lines)


class Telegram:
    def __init__(self, token: str, chat_id: str, session: requests.Session):
        self.token = token
        self.chat_id = chat_id
        self.session = session

    @property
    def enabled(self) -> bool:
        return bool(self.token and self.chat_id)

    def send_text(self, text: str) -> None:
        try:
            r = self.session.post(
                f"https://api.telegram.org/bot{self.token}/sendMessage",
                json={
                    "chat_id": self.chat_id,
                    "text": text,
                    "parse_mode": "HTML",
                    "disable_web_page_preview": True,
                },
                timeout=20,
            )
        except requests.RequestException:
            # The original exception text embeds the bot token in the URL.
            raise TelegramError("Telegram request failed") from None
        if not r.ok:
            raise TelegramError(f"Telegram HTTP {r.status_code}: {r.text[:200]}", r.status_code)
        time.sleep(1.1)  # Telegram allows ~1 message/second per chat

    def send(self, job: Job) -> None:
        self.send_text(format_job(job))
