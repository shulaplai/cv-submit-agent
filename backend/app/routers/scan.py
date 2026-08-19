"""Scan endpoints + in-memory scan state."""
import asyncio
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..db import SessionLocal, get_db
from ..schemas import ScanResult
from ..services.scanner import ScanSummary, run_scan

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/scan", tags=["scan"])

_state: dict = {"running": False, "last": None, "last_error": None}


async def _scan_job():
    db: Session = SessionLocal()
    try:
        summary: ScanSummary = await run_scan(db)
        _state["last"] = {
            "at": datetime.now(timezone.utc).isoformat(),
            "scanned": summary.scanned,
            "new_jobs": summary.new_jobs,
            "skipped_duplicates": summary.skipped_duplicates,
            "enriched": summary.enriched,
            "low_match": summary.low_match,
            "errors": summary.errors,
        }
    except Exception as e:  # noqa: BLE001
        log.exception("scan crashed")
        _state["last_error"] = str(e)
    finally:
        _state["running"] = False
        db.close()


@router.post("")
async def start_scan():
    if _state["running"]:
        return {"started": False, "message": "scan 已經喺度行緊"}
    _state["running"] = True
    _state["last_error"] = None
    asyncio.create_task(_scan_job())
    return {"started": True, "message": "scan 已開始，請稍後查詢狀態"}


@router.get("/status")
def scan_status():
    return {"running": _state["running"], "last": _state["last"], "last_error": _state["last_error"]}
