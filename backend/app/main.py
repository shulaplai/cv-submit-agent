"""cv-submit-agent FastAPI application entrypoint."""
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .config import settings
from .db import init_db
from .routers import browser, jobs, profile, scan, stats

import os as _os

_LOGS_DIR = Path(_os.environ.get("CVSUBMIT_LOG_DIR")
                 or (Path(__file__).resolve().parents[2] / "logs"))
_LOGS_DIR.mkdir(parents=True, exist_ok=True)
_handlers: list[logging.Handler] = [logging.StreamHandler()]
try:
    # 檔案 log 有 rotate（max 5MB x 5）—— 以前只寫 /tmp，重啟就被覆蓋、亦冇得查。
    from logging.handlers import RotatingFileHandler

    _handlers.append(RotatingFileHandler(
        _LOGS_DIR / "cvsubmit.log", maxBytes=5 * 1024 * 1024, backupCount=5,
        encoding="utf-8"))
except Exception:  # noqa: BLE001 — 寫唔到檔都照用 console
    pass
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                    handlers=_handlers)
log = logging.getLogger(__name__)

_scheduler: AsyncIOScheduler | None = None


async def _scheduled_scan():
    from .routers.scan import _scan_job
    await _scan_job()


def _start_catchup_if_stale() -> None:
    """開機／喚醒時如果好耐冇掃過，即刻補一次（唔好白等下一晚）。"""
    from .routers.scan import last_scan_age_hours, start_catchup_scan

    age = last_scan_age_hours()
    hours = settings.SCAN_CATCHUP_HOURS
    if age is None:
        log.info("catch-up: 未見過掃描紀錄，會等下一次排程（唔會開機即掃）")
        return
    if age < hours:
        return
    log.warning("catch-up: 上次掃描已經 %.1f 小時前（> %s 小時），即刻補掃一次", age, hours)
    start_catchup_scan()


def build_scheduler() -> "AsyncIOScheduler | None":
    """建立掃描排程（None = 停用）。

    ⚠ APScheduler 預設 misfire_grace_time = 1 秒：部 Mac 睡醒／時鐘差少少就會
    當「錯過」直接跳過。實測 2026-09-24、09-26 兩晚就係被 1.12 秒 grace 跳過，
    而且冇 catch-up -> 「每兩晚 03:00 scan」實際上冇跑過。呢度放寬到
    SCAN_MISFIRE_GRACE_SECONDS。
    """
    if settings.SCAN_DAY_INTERVAL <= 0:
        return None
    sched = AsyncIOScheduler(job_defaults={
        "misfire_grace_time": settings.SCAN_MISFIRE_GRACE_SECONDS,
        "coalesce": True,
        "max_instances": 1,
    })
    sched.add_job(
        _scheduled_scan,
        # 雙日錨定（2-31/2）而唔係 */2（單日）：確保「下次」就係今晚凌晨，
        # 之後每兩日凌晨 SCAN_HOUR 照跑（*/2 會喺單數日，跳過今晚）。
        CronTrigger(day=f"2-31/{settings.SCAN_DAY_INTERVAL}", hour=settings.SCAN_HOUR),
        id="scan_jobs",
        misfire_grace_time=settings.SCAN_MISFIRE_GRACE_SECONDS,
        coalesce=True,
        max_instances=1,
    )
    return sched


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    global _scheduler
    _scheduler = build_scheduler()
    if _scheduler is not None:
        _scheduler.start()
        next_run = _scheduler.get_job("scan_jobs").next_run_time
        log.info("scheduled scan: every %s days at %02d:00 (next: %s, grace: %ss)",
                 settings.SCAN_DAY_INTERVAL, settings.SCAN_HOUR, next_run,
                 settings.SCAN_MISFIRE_GRACE_SECONDS)
        _start_catchup_if_stale()
    yield
    if _scheduler:
        _scheduler.shutdown(wait=False)
    from .services.scraper_base import close_all_browsers
    await close_all_browsers()


app = FastAPI(title="CV Submit Agent", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(profile.router)
app.include_router(jobs.router)
app.include_router(scan.router)
app.include_router(stats.router)
app.include_router(browser.router)


@app.get("/api/health")
def health():
    return {"ok": True}


_static = Path(__file__).resolve().parent.parent / "static"
if _static.exists():
    app.mount("/", StaticFiles(directory=str(_static), html=True), name="static")
