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
    description: str = ""
    posted_at: datetime | None = None

    @property
    def key(self) -> str:
        return f"{self.source}:{self.id}"

    @property
    def fingerprint(self) -> str:
        """Identifies the same posting when it appears on several boards.

        Location is included because the same team often posts the same title
        in several cities; those are distinct opportunities. Remote spellings
        are normalised so 'Remote' and 'Worldwide' collapse.
        """
        def norm(value: str) -> str:
            return re.sub(r"[^a-z0-9]+", " ", (value or "").lower()).strip()

        location = norm(self.location)
        if re.search(r"\b(?:remote|worldwide|anywhere|global)\b", self.location or "", re.I):
            location = "remote"
        return f"fp:{norm(self.company)}|{norm(self.title)}|{location}"
