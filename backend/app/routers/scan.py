"""Scan endpoints + in-memory scan state + backfill trigger."""
import asyncio
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

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
            "skipped_location": summary.skipped_location,
            "location_uncertain": summary.location_uncertain,
            "capped": summary.capped,
            "enriched": summary.enriched,
            "backfilled": summary.backfilled,
            "low_match": summary.low_match,
            "details_fetched": summary.details_fetched,
            "priority_kept": summary.priority_kept,
            "priority_capped": summary.priority_capped,
            "skipped_blocked": summary.skipped_blocked,
            "ai_checked": summary.ai_checked,
            "ai_non_it": summary.ai_non_it,
            "ai_llm_calls": summary.ai_llm_calls,
            "stopped": summary.stopped,
            "errors": summary.errors,
            "tracks": summary.tracks,
            "track": track or "all",
            "channels": channels or [],
        }
        _record_scan_time()
    except Exception as e:  # noqa: BLE001
        log.exception("scan crashed")
        _state["last_error"] = str(e)
    finally:
        _state["running"] = False
        _state["stop_requested"] = False
        scan_control.clear_stop()
        db.close()


async def _backfill_job(limit: int = BACKFILL_LIMIT, scope: str = "oldest"):
    """Enrich up to `limit` rows right now.

    ``scope="oldest"``      = 最舊未補嘅記錄（原本行為）
    ``scope="it_unscored"`` = 未評分嘅 IT 工（match_score = 0）—— 用戶要求：
    舊資料有 173 份 IT 工從來冇入過 LLM，分數永遠 0、排序永遠沉底；一撳分批補返。
    """
    from ..services.cv_loader import load_skills
    from ..services.scanner import unscored_it_candidates

    db: Session = SessionLocal()
    try:
        progress = _state["progress"]
        progress.update({"platform": "backfill", "phase": "running", "count": 0})
        rows = (unscored_it_candidates(db, limit) if scope == "it_unscored"
                else _backfill_candidates(db, limit))
        skills = load_skills()
        done = 0
        for row in rows:
            if scan_control.stop_requested():
                break
            progress["count"] = done + 1
            progress["platform"] = row.platform
            await _enrich_one(db, row, row.platform, _fetch_detail_for(row.platform), skills)
            done += 1
        db.commit()
        left = len(unscored_it_candidates(db, 10_000)) if scope == "it_unscored" else 0
        _state["last_backfill"] = {"at": datetime.now(timezone.utc).isoformat(),
                                   "processed": done, "scope": scope, "left": left}
        progress.update({"platform": "", "phase": "done", "count": 0})
    except Exception as e:  # noqa: BLE001
        log.exception("backfill crashed")
        _state["last_error"] = f"backfill: {e}"
    finally:
        _state["running"] = False
        scan_control.clear_stop()
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
async def start_backfill(payload: dict | None = None):
    """即刻補齊（會用 LLM）。

    body 可以係 `{"limit": 30, "scope": "it_unscored"}`：
      - scope="oldest"（預設）= 最舊未處理記錄，每次 10 份
      - scope="it_unscored" = 未評分（match_score=0）嘅 IT 工，每次 30 份
        —— 用戶要求：一撳補返嗰 173 份從來冇評分嘅 IT 工。
    """
    if _state["running"]:
        return {"started": False, "message": "scan 已經喺度行緊，等佢完先"}
    body = payload or {}
    scope = str(body.get("scope") or "oldest")
    if scope not in ("oldest", "it_unscored"):
        raise HTTPException(status_code=400, detail="scope 必須係 oldest 或 it_unscored")
    default_limit = 30 if scope == "it_unscored" else BACKFILL_LIMIT
    limit = int(body.get("limit") or default_limit)
    limit = max(1, min(limit, 200))
    scan_control.clear_stop()
    _state["running"] = True
    _state["last_error"] = None
    asyncio.create_task(_backfill_job(limit, scope))
    if scope == "it_unscored":
        return {"started": True, "limit": limit, "scope": scope,
                "message": f"開始補齊未評分 IT 工（最多 {limit} 份，會用 LLM 評分＋生成 CL）"}
    return {"started": True, "limit": limit, "scope": scope,
            "message": f"補齊已開始（最多 {limit} 份最舊未處理記錄）"}


def _last_scan_path() -> Path:
    """最後掃描時間檔案（可用 LAST_SCAN_PATH 覆寫 -> 測試先唔會污染真資料）。"""
    import os

    override = os.environ.get("LAST_SCAN_PATH")
    if override:
        return Path(override)
    return Path(__file__).resolve().parents[3] / "data" / "last_scan.json"


_LAST_SCAN_FILE = _last_scan_path()


def _record_scan_time() -> None:
    """記低最後一次掃描時間（開機 catch-up 要用；記憶體唔夠，restart 就冇）。"""
    try:
        _LAST_SCAN_FILE.parent.mkdir(parents=True, exist_ok=True)
        _LAST_SCAN_FILE.write_text(
            json.dumps({"at": datetime.now(timezone.utc).isoformat()}), encoding="utf-8")
    except Exception as e:  # noqa: BLE001 — 記錄失敗唔應該影響掃描
        log.warning("記錄 last_scan 失敗: %s", e)


def last_scan_age_hours() -> float | None:
    """上次掃描距今幾多小時（None = 從來冇紀錄）。"""
    try:
        raw = json.loads(_LAST_SCAN_FILE.read_text(encoding="utf-8"))
        at = datetime.fromisoformat(raw["at"])
        if at.tzinfo is None:
            at = at.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - at).total_seconds() / 3600
    except FileNotFoundError:
        return None
    except Exception as e:  # noqa: BLE001
        log.warning("讀 last_scan 失敗: %s", e)
        return None


def start_catchup_scan() -> bool:
    """即刻補掃一次（背景）。已經有 scan／補 JD 跑緊就唔會重複開。"""
    if _state["running"]:
        log.info("catch-up: 已經有 scan 跑緊，唔補")
        return False
    _state["running"] = True
    _state["last_error"] = None
    _state["stop_requested"] = False
    scan_control.clear_stop()
    asyncio.create_task(_scan_job())
    return True


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
