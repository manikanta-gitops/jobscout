from __future__ import annotations

import html
import logging
import os
import re
import xml.etree.ElementTree as ET
from collections.abc import Callable, Iterator
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .models import Job

Fetcher = Callable[[dict, requests.Session], Iterator[Job]]
log = logging.getLogger("jobscout")


def make_session() -> requests.Session:
    s = requests.Session()
    s.headers["User-Agent"] = "jobscout/1.0 (personal job alert bot)"
    retry = Retry(total=3, backoff_factor=2, status_forcelist=(429, 500, 502, 503, 504), allowed_methods=("GET", "POST"))
    s.mount("https://", HTTPAdapter(max_retries=retry))
    return s


def _get(s: requests.Session, url: str, **params: Any) -> requests.Response:
    r = s.get(url, params=params or None, timeout=20)
    r.raise_for_status()
    return r


def _dt(value: Any) -> datetime | None:
    """Parse ISO strings, RFC 822 strings, unix seconds or milliseconds into aware UTC."""
    if not value:
        return None
    try:
        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(value / 1000 if value > 1e11 else value, timezone.utc)
        try:
            dt = datetime.fromisoformat(str(value))
        except ValueError:
            dt = parsedate_to_datetime(str(value))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError, OverflowError):
        return None


def _salary(lo: Any, hi: Any, currency: str = "") -> str:
    lo, hi = lo or 0, hi or 0
    if not lo and not hi:
        return ""
    amount = f"{lo:,.0f} - {hi:,.0f}" if lo and hi else f"{(lo or hi):,.0f}"
    return f"{currency} {amount}".strip()


def _text(markup: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", markup or ""))).strip()


def remoteok(cfg: dict, s: requests.Session) -> Iterator[Job]:
    for j in _get(s, "https://remoteok.com/api").json():
        if not isinstance(j, dict) or "position" not in j:
            continue  # first element is a legal notice
        yield Job(
            source="remoteok",
            id=str(j["id"]),
            title=j["position"],
            company=j.get("company", ""),
            url=j.get("url") or f"https://remoteok.com/remote-jobs/{j['id']}",
            location=j.get("location", ""),
            salary=_salary(j.get("salary_min"), j.get("salary_max"), "USD"),
            tags=j.get("tags") or [],
            posted_at=_dt(j.get("date")),
        )


def weworkremotely(cfg: dict, s: requests.Session) -> Iterator[Job]:
    for feed in cfg.get("feeds", []):
        root = ET.fromstring(_get(s, feed).content)
        for item in root.iterfind("./channel/item"):
            title = item.findtext("title", "")
            company, _, role = title.partition(": ")
            link = item.findtext("link", "")
            yield Job(
                source="weworkremotely",
                id=item.findtext("guid") or link,
                title=role or title,
                company=company if role else "",
                url=link,
                location=item.findtext("region", ""),
                posted_at=_dt(item.findtext("pubDate")),
            )


def remotive(cfg: dict, s: requests.Session) -> Iterator[Job]:
    for category in cfg.get("categories", ["devops"]):
        for j in _get(s, "https://remotive.com/api/remote-jobs", category=category).json().get("jobs", []):
            yield Job(
                source="remotive",
                id=str(j["id"]),
                title=j["title"],
                company=j.get("company_name", ""),
                url=j["url"],
                location=j.get("candidate_required_location", ""),
                salary=j.get("salary", ""),
                tags=j.get("tags") or [],
                posted_at=_dt(j.get("publication_date")),
            )


def jobicy(cfg: dict, s: requests.Session) -> Iterator[Job]:
    for tag in cfg.get("tags", ["devops"]):
        for j in _get(s, "https://jobicy.com/api/jobs", count=100, tag=tag).json().get("jobs", []):
            yield Job(
                source="jobicy",
                id=str(j["id"]),
                title=_text(j["jobTitle"]),
                company=_text(j.get("companyName", "")),
                url=j["url"],
                location=j.get("jobGeo", ""),
                salary=_salary(j.get("salaryMin"), j.get("salaryMax"), j.get("salaryCurrency", "")),
                tags=j.get("jobIndustry") or [],
                posted_at=_dt(j.get("pubDate")),
            )


def himalayas(cfg: dict, s: requests.Session) -> Iterator[Job]:
    for q in cfg.get("queries", ["devops"]):
        for j in _get(s, "https://himalayas.app/jobs/api/search", q=q, sort="recent").json().get("jobs", []):
            places = [r if isinstance(r, str) else r.get("name", "") for r in j.get("locationRestrictions") or []]
            yield Job(
                source="himalayas",
                id=j["guid"],
                title=j["title"],
                company=j.get("companyName", ""),
                url=j["applicationLink"],
                location=", ".join(places) or "Worldwide",
                salary=_salary(j.get("minSalary"), j.get("maxSalary"), j.get("currency", "")),
                tags=j.get("categories") or [],
                posted_at=_dt(j.get("pubDate")),
            )


def arbeitnow(cfg: dict, s: requests.Session) -> Iterator[Job]:
    for page in range(1, cfg.get("pages", 1) + 1):
        for j in _get(s, "https://www.arbeitnow.com/api/job-board-api", page=page).json().get("data", []):
            yield Job(
                source="arbeitnow",
                id=j["slug"],
                title=j["title"],
                company=j.get("company_name", ""),
                url=j["url"],
                location=j.get("location", ""),
                tags=j.get("tags") or [],
                posted_at=_dt(j.get("created_at")),
            )


_CURRENCY = {"in": "INR", "gb": "GBP", "us": "USD", "ca": "CAD", "au": "AUD", "de": "EUR"}


def adzuna(cfg: dict, s: requests.Session) -> Iterator[Job]:
    app_id, app_key = os.getenv("ADZUNA_APP_ID"), os.getenv("ADZUNA_APP_KEY")
    if not (app_id and app_key):
        log.warning("adzuna: ADZUNA_APP_ID / ADZUNA_APP_KEY not set; skipping")
        return
    for country in cfg.get("countries", ["in"]):
        for q in cfg.get("queries", ["devops engineer"]):
            data = _get(
                s,
                f"https://api.adzuna.com/v1/api/jobs/{country}/search/1",
                app_id=app_id,
                app_key=app_key,
                what=q,
                results_per_page=50,
                max_days_old=cfg.get("max_days_old", 3),
                sort_by="date",
            ).json()
            for j in data.get("results", []):
                yield Job(
                    source="adzuna",
                    id=str(j["id"]),
                    title=_text(j["title"]),
                    company=(j.get("company") or {}).get("display_name", ""),
                    url=j["redirect_url"],
                    location=(j.get("location") or {}).get("display_name", ""),
                    salary=_salary(j.get("salary_min"), j.get("salary_max"), _CURRENCY.get(country, "")),
                    posted_at=_dt(j.get("created")),
                )


def _per_company(cfg: dict, s: requests.Session, fetch: Callable[[str], Iterator[Job]]) -> Iterator[Job]:
    for slug in cfg.get("companies", []):
        try:
            yield from fetch(slug)
        except requests.RequestException as e:
            status = getattr(e.response, "status_code", "error")
            log.warning("skipped company '%s' (%s)", slug, status)


def greenhouse(cfg: dict, s: requests.Session) -> Iterator[Job]:
    def fetch(slug: str) -> Iterator[Job]:
        for j in _get(s, f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs").json().get("jobs", []):
            yield Job(
                source="greenhouse",
                id=f"{slug}-{j['id']}",
                title=j["title"],
                company=slug,
                url=j["absolute_url"],
                location=(j.get("location") or {}).get("name", ""),
                posted_at=_dt(j.get("first_published") or j.get("updated_at")),
            )

    return _per_company(cfg, s, fetch)


def lever(cfg: dict, s: requests.Session) -> Iterator[Job]:
    def fetch(slug: str) -> Iterator[Job]:
        for j in _get(s, f"https://api.lever.co/v0/postings/{slug}", mode="json").json():
            cats = j.get("categories") or {}
            yield Job(
                source="lever",
                id=f"{slug}-{j['id']}",
                title=j["text"],
                company=slug,
                url=j["hostedUrl"],
                location=cats.get("location", ""),
                tags=[t for t in (cats.get("team"), cats.get("department")) if t],
                posted_at=_dt(j.get("createdAt")),
            )

    return _per_company(cfg, s, fetch)


def ashby(cfg: dict, s: requests.Session) -> Iterator[Job]:
    def fetch(slug: str) -> Iterator[Job]:
        for j in _get(s, f"https://api.ashbyhq.com/posting-api/job-board/{slug}").json().get("jobs", []):
            yield Job(
                source="ashby",
                id=f"{slug}-{j['id']}",
                title=j["title"],
                company=slug,
                url=j["jobUrl"],
                location=j.get("location", ""),
                posted_at=_dt(j.get("publishedAt")),
            )

    return _per_company(cfg, s, fetch)


def smartrecruiters(cfg: dict, s: requests.Session) -> Iterator[Job]:
    def fetch(slug: str) -> Iterator[Job]:
        for j in _get(s, f"https://api.smartrecruiters.com/v1/companies/{slug}/postings", limit=100).json().get("content", []):
            level = (j.get("experienceLevel") or {}).get("label", "")
            job_type = (j.get("typeOfEmployment") or {}).get("label", "")
            yield Job(
                source="smartrecruiters",
                id=f"{slug}-{j['id']}",
                title=_text(j["name"]),
                company=(j.get("company") or {}).get("name", slug),
                url=f"https://jobs.smartrecruiters.com/{slug}/{j['id']}",
                location=(j.get("location") or {}).get("fullLocation", ""),
                tags=[t for t in (level, job_type, (j.get("department") or {}).get("label", "")) if t],
                posted_at=_dt(j.get("releasedDate")),
            )

    return _per_company(cfg, s, fetch)


def jooble(cfg: dict, s: requests.Session) -> Iterator[Job]:
    key = os.getenv("JOOBLE_API_KEY", "")
    if not key:
        log.warning("jooble: JOOBLE_API_KEY not set; skipping")
        return
    for q in cfg.get("queries", ["devops engineer"]):
        data = s.post(
            f"https://jooble.org/api/{key}",
            json={"keywords": q, "location": cfg.get("location", "India"), "page": 1},
            headers={"Content-Type": "application/json"},
            timeout=25,
        ).json()
        for j in data.get("jobs", []):
            yield Job(
                source="jooble",
                id=str(j.get("id", "")),
                title=_text(j.get("title", "")),
                company=j.get("company", ""),
                url=j.get("link", ""),
                location=j.get("location", ""),
                salary=j.get("salary", ""),
                tags=[q],
                posted_at=_dt(j.get("updated")),
            )


SOURCES: dict[str, Fetcher] = {
    "remoteok": remoteok,
    "weworkremotely": weworkremotely,
    "remotive": remotive,
    "jobicy": jobicy,
    "himalayas": himalayas,
    "arbeitnow": arbeitnow,
    "adzuna": adzuna,
    "greenhouse": greenhouse,
    "lever": lever,
    "ashby": ashby,
    "smartrecruiters": smartrecruiters,
    "jooble": jooble,
}
