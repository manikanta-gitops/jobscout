from __future__ import annotations

import re
from datetime import datetime, timedelta

from .models import Job


def _compile(words: list[str]) -> re.Pattern[str] | None:
    if not words:
        return None
    parts = (rf"(?<![a-z0-9]){re.escape(w.lower())}(?![a-z0-9])" for w in words)
    return re.compile("|".join(parts), re.IGNORECASE)


class JobFilter:
    def __init__(self, cfg: dict):
        self.include = _compile(cfg.get("include") or [])
        self.exclude = _compile(cfg.get("exclude_title") or [])
        self.locations = [t.lower() for t in cfg.get("locations") or []]
        self.max_age = timedelta(days=cfg["max_age_days"]) if cfg.get("max_age_days") else None

    def accepts(self, job: Job, now: datetime) -> bool:
        if self.include and not self.include.search(f"{job.title} {' '.join(job.tags)}"):
            return False
        if self.exclude and self.exclude.search(job.title):
            return False
        if self.locations and job.location:
            loc = job.location.lower()
            if not any(t in loc for t in self.locations):
                return False
        if self.max_age and job.posted_at and now - job.posted_at > self.max_age:
            return False
        return True
