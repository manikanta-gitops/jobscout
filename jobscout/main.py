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

from .filters import JobFilter
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
        if job.key in batch_keys or batch_fps.get(job.fingerprint, job.source) != job.source:
            continue
        if store.has(job) or not flt.accepts(job, now):
            continue
        batch_keys.add(job.key)
        batch_fps[job.fingerprint] = job.source
        fresh.append(job)
    log.info("%d new matching jobs", len(fresh))

    first_run = store.is_empty
    limit = cfg.get("first_run_limit", 10) if first_run else cfg.get("max_per_run", 30)
    to_send = fresh[:limit]

    if dry_run:
        for job in to_send:
            print(f"[{job.source}] {job.title} | {job.company} | {job.location} | {job.url}")
        return

    if first_run:  # baseline: do not replay the whole backlog later
        for job in fresh[limit:]:
            store.add(job, now)

    sent = 0
    try:
        for job in to_send:
            try:
                tg.send(job)
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
    log.info("sent %d of %d new jobs", sent, len(fresh))


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
