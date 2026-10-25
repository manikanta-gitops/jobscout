from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class Job:
    source: str
    id: str
    title: str
    company: str
    url: str
    location: str = ""
    salary: str = ""
    tags: list[str] = field(default_factory=list)
    posted_at: datetime | None = None

    @property
    def key(self) -> str:
        return f"{self.source}:{self.id}"

    @property
    def fingerprint(self) -> str:
        """Identifies the same posting when it appears on several boards."""
        norm = lambda s: re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()
        return f"fp:{norm(self.company)}|{norm(self.title)}"
