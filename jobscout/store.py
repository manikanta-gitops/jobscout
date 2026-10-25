from __future__ import annotations

import json
import os
from datetime import datetime, timedelta
from pathlib import Path

from .models import Job

RETENTION = timedelta(days=90)


class Store:
    """Remembers sent jobs, when each source was last polled, and consecutive source failures."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.seen: dict[str, str] = {}
        self.fingerprints: dict[str, list[str]] = {}  # fingerprint -> [source, timestamp]
        self.last_run: dict[str, str] = {}
        self.failures: dict[str, int] = {}
        if self.path.exists():
            data = json.loads(self.path.read_text(encoding="utf-8"))
            self.seen = data.get("seen", {})
            self.fingerprints = data.get("fingerprints", {})
            self.last_run = data.get("last_run", {})
            self.failures = data.get("failures", {})

    @property
    def is_empty(self) -> bool:
        return not self.seen

    def is_due(self, source: str, interval_minutes: int, now: datetime) -> bool:
        last = self.last_run.get(source)
        return last is None or now - datetime.fromisoformat(last) >= timedelta(minutes=interval_minutes)

    def mark_run(self, source: str, now: datetime, ok: bool) -> int:
        """Record a poll (failed polls also wait out the interval). Returns consecutive failures."""
        self.last_run[source] = now.isoformat()
        self.failures[source] = 0 if ok else self.failures.get(source, 0) + 1
        return self.failures[source]

    def has(self, job: Job) -> bool:
        """Same posting seen before, or the same role already sent from a different board."""
        fp = self.fingerprints.get(job.fingerprint)
        return job.key in self.seen or (fp is not None and fp[0] != job.source)

    def add(self, job: Job, now: datetime) -> None:
        self.seen[job.key] = now.isoformat()
        self.fingerprints[job.fingerprint] = [job.source, now.isoformat()]

    def save(self, now: datetime) -> None:
        cutoff = now - RETENTION
        self.seen = {k: v for k, v in self.seen.items() if datetime.fromisoformat(v) >= cutoff}
        self.fingerprints = {k: v for k, v in self.fingerprints.items() if datetime.fromisoformat(v[1]) >= cutoff}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        payload = {
            "seen": self.seen,
            "fingerprints": self.fingerprints,
            "last_run": self.last_run,
            "failures": self.failures,
        }
        tmp.write_text(json.dumps(payload, indent=1), encoding="utf-8")
        os.replace(tmp, self.path)
