from __future__ import annotations

import re
from datetime import datetime, timedelta

from .models import Job
from .taxonomy import classify_title


def _compile(words: list[str]) -> re.Pattern[str] | None:
    if not words:
        return None
    parts = (rf"(?<![a-z0-9]){re.escape(w.lower())}(?![a-z0-9])" for w in words)
    return re.compile("|".join(parts), re.IGNORECASE)


# Structured location patterns (word-boundary) aligned with matching.py
INDIA_LOC = re.compile(
    r"\bindia\b|hyderabad|bangalore|bengaluru|chennai|pune|mumbai|delhi|noida"
    r"|gurgaon|gurugram|kolkata|ahmedabad|indore|coimbatore|kochi",
    re.IGNORECASE,
)
UNVERIFIED_LOC = re.compile(
    r"\bremote\b|remotely|anywhere|worldwide|global|\bapac\b|\basia\b|distributed",
    re.IGNORECASE,
)

_INDIA_ALIASES = {
    "india", "bangalore", "bengaluru", "hyderabad", "chennai", "pune", "mumbai",
    "delhi", "noida", "gurgaon", "gurugram", "kolkata", "ahmedabad", "indore",
    "coimbatore", "kochi",
}
_UNVERIFIED_ALIASES = {
    "unverified", "remote", "remotely", "anywhere", "worldwide", "global",
    "apac", "asia", "distributed",
}
_OUTSIDE_ALIASES = {"outside", "outside_india", "non-india", "international"}


def _location_status(raw: str) -> str:
    """Returns 'india', 'unverified', or 'outside_india'."""
    loc = re.sub(r"\s+", " ", (raw or "").strip())
    if not loc:
        return "unverified"
    if INDIA_LOC.search(loc):
        return "india"
    if UNVERIFIED_LOC.search(loc):
        return "unverified"
    return "outside_india"


def _normalized_modes(values: list[str]) -> set[str]:
    """Map legacy city/region aliases to the three real location statuses."""
    modes: set[str] = set()
    for value in values:
        token = (value or "").strip().lower()
        if token in _INDIA_ALIASES:
            modes.add("india")
        elif token in _UNVERIFIED_ALIASES:
            modes.add("unverified")
        elif token in _OUTSIDE_ALIASES:
            modes.add("outside_india")
    return modes


class JobFilter:
    def __init__(self, cfg: dict):
        self.include = _compile(cfg.get("include") or [])
        self.exclude = _compile(cfg.get("exclude_title") or [])
        self.location_modes = _normalized_modes(cfg.get("locations") or [])
        self.max_age = timedelta(days=cfg["max_age_days"]) if cfg.get("max_age_days") else None

    def accepts(self, job: Job, now: datetime) -> bool:
        info = classify_title(job.title)
        if info.rejection:
            return False

        # The title classifier is the primary relevance gate.  `include` is
        # kept as an optional narrowing list for titles the classifier accepts.
        if self.include and not self.include.search(job.title) and not info.role_family:
            return False
        if self.exclude and self.exclude.search(job.title):
            return False
        if self.location_modes and job.location is not None:
            loc_status = _location_status(job.location)
            if loc_status not in self.location_modes:
                return False
        if self.max_age and job.posted_at and now - job.posted_at > self.max_age:
            return False
        return True
