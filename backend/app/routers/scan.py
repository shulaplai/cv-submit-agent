"""Scan endpoints + in-memory scan state + backfill trigger."""
import asyncio
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException
from sqlalchemy.orm import Session

from ..config import settings
from ..db import SessionLocal
from ..services.scanner import ScanSummary, _backfill_candidates, _enrich_one, _fetch_detail_for, run_scan
from ..services import scan_control

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/scan", tags=["scan"])

_state: dict = {
    "running": False,
    "last": None,
    "last_error": None,
    "progress": {"platform": "", "phase": "", "count": 0},
    "stop_requested": False,
    "track": None,  # "all" | "it" | "general" — which tracks the running scan covers
    "channels": [],  # 空 = 全部渠道；否則係 CHANNELS 嘅 subset
}

BACKFILL_LIMIT = 10

_VALID_TRACKS = {"all", "it", "general"}

# 可以逐個揀嘅渠道（同 scanner.CHANNELS 一致）
_VALID_CHANNELS = {"offertoday", "govhk_gbayes", "govhk_it", "govhk_general", "jobsdb"}
CHANNEL_LABEL = {
    "offertoday": "OfferToday",
    "govhk_gbayes": "大灣區計劃",
    "govhk_it": "政府 IT",
    "govhk_general": "政府一般",
    "jobsdb": "JobsDB",
}


def _channel_label(channels: list[str]) -> str:
    return "、".join(CHANNEL_LABEL.get(c, c) for c in channels)


async def _scan_job(track: str | None = None, channels: list[str] | None = None):
    db: Session = SessionLocal()
    try:
        progress = _state["progress"]
        progress.update({"platform": "", "phase": "", "count": 0})
        summary: ScanSummary = await run_scan(db, progress, track=track, channels=channels)
        _state["last"] = {
            "at": datetime.now(timezone.utc).isoformat(),
            "scanned": summary.scanned,
            "new_jobs": summary.new_jobs,
            "skipped_duplicates": summary.skipped_duplicates,
            "skipped_old": summary.skipped_old,
            "capped": summary.capped,
            "enriched": summary.enriched,
            "backfilled": summary.backfilled,
            "low_match": summary.low_match,
            "details_fetched": summary.details_fetched,
            "stopped": summary.stopped,
            "errors": summary.errors,
            "tracks": summary.tracks,
            "track": track or "all",
            "channels": channels or [],
        }
    except Exception as e:  # noqa: BLE001
        log.exception("scan crashed")
        _state["last_error"] = str(e)
    finally:
        _state["running"] = False
        _state["stop_requested"] = False
        scan_control.clear_stop()
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
async def start_scan(payload: dict | None = None):
    """Start a scan of the enabled tracks (all) or a single track (淨IT/淨一般).

    ``channels`` 可以再收窄到指定渠道（OfferToday／政府 IT／政府一般／大灣區計劃）。
    唔俾 channels 或者空 array = 全部渠道。
    """
    payload = payload or {}
    track = payload.get("track", "all")
    if track not in _VALID_TRACKS:
        raise HTTPException(status_code=400, detail=f"track 必須係 all / it / general（收到: {track}）")
    raw_channels = payload.get("channels") or []
    if not isinstance(raw_channels, (list, tuple)):
        raise HTTPException(status_code=400, detail="channels 必須係 array")
    channels: list[str] = []
    for c in raw_channels:
        c = str(c).strip()
        if c and c not in channels:
            if c not in _VALID_CHANNELS:
                raise HTTPException(
                    status_code=400,
                    detail=f"唔識嘅渠道: {c}（可用: {'/'.join(sorted(_VALID_CHANNELS))}）")
            channels.append(c)
    if _state["running"]:
        return {"started": False, "message": "scan 已經喺度行緊"}
    scan_control.clear_stop()
    _state["running"] = True
    _state["last_error"] = None
    _state["stop_requested"] = False
    _state["track"] = track
    _state["channels"] = channels
    asyncio.create_task(_scan_job(track, channels or None))
    label = {"all": "全部職位", "it": "IT 職位", "general": "一般職位"}[track]
    scope = f"｜{_channel_label(channels)}" if channels else "｜全部渠道"
    return {"started": True, "message": f"scan 已開始（{label}{scope}），請稍後查詢狀態"}


@router.post("/stop")
async def stop_scan():
    """暫停掣：中斷進行中嘅 scan；已掃到嘅內容照樣入庫。"""
    if not _state["running"]:
        return {"stopped": False, "message": "而家冇 scan 喺度行緊"}
    scan_control.request_stop()
    _state["stop_requested"] = True
    return {"stopped": True, "message": "已要求暫停——掃緊嘅部分會照入庫"}


async def _backfill_jd_job(limit: int):
    """為「冇 JD」嘅工逐份攞詳情（只用網頁，唔用 LLM）。"""
    db: Session = SessionLocal()
    try:
        from ..services.scanner import _fill_detail, _fetch_detail_for
        from ..services.scanner import _PaceGate
        from ..models import JobApplication
        from ..config import settings as _settings

        progress = _state["progress"]
        progress.update({"platform": "jd", "phase": "running", "count": 0})
        rows = (db.query(JobApplication)
                .filter(JobApplication.platform.in_(("offertoday", "jobsdb")))
                .filter((JobApplication.jd_text == "") | (JobApplication.jd_text.is_(None)))
                .order_by(JobApplication.id.desc())
                .limit(limit).all())
        pace = _PaceGate(_settings.SCAN_JOB_DELAY_MIN_SECONDS,
                         _settings.SCAN_JOB_DELAY_MAX_SECONDS)
        done = 0
        for row in rows:
            if scan_control.stop_requested():
                break
            progress["count"] = done + 1
            progress["platform"] = row.platform
            try:
                await _fill_detail(db, row, _fetch_detail_for(row.platform),
                                   pace=pace, prune=False)
            except Exception as e:  # noqa: BLE001
                log.warning("jd backfill failed for %s: %s", row.id, e)
            done += 1
        db.commit()
        left = (db.query(JobApplication)
                .filter(JobApplication.platform.in_(("offertoday", "jobsdb")))
                .filter((JobApplication.jd_text == "") | (JobApplication.jd_text.is_(None)))
                .count())
        _state["last_jd_backfill"] = {
            "at": datetime.now(timezone.utc).isoformat(), "processed": done, "left": left,
        }
        progress.update({"platform": "", "phase": "done", "count": 0})
    except Exception as e:  # noqa: BLE001
        log.exception("jd backfill crashed")
        _state["last_error"] = f"jd backfill: {e}"
    finally:
        _state["running"] = False
        scan_control.clear_stop()
        db.close()


@router.post("/backfill-jd")
async def start_backfill_jd(payload: dict | None = None):
    """即刻幫「冇 JD」嘅工補 JD（唔用 LLM）；default 每次 25 份。"""
    if _state["running"]:
        return {"started": False, "message": "已經有 scan／補齊喺度行緊，等佢完先"}
    limit = int((payload or {}).get("limit") or 25)
    limit = max(1, min(limit, 200))
    scan_control.clear_stop()
    _state["running"] = True
    _state["last_error"] = None
    asyncio.create_task(_backfill_jd_job(limit))
    return {"started": True, "limit": limit,
            "message": f"開始補 JD（最多 {limit} 份，冇 JD 嘅工）"}


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
        "last_jd_backfill": _state.get("last_jd_backfill"),
        "progress": _state["progress"],
        "last_error": _state["last_error"],
        "stop_requested": _state["stop_requested"],
        "track": _state.get("track"),
        "channels": _state.get("channels", []),
        "channel_labels": {c: CHANNEL_LABEL.get(c, c) for c in _VALID_CHANNELS},
    }
