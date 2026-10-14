"""Production schedule and publish slots.

kanal.yaml:
  schedule:
    timezone: Europe/Istanbul
    produce: [{days: [mon, thu], time: "09:00"}]     # when a new episode is produced
    publish_times: ["17:00"]                          # YouTube publish slots
    publish_days: [tue, fri]                          # empty = any day
    min_hours_before_publish: 12

`python studio.py tick` does whatever is due now (cron / Task Scheduler call it every 15 minutes);
`python studio.py zamanlayici` keeps running and ticks every minute.
"""
from __future__ import annotations

import datetime as dt
import os
import platform
import sys
import time
from pathlib import Path
from zoneinfo import ZoneInfo

from .util import ROOT, channel_dir, log, output_dir, read_json, t, write_json

DAYS = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6,
        "pzt": 0, "sal": 1, "car": 2, "çar": 2, "per": 3, "cum": 4, "cmt": 5, "paz": 6}
GRACE_HOURS = 12        # a missed slot (computer off) still runs if less than this late


def tz(cfg):
    return ZoneInfo(cfg["schedule"].get("timezone") or "UTC")


def days(lst) -> set[int]:
    return {DAYS[str(d).lower()[:3]] for d in lst or []} or set(range(7))


def hm(s: str) -> tuple[int, int]:
    a, b = str(s).split(":")
    return int(a), int(b)


def state_file() -> Path:
    return channel_dir() / ".zamanlayici.json"


def due_jobs(cfg, now: dt.datetime, state: dict) -> list[tuple[str, dt.datetime]]:
    """Production slots whose time has come and that have not run yet."""
    out = []
    for n, e in enumerate(cfg["schedule"].get("produce", [])):
        H, M = hm(e.get("time", "09:00"))
        for back in range(0, 8):
            d = (now - dt.timedelta(days=back)).date()
            occ = dt.datetime(d.year, d.month, d.day, H, M, tzinfo=now.tzinfo)
            if occ <= now and occ.weekday() in days(e.get("days")):
                key = f"produce{n}"
                last = state.get(key)
                if (not last or dt.datetime.fromisoformat(last) < occ) and now - occ < dt.timedelta(hours=GRACE_HOURS):
                    out.append((key, occ))
                break
    return out


def next_publish(cfg, earliest: dt.datetime, taken: set[str]) -> dt.datetime:
    z = tz(cfg)
    e = earliest.astimezone(z)
    for back in range(0, 60):
        d = (e + dt.timedelta(days=back)).date()
        if d.weekday() not in days(cfg["schedule"].get("publish_days")):
            continue
        for s in sorted(cfg["schedule"].get("publish_times") or ["17:00"]):
            H, M = hm(s)
            slot = dt.datetime(d.year, d.month, d.day, H, M, tzinfo=z)
            if slot >= e and slot.isoformat() not in taken:
                return slot
    return e + dt.timedelta(days=1)


def taken_slots() -> set[str]:
    out = set()
    for p in output_dir().glob("*/durum.json"):
        for u in (read_json(p, {}).get("youtube") or {}).get("uploads", []):
            if u.get("publish_local"):
                out.add(u["publish_local"])
    return out


def publish_time(cfg) -> tuple[str, str]:
    """→ (RFC3339 UTC for the API, local ISO for bookkeeping)"""
    now = dt.datetime.now(tz(cfg))
    slot = next_publish(cfg, now + dt.timedelta(hours=cfg["schedule"].get("min_hours_before_publish", 12)), taken_slots())
    return slot.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), slot.isoformat()


# ---------------------------------------------------------------- tick / loop
class Lock:
    def __init__(self):
        self.p = channel_dir() / ".calisiyor.lock"

    def __enter__(self):
        if self.p.exists() and time.time() - self.p.stat().st_mtime < 6 * 3600:
            raise SystemExit(t("Başka bir üretim sürüyor (kanal/.calisiyor.lock).", "Another run is in progress (kanal/.calisiyor.lock)."))
        self.p.write_text(str(os.getpid()))
        return self

    def __exit__(self, *a):
        self.p.unlink(missing_ok=True)


def tick(cfg) -> list[str]:
    from . import pipeline
    done = []
    with Lock():
        state = read_json(state_file(), {})
        now = dt.datetime.now(tz(cfg))
        for key, occ in due_jobs(cfg, now, state):
            log(t(f"Zamanı geldi: {occ:%a %H:%M} üretimi", f"Due: production slot {occ:%a %H:%M}"))
            state[key] = now.isoformat(); write_json(state_file(), state)
            try:
                done.append(pipeline.produce_next(cfg))
            except SystemExit as e:
                log(str(e))
        done += pipeline.resume_all(cfg)          # approved episodes continue (render, upload …)
    return [d for d in done if d]


def loop(cfg):
    log(t("Zamanlayıcı çalışıyor (durdurmak için Ctrl+C).", "Scheduler running (Ctrl+C to stop)."))
    while True:
        try:
            tick(cfg)
        except SystemExit as e:
            log(str(e))
        except Exception as e:      # keep the loop alive, show the error
            log(f"HATA/ERROR: {e}")
        time.sleep(60)


def install_command() -> tuple[str, list[str] | None]:
    """→ (human readable description, command to run or None)"""
    py, script = sys.executable, str(Path(__file__).resolve().parent.parent / "studio.py")
    output_dir().mkdir(parents=True, exist_ok=True)
    if platform.system() == "Windows":
        cmd = ["schtasks", "/Create", "/F", "/SC", "MINUTE", "/MO", "15", "/TN", "AutoVideoStudio",
               "/TR", f'"{py}" "{script}" tick']
        return " ".join(cmd), cmd
    logf = output_dir() / "zamanlayici.log"
    line = f'*/15 * * * * cd "{ROOT}" && "{py}" "{script}" tick >> "{logf}" 2>&1'
    return t(f"crontab satırı:\n{line}", f"crontab line:\n{line}"), None
