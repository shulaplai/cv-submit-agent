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

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger(__name__)

_scheduler: AsyncIOScheduler | None = None


async def _scheduled_scan():
    from .routers.scan import _scan_job
    await _scan_job()


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    global _scheduler
    if settings.SCAN_DAY_INTERVAL > 0:
        _scheduler = AsyncIOScheduler()
        _scheduler.add_job(
            _scheduled_scan,
            CronTrigger(day=f"*/{settings.SCAN_DAY_INTERVAL}", hour=settings.SCAN_HOUR),
            id="scan_jobs",
            coalesce=True,
            max_instances=1,
        )
        _scheduler.start()
        next_run = _scheduler.get_job("scan_jobs").next_run_time
        log.info("scheduled scan: every %s days at %02d:00 (next: %s)",
                 settings.SCAN_DAY_INTERVAL, settings.SCAN_HOUR, next_run)
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
