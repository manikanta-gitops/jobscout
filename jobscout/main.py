from __future__ import annotations

import argparse
import logging
import os
import sys
import time
import tomllib
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv

from .details import ensure_description
from .filters import JobFilter
from .matching import evaluate, quick_reject
from .models import Job
from .notify import Telegram, TelegramError
from .sources import SOURCES, make_session
from .store import Store

log = logging.getLogger("jobscout")
ROOT = Path(__file__).resolve().parent.parent  # paths are relative to the project, not the current directory
EPOCH = datetime.fromtimestamp(0, timezone.utc)
ALERT_AFTER_FAILURES = 6


def _describe(e: Exception) -> str:
    """Request errors embed the full URL (which may hold API keys), so log only the status."""
    if isinstance(e, requests.RequestException):
        status = getattr(e.response, "status_code", None)
        return f"{type(e).__name__}" + (f" (HTTP {status})" if status else "")
    return str(e)


def _fetch(name: str, scfg: dict):
    return list(SOURCES[name](scfg, make_session()))


def run_once(cfg: dict, store: Store, tg: Telegram, dry_run: bool) -> None:
    now = datetime.now(timezone.utc)
    flt = JobFilter(cfg.get("filters") or {})
    match_cfg = cfg.get("matching") or {}

    due = []
    for name, scfg in (cfg.get("sources") or {}).items():
        scfg = scfg or {}
        if not scfg.get("enabled", True):
            continue
        if name not in SOURCES:
            log.warning("unknown source '%s'", name)
        elif store.is_due(name, scfg.get("interval_minutes", 30), now):
            due.append((name, scfg))

    collected, alerts = [], []
    with ThreadPoolExecutor(max_workers=5) as pool:
        futures = {pool.submit(_fetch, name, scfg): name for name, scfg in due}
        for fut in as_completed(futures):
            name = futures[fut]
            try:
                jobs = fut.result()
            except Exception as e:  # one broken source must not stop the others
                fails = store.mark_run(name, now, ok=False)
                log.error("%s failed (%d in a row): %s", name, fails, _describe(e))
                if fails == ALERT_AFTER_FAILURES:
                    alerts.append(f"jobscout: source '{name}' has failed {fails} runs in a row: {_describe(e)}")
                continue
            store.mark_run(name, now, ok=True)
            log.info("%s: fetched %d", name, len(jobs))
            collected.extend(jobs)

    fresh, batch_keys, batch_fps = [], set(), {}
    for job in sorted(collected, key=lambda j: j.posted_at or EPOCH, reverse=True):
        if job.key in batch_keys or job.fingerprint in batch_fps:
            continue
        if store.has(job) or not flt.accepts(job, now):
            continue
        reason = quick_reject(job)
        if reason:
            log.info("job %s pre-rejected: %s", job.key, reason)
            continue
        batch_keys.add(job.key)
        batch_fps[job.fingerprint] = job.source
        fresh.append(job)
    log.info("%d new matching jobs after pre-filter", len(fresh))

    # Fetch missing descriptions for sources that support it
    session = make_session()
    for job in fresh:
        ensure_description(job, session)

    # Score jobs against profile
    scored = []
    for job in fresh:
        result = evaluate(job, match_cfg)
        if result.verdict == "send":
            scored.append((job, result))
        else:
            log.info("job %s: %s (score=%d, conf=%s, loc=%s) - %s",
                     job.key, result.verdict, result.score, result.confidence,
                     result.location_status, "; ".join(result.reasons))

    # Rank by relevance first, then by recency.
    scored.sort(key=lambda item: (item[1].score, item[0].posted_at or EPOCH), reverse=True)

    first_run = store.is_empty
    limit = cfg.get("first_run_limit", 10) if first_run else cfg.get("max_per_run", 30)
    to_send = scored[:limit]

    if dry_run:
        for job, result in to_send:
            print(f"[{job.source}] {job.title} | {job.company} | {job.location} | {job.url}")
            print(f"  score={result.score} confidence={result.confidence} loc={result.location_status}")
            print(f"  matched: {result.matched_must} | missing: {result.missing_must}")
            print(f"  reasons: {'; '.join(result.reasons)}")
        return

    if first_run:  # baseline: do not replay the whole backlog later
        for job, _ in scored[limit:]:
            store.add(job, now)

    sent = 0
    try:
        for job, result in to_send:
            try:
                # Include match details in notification
                msg = format_job_with_match(job, result)
                tg.send_text(msg)
                sent += 1
            except TelegramError as e:
                if not e.is_rejection:
                    raise
                log.warning("Telegram rejected %s, skipping it: %s", job.key, e)
            store.add(job, now)
        for text in alerts:
            tg.send_text(text)
    except TelegramError as e:
        log.error("sending stopped, will retry next run: %s", e)
    finally:
        store.save(now)
    log.info("sent %d of %d matched jobs", sent, len(scored))


def _clip(text: str, limit: int) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[: limit - 3].rstrip() + "..."


def format_job_with_match(job: Job, result) -> str:
    """Format a matched job for Telegram HTML.

    Includes the experience requirement, matched core skills, relevance score,
    a concise reason, location/remote information and the apply link.
    """
    from html import escape

    # Text nodes only need &, < and > escaped; quotes matter in the href below.
    def text(value: str) -> str:
        return escape(value, quote=False)

    lines = [f"<b>{text(job.title)}</b>"]
    if job.company:
        lines.append(text(job.company))

    location = (job.location or "").strip() or "Remote / not specified"
    meta = [f"Location: {text(location)}"]
    if job.posted_at:
        meta.append(f"Posted: {job.posted_at:%Y-%m-%d}")
    lines.append(" | ".join(meta))
    if job.salary:
        lines.append(f"Salary: {text(job.salary)}")

    experience = result.experience_note or "Experience not stated"
    lines.append(
        f"<b>Match:</b> {result.score}/100 ({text(result.confidence)}) | "
        f"<b>Experience:</b> {text(experience)}"
    )
    if result.matched_must:
        lines.append(f"<b>Core skills:</b> {text(', '.join(result.matched_must))}")
    if result.matched_support:
        lines.append(f"<b>Also:</b> {text(', '.join(result.matched_support[:8]))}")

    # Avoid repeating the skill lists in the reason line.
    why = [
        reason
        for reason in result.reasons
        if not reason.startswith(("required core skills:", "supporting skills:", "only preferred/incidental"))
    ]
    if why:
        lines.append(f"<b>Why:</b> {text(_clip('; '.join(why), 500))}")

    lines.append(f'{text(job.source)} - <a href="{escape(job.url, quote=True)}">Apply</a>')
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(prog="jobscout", description="DevOps job alerts to Telegram")
    ap.add_argument("--config", default=str(ROOT / "config.toml"))
    ap.add_argument("--dry-run", action="store_true", help="print matches; send nothing, save nothing")
    ap.add_argument("--loop", type=int, metavar="MINUTES", help="keep running, polling every N minutes")
    ap.add_argument("--test", action="store_true", help="send a Telegram test message and exit")
    args = ap.parse_args()

    # Windows consoles default to a legacy code page that cannot print many job titles.
    for stream in (sys.stdout, sys.stderr):
        if stream is not None:
            stream.reconfigure(encoding="utf-8", errors="replace")
    load_dotenv(ROOT / ".env")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    with open(args.config, "rb") as f:
        cfg = tomllib.load(f)

    tg = Telegram(os.getenv("TELEGRAM_BOT_TOKEN", ""), os.getenv("TELEGRAM_CHAT_ID", ""), make_session())
    if args.test:
        if not tg.enabled:
            log.error("set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID")
            return 1
        try:
            tg.send_text("jobscout is connected.")
        except TelegramError as e:
            log.error("%s", e)
            return 1
        return 0

    dry_run = args.dry_run or not tg.enabled
    if dry_run and not args.dry_run:
        log.warning("Telegram not configured; running as dry run")

    store_path = os.getenv("STATE_PATH") or str(ROOT / "state" / "state.json")
    while True:
        try:
            run_once(cfg, Store(store_path), tg, dry_run)
        except Exception:
            if not args.loop:
                raise
            log.exception("run failed; continuing")
        if not args.loop:
            return 0
        time.sleep(args.loop * 60)
