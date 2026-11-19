"""Fetches job descriptions only when the source does not ship them inline.

Failures are swallowed and leave job.description empty; matching then scores
the listing on its title/tags and caps confidence instead of inventing facts.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from .models import Job
from .sources import _text

log = logging.getLogger("jobscout")


def greenhouse(session, job: Job) -> str:
    slug, _, jid = job.id.partition("-")
    data = session.get(
        f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs/{jid}", timeout=20
    ).json()
    # The Job Board API returns HTML-escaped content, not base64.
    return _text(data.get("content") or "", keep_newlines=True)


def smartrecruiters(session, job: Job) -> str:
    slug, _, pid = job.id.partition("-")
    data = session.get(
        f"https://api.smartrecruiters.com/v1/companies/{slug}/postings/{pid}", timeout=20
    ).json()
    ad = data.get("jobAd") or {}
    if isinstance(ad.get("sections"), list):
        parts = [
            f"{s.get('title', '')}: {_text(s.get('text', ''), keep_newlines=True)}"
            for s in ad["sections"]
            if isinstance(s, dict) and s.get("text")
        ]
        return "\n".join(parts).strip()
    return _text(ad.get("content") or "", keep_newlines=True)


def lever(session, job: Job) -> str:
    """Fetch full posting from Lever API."""
    slug, _, jid = job.id.partition("-")
    data = session.get(
        f"https://api.lever.co/v0/postings/{slug}/{jid}", timeout=20
    ).json()
    return _text(data.get("descriptionPlain") or data.get("description") or "", keep_newlines=True)


def ashby(session, job: Job) -> str:
    """Fetch full posting from Ashby API."""
    slug, _, jid = job.id.partition("-")
    data = session.get(
        f"https://api.ashbyhq.com/posting-api/job-board/{slug}/job/{jid}", timeout=20
    ).json()
    return _text(data.get("descriptionHtml") or data.get("description") or data.get("descriptionPlain") or "", keep_newlines=True)


def adzuna(session, job: Job) -> str:
    """Adzuna API returns description in the search results; this is a no-op placeholder.
    If a dedicated detail endpoint becomes available, implement here.
    """
    return ""


DETAIL: dict[str, Callable] = {
    "greenhouse": greenhouse,
    "smartrecruiters": smartrecruiters,
    "lever": lever,
    "ashby": ashby,
    "adzuna": adzuna,
}


def ensure_description(job: Job, session) -> None:
    """Best-effort: on any failure the job is left with no description."""
    if (job.description or "").strip() or job.source not in DETAIL:
        return
    try:
        job.description = DETAIL[job.source](session, job)[:20000]
    except Exception as e:  # network/HTTP/parse problems must never stop a run
        log.debug("description fetch failed for %s (%s)", job.key, type(e).__name__)
