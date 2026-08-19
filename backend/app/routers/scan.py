"""Scan endpoints + in-memory scan state + backfill trigger."""
import asyncio
import logging
from datetime import datetime, timezone

from fastapi import APIRouter
from sqlalchemy.orm import Session

from ..config import settings
from ..db import SessionLocal
from ..services.scanner import ScanSummary, _backfill_candidates, _enrich_one, _fetch_detail_for, run_scan

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/scan", tags=["scan"])

_state: dict = {
    "running": False,
    "last": None,
    "last_error": None,
    "progress": {"platform": "", "phase": "", "count": 0},
}

BACKFILL_LIMIT = 10


async def _scan_job():
    db: Session = SessionLocal()
    try:
        progress = _state["progress"]
        progress.update({"platform": "", "phase": "", "count": 0})
        summary: ScanSummary = await run_scan(db, progress)
        _state["last"] = {
            "at": datetime.now(timezone.utc).isoformat(),
            "scanned": summary.scanned,
            "new_jobs": summary.new_jobs,
            "skipped_duplicates": summary.skipped_duplicates,
            "enriched": summary.enriched,
            "backfilled": summary.backfilled,
            "low_match": summary.low_match,
            "errors": summary.errors,
        }
    except Exception as e:  # noqa: BLE001
        log.exception("scan crashed")
        _state["last_error"] = str(e)
    finally:
        _state["running"] = False
        db.close()


async def _backfill_job():
    """Enrich up to BACKFILL_LIMIT oldest un-enriched rows right now."""
    db: Session = SessionLocal()
    try:
        progress = _state["progress"]
        progress.update({"platform": "backfill", "phase": "running", "count": 0})
        rows = _backfill_candidates(db, BACKFILL_LIMIT)
        done = 0
        for row in rows:
            progress["count"] = done + 1
            await _enrich_one(db, row, row.platform, _fetch_detail_for(row.platform), [])
            done += 1
        db.commit()
        _state["last_backfill"] = {"at": datetime.now(timezone.utc).isoformat(), "processed": done}
        progress.update({"platform": "", "phase": "done", "count": 0})
    except Exception as e:  # noqa: BLE001
        log.exception("backfill crashed")
        _state["last_error"] = f"backfill: {e}"
    finally:
        db.close()


@router.post("")
async def start_scan():
    if _state["running"]:
        return {"started": False, "message": "scan 已經喺度行緊"}
    _state["running"] = True
    _state["last_error"] = None
    asyncio.create_task(_scan_job())
    return {"started": True, "message": "scan 已開始，請稍後查詢狀態"}


@router.post("/backfill")
async def start_backfill():
    if _state["running"]:
        return {"started": False, "message": "scan 已經喺度行緊，等佢完先"}
    _state["running"] = True
    _state["last_error"] = None
    asyncio.create_task(_backfill_job())
    return {"started": True, "message": "補齊已開始（最多 10 份最舊未處理記錄）"}


@router.get("/status")
def scan_status():
    return {
        "running": _state["running"],
        "last": _state["last"],
        "last_backfill": _state.get("last_backfill"),
        "progress": _state["progress"],
        "last_error": _state["last_error"],
    }
