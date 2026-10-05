"""Job application endpoints: list, detail, enrich, CL versions, apply actions."""
import asyncio
import logging
from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, or_
from sqlalchemy.orm import Session, selectinload

from ..config import settings
from ..db import SessionLocal, get_db
from ..models import CoverLetter, JobApplication, Profile, utcnow
from ..schemas import (
    CoverLetterEditIn,
    EmailPreview,
    JobApplicationOut,
    JobListOut,
    RegenerateCLLIn,
    UpdateApplicationIn,
)
from ..services import scraper_govhk, scraper_jobsdb, scraper_offertoday
from ..services.apply_bot import open_apply
from ..services.cl_generator import generate_cl_checked
from ..services.cv_loader import (CVError, VARIANT_LABEL, get_cv_text, load_skills,
                                  resolve_cv_for_job)
from ..services.email_bot import build_email_polished
from ..services.language import detect_language
from ..services.llm import LLMError
from ..services.matcher import score_job
from ..services.scanner import make_dup_key
from ..services.jobdate import parse_posted_date
from ..services.scraper_base import get_browser
from ..services.store import mark_applied
from ..services.tuning import load_tuning

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/jobs", tags=["jobs"])

VALID_STATUSES = {
    "pending_review", "low_match", "applied", "needs_manual_intervention",
    "failed", "interviewing", "rejected", "offer", "no_response",
}
# 有記錄結果嘅狀態（PATCH 時會記 outcome_at，餵統計漏斗）
OUTCOME_STATUSES = {"interviewing", "rejected", "offer", "no_response"}


def _detail_fetcher(platform: str):
    """platform -> 可以補詳情嘅 scraper（政府工三個子渠道都行 govhk scraper）。"""
    if platform == "jobsdb":
        return scraper_jobsdb.fetch_detail
    if platform == "offertoday":
        return scraper_offertoday.fetch_detail
    if (platform or "").startswith("govhk"):
        return scraper_govhk.fetch_detail
    return None


def _load(db: Session, job_id: int) -> JobApplication:
    row = db.get(JobApplication, job_id)
    if row is None:
        raise HTTPException(status_code=404, detail="job not found")
    return row


def _attach_dup_counts(db: Session, rows: list[JobApplication]) -> None:
    """Set dup_count = number of OTHER rows sharing the same dup_key."""
    keys = {r.dup_key for r in rows if r.dup_key}
    if not keys:
        for r in rows:
            r.dup_count = 0
        return
    counts = dict(
        db.query(JobApplication.dup_key, func.count())
        .filter(JobApplication.dup_key.in_(keys))
        .group_by(JobApplication.dup_key)
        .all()
    )
    for r in rows:
        r.dup_count = max(0, (counts.get(r.dup_key, 0) - 1)) if r.dup_key else 0


def _split_multi(value: str | None) -> list[str]:
    """Comma-separated multi-select -> non-empty trimmed list."""
    if not value:
        return []
    return [v.strip() for v in value.split(",") if v.strip()]


def _parse_day(value: str | None, name: str, date_only: bool) -> date | datetime | None:
    """Parse a YYYY-MM-DD filter value; 400 on garbage. Returns a date (or a
    datetime when date_only is False — used for created_at ranges)."""
    if not value:
        return None
    try:
        if date_only:
            return date.fromisoformat(value)
        d = datetime.fromisoformat(value)
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        return d
    except ValueError:
        raise HTTPException(status_code=400, detail=f"{name} 格式唔啱: {value}")


def _apply_filters(query, *, statuses=None, platforms=None, category=None, q="",
                   show_all=False, added_from=None, added_to=None,
                   posted_from=None, posted_to=None, min_match=None, max_match=None,
                   has_jd=False, has_cl=False, ready_to_apply=False,
                   ai_only=False, exclude_contract=False, exclude_agency=False,
                   levels=None):
    """Shared filter builder. ``statuses``/``platforms`` are lists (multi-select).
    Used both for the main query and for facet counts.

    ``ai_only`` / ``exclude_contract`` / ``exclude_agency`` / ``levels`` 係
    「搵更適合自己嘅工」嘅篩選（flag 喺 jobflags.py 計）：
      - ai_only: 標題或 JD 提到 AI（職位台「✦ AI 職位」chip）
      - exclude_contract: 唔要合約／臨時／兼職／實習（用戶想搵穩定長工）
      - exclude_agency: 唔要外派／獵頭／人力資源公司
      - levels: 資歷級別（under／fit／over 複選；未評分嘅工 level 係空，
        只有用戶明確揀 level 先會排除佢哋）
    """
    if not settings.JOBSDB_ENABLED:
        # JobsDB is hidden for now — keep its rows out of the board.
        query = query.filter(JobApplication.platform != "jobsdb")
    if statuses:
        query = query.filter(JobApplication.status.in_(statuses))
    if platforms:
        query = query.filter(JobApplication.platform.in_(platforms))
    if category in ("it", "general"):
        query = query.filter(JobApplication.category == category)
    if ai_only:
        query = query.filter(JobApplication.ai_match.is_(True))
    if exclude_contract:
        query = query.filter(JobApplication.is_contract.is_(False))
    if exclude_agency:
        query = query.filter(JobApplication.is_agency.is_(False))
    if levels:
        query = query.filter(JobApplication.match_level.in_(levels))
    # 低匹配工預設隱藏；但若用戶明確篩選 low_match 就唔好再排除（否則會出空列表）
    if not show_all and (not statuses or "low_match" not in statuses):
        query = query.filter(JobApplication.status != "low_match")
    if q:
        like = f"%{q}%"
        query = query.filter(or_(JobApplication.title.like(like),
                                 JobApplication.company.like(like)))
    # 入庫日期 range (created_at is when the job entered the DB)
    d_from = _parse_day(added_from, "added_from", False)
    if d_from is not None:
        query = query.filter(JobApplication.created_at >= d_from)
    d_to = _parse_day(added_to, "added_to", False)
    if d_to is not None:
        query = query.filter(JobApplication.created_at < d_to + timedelta(days=1))
    # 刊登日期 range (normalized posted_date column)
    p_from = _parse_day(posted_from, "posted_from", True)
    if p_from is not None:
        query = query.filter(JobApplication.posted_date >= p_from)
    p_to = _parse_day(posted_to, "posted_to", True)
    if p_to is not None:
        query = query.filter(JobApplication.posted_date <= p_to)
    # 匹配度範圍
    if min_match is not None:
        query = query.filter(JobApplication.match_score >= min_match)
    if max_match is not None:
        query = query.filter(JobApplication.match_score <= max_match)
    # 有無 JD / 有無 CL
    if has_jd:
        query = query.filter(JobApplication.jd_text != "")
    if has_cl:
        query = query.filter(JobApplication.cover_letters.any())
    # 「可以即刻投遞」: 待處理 + 有 CL 已備 + 唔係外部網站
    if ready_to_apply:
        query = query.filter(
            JobApplication.status == "pending_review",
            JobApplication.apply_method != "external_link",
            JobApplication.cover_letters.any(),
        )
    return query


def _facets(db: Session, **filters) -> dict:
    """Per-status / per-platform counts under the active filters (excluding the
    dimension being counted) — powers the badge numbers on the filter chips.

    另外回報 AI／合約／外派／資歷級別嘅數量（新 chip 用），同樣「唔計自己嗰個
    維度」——否則 chip 一撳落去自己就會顯示 0。
    """
    status_facets: dict[str, int] = {}
    base = _apply_filters(db.query(JobApplication),
                          statuses=None, platforms=filters.get("platforms"),
                          **{k: v for k, v in filters.items() if k not in ("statuses", "platforms")})
    for s, n in base.with_entities(JobApplication.status, func.count()).group_by(JobApplication.status):
        status_facets[s] = n
    platform_facets: dict[str, int] = {}
    base = _apply_filters(db.query(JobApplication),
                          statuses=filters.get("statuses"), platforms=None,
                          **{k: v for k, v in filters.items() if k not in ("statuses", "platforms")})
    for p, n in base.with_entities(JobApplication.platform, func.count()).group_by(JobApplication.platform):
        platform_facets[p] = n

    # AI／合約／外派：計嘅時候唔套用 ai_only（否則撳咗 AI chip 之後其他數字會變 0）
    flag_base = {k: v for k, v in filters.items() if k != "ai_only"}
    flag_counts: dict[str, int] = {}
    for flag, column in (("ai", JobApplication.ai_match),
                         ("contract", JobApplication.is_contract),
                         ("agency", JobApplication.is_agency)):
        q = _apply_filters(db.query(JobApplication), ai_only=False, **flag_base)
        flag_counts[flag] = q.filter(column.is_(True)).count()

    # 資歷級別：唔套用 levels（同上原因）
    level_base = {k: v for k, v in filters.items() if k != "levels"}
    level_facets: dict[str, int] = {}
    q = _apply_filters(db.query(JobApplication), levels=[], **level_base)
    for lvl, n in q.with_entities(JobApplication.match_level, func.count()).group_by(
            JobApplication.match_level):
        level_facets[lvl or "unknown"] = n

    return {"statuses": status_facets, "platforms": platform_facets,
            "levels": level_facets, **flag_counts}


def _attach_cv_variant(rows: list[JobApplication]) -> None:
    """每行填 `cv_variant`（申請時實際會交邊份 CV；UI 顯示用）。"""
    for r in rows:
        try:
            _path, variant = resolve_cv_for_job(r.title or "", r.jd_language or "zh")
        except Exception:  # noqa: BLE001 — 冇設定 CV 都唔應該爆
            variant = "default"
        r.cv_variant = VARIANT_LABEL.get(variant or "default", variant or "default")


@router.get("", response_model=JobListOut)
def list_jobs(status: str | None = None, platform: str | None = None,
              category: str | None = None, q: str = "",
              show_all: bool = False, limit: int = 100, offset: int = 0,
              sort: str = "updated",
              added_from: str | None = None, added_to: str | None = None,
              posted_from: str | None = None, posted_to: str | None = None,
              min_match: int | None = None, max_match: int | None = None,
              has_jd: bool = False, has_cl: bool = False,
              ready_to_apply: bool = False,
              ai_only: bool = False, exclude_contract: bool = False,
              exclude_agency: bool = False, levels: str | None = None,
              db: Session = Depends(get_db)):
    """List jobs. status / platform accept comma-separated multi-selects;
    ready_to_apply = 待處理 + 有 CL + 唔係外部網站 (可以即刻投遞).

    sort="focus" = 「更適合自己」排序：AI 優先 -> 高分 -> 新鮮。
    """
    filters = dict(
        statuses=_split_multi(status), platforms=_split_multi(platform),
        category=category, q=q, show_all=show_all,
        added_from=added_from, added_to=added_to,
        posted_from=posted_from, posted_to=posted_to,
        min_match=min_match, max_match=max_match,
        has_jd=has_jd, has_cl=has_cl, ready_to_apply=ready_to_apply,
        ai_only=ai_only, exclude_contract=exclude_contract,
        exclude_agency=exclude_agency, levels=_split_multi(levels),
    )
    query = _apply_filters(db.query(JobApplication), **filters)
    order = {
        "updated": (JobApplication.updated_at.desc(),),
        "created": (JobApplication.created_at.desc(),),   # 入庫日期（最新先）
        # 刊登日期：用正規化 posted_date 排序（無刊登日期嘅排最後）
        "posted": (JobApplication.posted_date.desc().nullslast(), JobApplication.id.desc()),
        "match": (JobApplication.match_score.desc(),),
        # 「AI 優先 + 高分 + 新鮮」：第一眼就見到最啱自己嘅工
        "focus": (JobApplication.ai_match.desc(), JobApplication.match_score.desc(),
                  JobApplication.posted_date.desc().nullslast(), JobApplication.id.desc()),
    }.get(sort)
    if order is None:
        raise HTTPException(
            status_code=400, detail="sort 必須係 updated/created/posted/match/focus")
    total = query.count()
    # 低匹配總數（永遠回報，畀前端顯示「顯示低匹配 (N)」同 pager 提示）
    hidden_q = db.query(JobApplication).filter(JobApplication.status == "low_match")
    if not settings.JOBSDB_ENABLED:
        hidden_q = hidden_q.filter(JobApplication.platform != "jobsdb")
    hidden = hidden_q.count()
    rows = (query.order_by(*order)
            .offset(offset).limit(limit)
            .options(selectinload(JobApplication.cover_letters)).all())
    _attach_dup_counts(db, rows)
    _attach_cv_variant(rows)
    return JobListOut(items=rows, total=total, hidden_low_match=hidden,
                      facets=_facets(db, **filters))


# ------------------------------------------------------------------ batch apply

_batch_state: dict = {"running": False, "total": 0, "done": 0, "results": []}


class BatchApplyIn(BaseModel):
    ids: list[int]
    auto: bool | None = None


async def _run_batch(ids: list[int], auto: bool | None) -> None:
    from ..services.apply_bot import open_apply
    from ..services.cl_generator import generate_cl_checked
    from ..services.cv_loader import get_cv_text

    db: Session = SessionLocal()
    try:
        profile = db.get(Profile, 1)
        effective_auto = auto if auto is not None else (
            profile.auto_submit if profile else settings.AUTO_SUBMIT
        )
        _batch_state.update({"running": True, "total": len(ids), "done": 0, "results": []})

        # 同名同公司（dup_key）嘅工喺跨平台會各有一行；同一批次只投一份，
        # 免得同一份工（例如同時喺 gov.hk IT 同一般 channel 出現）交兩次。
        batch_dup_keys: dict[str, int] = {}
        for _jid in ids:
            _row = db.get(JobApplication, _jid)
            if _row is not None and _row.status == "applied":
                _k = _dup_key_of(_row)
                if _k:
                    batch_dup_keys.setdefault(_k, _jid)

        for job_id in ids:
            entry: dict = {"id": job_id, "title": "", "ok": False,
                           "submitted": False, "message": ""}
            try:
                row = db.get(JobApplication, job_id)
                if row is None:
                    entry["message"] = "揾唔到職位"
                elif row.status == "applied":
                    entry.update({"ok": True, "title": row.title, "message": "已經投咗，skip"})
                elif _dup_key_of(row) and _dup_key_of(row) in batch_dup_keys:
                    first = batch_dup_keys[_dup_key_of(row)]
                    entry.update({"ok": True, "title": row.title,
                                  "message": f"⚠ 同「#{first}」係同一份工（同公司同職位），"
                                             f"已經／即將投過，唔會重複交。"})
                elif _dup_key_of(row) and _dup_key_of(row) in _applied_dup_keys(db, row):
                    entry.update({"ok": True, "title": row.title,
                                  "message": "⚠ 同一份工（同公司同職位）已經投過，唔會重複交。"})
                elif row.apply_method == "external_link":
                    entry.update({"title": row.title, "message": "外部網站，唔自動投（俾 link 你）"})
                else:
                    entry["title"] = row.title
                    _sync_jd_language(db, row)
                    # ensure a CL exists (generate on the fly if possible)
                    cl_text = ""
                    latest = (db.query(CoverLetter).filter_by(application_id=row.id)
                              .order_by(CoverLetter.version.desc()).first())
                    if latest:
                        cl_text = latest.content
                    elif row.jd_text:
                        try:
                            cv_text = get_cv_text(row.jd_language, row.title)
                            job_dict = {"title": row.title, "company": row.company,
                                        "location": row.location, "salary_range": row.salary_range,
                                        "jd_text": row.jd_text, "short_desc": ""}
                            content, _w = await generate_cl_checked(
                                cv_text, row.jd_text, job_dict, row.jd_language)
                            cl_text = content
                            db.add(CoverLetter(application_id=row.id, language=row.jd_language,
                                               content=content, version=1))
                            db.commit()
                        except Exception as e:  # noqa: BLE001
                            entry["message"] = f"未生成 CL：{str(e)[:120]}"
                    result = await open_apply(row, cl_text, auto=effective_auto)
                    entry.update({
                        "ok": result.get("ok", False),
                        "submitted": result.get("submitted", False),
                        "message": result.get("message", ""),
                    })
                    if result.get("submitted"):
                        row.status = "applied"
                        row.applied_at = row.applied_at or utcnow()
                        db.commit()
            except Exception as e:  # noqa: BLE001
                from ..services.apply_bot import friendly_browser_error
                entry["message"] = friendly_browser_error(e) or str(e)[:200]
            finally:
                _batch_state["results"].append(entry)
                _batch_state["done"] += 1
    finally:
        _batch_state["running"] = False
        db.close()


def _sync_jd_language(db: Session, row: JobApplication) -> None:
    """申請前按 JD／標題重新校正 jd_language（決定交中文定英文 CV／intro）。

    舊資料好多都錯標（例如中文 JD 標成 en），所以每次投遞前校正一次並寫返 DB。
    """
    from ..services.language import detect_language

    text = (row.jd_text or "").strip() or (row.title or "")
    correct = detect_language(text)
    if correct and row.jd_language != correct:
        log.info("修正 jd_language：job #%s %r（%s -> %s）",
                 row.id, (row.title or "")[:30], row.jd_language, correct)
        row.jd_language = correct
        db.commit()


def _dup_key_of(row: JobApplication) -> str:
    """dup_key（同公司同職位）；空白就即場計（舊資料好多都冇填）。"""
    key = (row.dup_key or "").strip()
    if not key and row.company and row.title:
        key = make_dup_key(row.company, row.title)
    return key


def _applied_dup_keys(db: Session, row: JobApplication) -> set[str]:
    """同公司同職位（dup_key）而且已經投過嘅工 -- 用嚟擋跨平台重複投遞。"""
    key = _dup_key_of(row)
    if not key:
        return set()
    hit = (db.query(JobApplication.id)
           .filter(JobApplication.dup_key == key,
                   JobApplication.status == "applied",
                   JobApplication.id != row.id)
           .first())
    return {key} if hit else set()


@router.post("/batch-apply")
async def batch_apply(payload: BatchApplyIn):
    """Apply to a whole checked list at once (background task, per-job results)."""
    if _batch_state["running"]:
        return {"started": False, "message": "batch 已經喺度行緊"}
    if not payload.ids:
        return {"started": False, "message": "冇揀到職位"}
    asyncio.create_task(_run_batch(payload.ids, payload.auto))
    return {"started": True, "total": len(payload.ids),
            "message": f"開始一齊投遞 {len(payload.ids)} 份（逐份處理，可睇進度）"}


@router.get("/batch-status")
def batch_status():
    return _batch_state


# ---------------------------------------------------------------- batch AI check

_ai_check_state: dict = {"running": False, "checked": 0, "total": 0, "batches": 0,
                         "non_it": 0, "it_ai": 0, "it": 0, "failed_batches": 0,
                         "errors": [], "marked_ids": [], "last": None}


class AiCheckIn(BaseModel):
    limit: int | None = None
    batch_size: int | None = None


async def _run_ai_check(limit: int, batch_size: int) -> None:
    from ..services import ai_filter

    db: Session = SessionLocal()
    try:
        _ai_check_state.update({
            "running": True, "checked": 0, "total": 0, "batches": 0,
            "non_it": 0, "it_ai": 0, "it": 0, "failed_batches": 0,
            "errors": [], "marked_ids": [],
        })
        progress = {"phase": "starting", "done": 0, "total": 0, "batch": 0}
        result = await ai_filter.run_ai_check(
            db, limit=limit, batch_size=batch_size, progress=progress)
        payload = result.as_dict()
        _ai_check_state.update(payload)
        _ai_check_state["running"] = False
        _ai_check_state["total"] = progress.get("total", payload["checked"])
        _ai_check_state["last"] = {"at": utcnow().isoformat(), **payload}
    except Exception as e:  # noqa: BLE001
        log.exception("ai check crashed")
        _ai_check_state.update({"running": False, "errors": [str(e)[:200]]})
    finally:
        db.close()


@router.get("/ai-check/pending")
def ai_check_pending(db: Session = Depends(get_db)):
    """仲有幾多份 IT 工未做 AI 檢查（UI 顯示按鈕數字用）。"""
    from ..services import ai_filter

    tuning = load_tuning(db)
    return {
        "pending": ai_filter.count_pending(db),
        "batch_size": tuning.ai_check_batch_size,
        "limit": tuning.ai_check_limit,
        "enabled": tuning.ai_check_enabled,
    }


@router.post("/ai-check")
async def start_ai_check(payload: AiCheckIn | None = None):
    """批量 AI 檢查：分批（預設 40 份/call）搵出唔係 IT 嘅職位並標低匹配。"""
    from ..services import ai_filter

    if _ai_check_state.get("running"):
        return {"started": False, "message": "AI 檢查已經喺度行緊"}
    tuning = load_tuning()
    limit = int((payload.limit if payload and payload.limit else tuning.ai_check_limit)
                or ai_filter.DEFAULT_LIMIT)
    batch_size = int((payload.batch_size if payload and payload.batch_size
                      else tuning.ai_check_batch_size) or ai_filter.DEFAULT_BATCH_SIZE)
    limit = max(1, min(limit, 2000))
    batch_size = max(1, min(batch_size, 200))
    asyncio.create_task(_run_ai_check(limit, batch_size))
    calls = max(1, -(-limit // batch_size))
    return {"started": True, "limit": limit, "batch_size": batch_size,
            "estimated_llm_calls": calls,
            "message": f"開始批量 AI 檢查（最多 {limit} 份，每批 {batch_size} 份 ≈ {calls} 次 LLM）"}


@router.get("/ai-check/status")
def ai_check_status():
    return _ai_check_state


@router.post("/ai-check/reset")
def ai_check_reset(payload: dict | None = None):
    """還原 AI 檢查判定（清空 verdict，令佢下次再檢查；唔會刪工）。"""
    ids = (payload or {}).get("ids") or []
    db = SessionLocal()
    try:
        q = db.query(JobApplication)
        if ids:
            q = q.filter(JobApplication.id.in_([int(i) for i in ids]))
        else:
            q = q.filter(JobApplication.ai_verdict != "")
        n = 0
        for row in q.all():
            row.ai_verdict = ""
            row.ai_verdict_reason = ""
            row.ai_checked_at = None
            if (row.match_reason or "").startswith("AI 檢查：") and row.status == "low_match":
                row.status = "pending_review"
                row.match_reason = ""
            n += 1
        db.commit()
        return {"ok": True, "reset": n}
    finally:
        db.close()


@router.get("/email-templates")
def email_templates():
    """List the email body templates the user can pick before sending."""
    from ..services.email_templates import list_templates
    return {"templates": list_templates()}


@router.get("/{job_id}", response_model=JobApplicationOut)
def get_job(job_id: int, db: Session = Depends(get_db)):
    row = _load(db, job_id)
    _attach_dup_counts(db, [row])
    _attach_cv_variant([row])
    return row


@router.post("/{job_id}/fetch-detail")
async def fetch_job_detail(job_id: int, db: Session = Depends(get_db)):
    """即刻去職位網站攞呢份工嘅完整 JD（詳情頁），順便更新刊登日期／公司／地點。

    專為「列表快照冇 JD」嘅卡而設：用戶一撳入去就自動補，唔使等下次 scan，
    亦唔會用 LLM（唔會扣 API 錢）。
    """
    from ..services.apply_bot import _is_closed_error
    from ..services.scanner import _draft_from_row
    from ..services.scraper_base import get_browser, repair_browsers

    row = _load(db, job_id)
    fetch_detail = _detail_fetcher(row.platform)
    if fetch_detail is None:
        return {"ok": False, "updated": False,
                "message": "呢個平台冇詳情頁可以補（JD 喺掃描時已經入庫）。"}

    async def _fetch_once():
        session = await get_browser(row.platform)
        return await fetch_detail(session, _draft_from_row(row))

    try:
        try:
            draft = await _fetch_once()
        except Exception as e:  # noqa: BLE001 — Chrome 被關 -> 自動修復再試一次
            if not _is_closed_error(e):
                raise
            log.warning("detail fetch: browser closed for job %s — repairing", job_id)
            await repair_browsers()
            draft = await _fetch_once()
    except Exception as e:  # noqa: BLE001
        log.warning("fetch-detail failed for job %s: %s", job_id, e)
        return {"ok": False, "updated": False,
                "message": f"攞唔到詳情：{str(e)[:140]}（可以撳「載入 JD + 生成 CL」再試）"}

    changed = []
    if draft.jd_text and draft.jd_text != row.jd_text:
        row.jd_text = draft.jd_text
        changed.append("JD")
    if draft.company and draft.company != row.company:
        row.company = draft.company
        changed.append("公司")
    if draft.location and draft.location != row.location:
        row.location = draft.location
        changed.append("地點")
    if draft.salary_range and draft.salary_range != row.salary_range:
        row.salary_range = draft.salary_range
        changed.append("薪酬")
    if draft.posted_at and draft.posted_at != row.posted_at:
        row.posted_at = draft.posted_at
        row.posted_date = parse_posted_date(draft.posted_at)
        changed.append("刊登日期")
    if draft.external_url and not row.external_url:
        row.external_url = draft.external_url
        row.apply_method = "external_link"
        changed.append("外部申請連結")
    # 政府工：詳情頁嘅「申請須知」先有聯絡 email —— 補 JD 時順手補返，
    # 否則舊資料（冇 email，被標成 form）永遠投唔到。
    if draft.contact_email and draft.contact_email != row.contact_email:
        row.contact_email = draft.contact_email
        changed.append("聯絡 email")
    if draft.contact_person and draft.contact_person != row.contact_person:
        row.contact_person = draft.contact_person
        changed.append("聯絡人")
    if draft.contact_email and row.apply_method != "external_link" and row.apply_method != "email":
        row.apply_method = "email"
        changed.append("申請方式（email）")
    db.commit()
    db.refresh(row)

    if not changed:
        return {"ok": True, "updated": False, "job": JobApplicationOut.model_validate(row),
                "message": "網站而家冇新資料（JD 可能仲未刊登）。"}
    return {"ok": True, "updated": True, "job": JobApplicationOut.model_validate(row),
            "message": f"已更新：{'、'.join(changed)}"}


@router.post("/{job_id}/refresh", response_model=JobApplicationOut)
async def refresh_job(job_id: int, db: Session = Depends(get_db)):
    """Fetch full JD (if missing), re-run match score and (re)generate CL."""
    row = _load(db, job_id)
    fetch_detail = _detail_fetcher(row.platform)

    if fetch_detail and not row.jd_text:
        from ..services.scraper_base import JobDraft
        draft = JobDraft(
            platform=row.platform, job_id=row.job_id_on_platform, title=row.title,
            url=row.url, company=row.company, location=row.location,
            salary_range=row.salary_range, jd_text=row.jd_text,
            posted_at=row.posted_at, apply_method=row.apply_method,
            contact_email=row.contact_email, contact_person=row.contact_person,
            external_url=row.external_url,
        )
        session = await get_browser(row.platform)
        draft = await fetch_detail(session, draft)
        row.jd_text = draft.jd_text
        if draft.company:
            row.company = draft.company
        if draft.location:
            row.location = draft.location
        if draft.salary_range:
            row.salary_range = draft.salary_range
        if draft.posted_at:
            row.posted_at = draft.posted_at
            row.posted_date = parse_posted_date(draft.posted_at)
            from ..services.jobdate import is_fresh
            max_age = settings.MAX_JOB_AGE_DAYS
            if max_age > 0 and not is_fresh(draft.posted_at, max_age):
                # 手動 refresh：唔刪你撳緊嗰份工，改為標記過期（隱藏出主頁）
                if row.status != "applied":
                    row.status = "low_match"
                row.match_reason = f"刊登日期已超過 {max_age} 日，已過期"
                db.commit()
                db.refresh(row)
                return row
        if draft.external_url:
            row.external_url = draft.external_url
            row.apply_method = "external_link"
        db.flush()

    row.jd_language = detect_language(row.jd_text or row.title)
    job_dict = {"title": row.title, "company": row.company, "location": row.location,
                "salary_range": row.salary_range, "jd_text": row.jd_text, "short_desc": ""}
    score, reason, level = await score_job(job_dict, load_skills())
    row.match_score = score
    row.match_reason = reason
    row.match_level = level
    from ..services.jobflags import compute_flags
    _flags = compute_flags(row)
    row.ai_match = _flags["ai_match"]
    row.is_contract = _flags["is_contract"]
    row.is_agency = _flags["is_agency"]
    if row.company and row.title:
        row.dup_key = make_dup_key(row.company, row.title)
    # 已投遞嘅工唔可以因為 refresh 而變返未投（否則會被重複投遞）。
    if row.status != "applied":
        row.status = "pending_review" if score >= settings.MATCH_THRESHOLD else "low_match"

    if score >= settings.MATCH_THRESHOLD:
        try:
            cv_text = get_cv_text(row.jd_language, row.title)
            content, _warning = await generate_cl_checked(
                cv_text, row.jd_text or row.title, job_dict, row.jd_language
            )
            latest = (db.query(CoverLetter).filter_by(application_id=row.id)
                      .order_by(CoverLetter.version.desc()).first())
            db.add(CoverLetter(application_id=row.id, language=row.jd_language,
                               content=content, version=(latest.version + 1 if latest else 1)))
        except (LLMError, CVError) as e:
            # 冇 LLM key 或者未設定 CV 都唔應該令「重新整理」爆 500：分數照更新
            log.warning("CL gen failed on refresh: %s", e)

    if not row.job_summary and row.jd_text:
        try:
            from ..services.matcher import summarize_job
            row.job_summary = await summarize_job(job_dict)
        except LLMError as e:
            log.warning("summary gen failed on refresh: %s", e)

    db.commit()
    db.refresh(row)
    return row


@router.post("/{job_id}/cover-letters", response_model=JobApplicationOut)
def save_cover_letter(job_id: int, payload: CoverLetterEditIn, db: Session = Depends(get_db)):
    """Save an edited cover letter as a NEW version (history preserved)."""
    row = _load(db, job_id)
    latest = (db.query(CoverLetter).filter_by(application_id=row.id)
              .order_by(CoverLetter.version.desc()).first())
    lang = latest.language if latest else detect_language(row.jd_text or row.title)
    db.add(CoverLetter(application_id=row.id, language=lang,
                       content=payload.content, version=(latest.version + 1 if latest else 1)))
    db.commit()
    db.refresh(row)
    return row


@router.post("/{job_id}/regenerate", response_model=JobApplicationOut)
async def regenerate_cl(job_id: int, payload: RegenerateCLLIn, db: Session = Depends(get_db)):
    row = _load(db, job_id)
    if not row.jd_text:
        raise HTTPException(status_code=400, detail="JD 未載入，請先 refresh")
    job_dict = {"title": row.title, "company": row.company, "location": row.location,
                "salary_range": row.salary_range, "jd_text": row.jd_text, "short_desc": ""}
    try:
        cv_text = get_cv_text(row.jd_language, row.title)
        content, _warning = await generate_cl_checked(
            cv_text, row.jd_text, job_dict, row.jd_language,
            instructions=payload.instructions,
        )
        latest = (db.query(CoverLetter).filter_by(application_id=row.id)
                  .order_by(CoverLetter.version.desc()).first())
        db.add(CoverLetter(application_id=row.id, language=row.jd_language,
                           content=content, version=(latest.version + 1 if latest else 1)))
        db.commit()
    except LLMError as e:
        raise HTTPException(status_code=502, detail=f"CL 生成失敗: {e}")
    db.refresh(row)
    return row


@router.get("/{job_id}/email-preview", response_model=EmailPreview)
async def email_preview(job_id: int, template: str = "standard", db: Session = Depends(get_db)):
    """Preview the composed application email WITHOUT opening Mail.

    同實際發送行**同一條 AI 潤色路徑**（`build_email_polished`）——所以預覽同
    實寄係同一份文字；`polished` / `body_original` 話俾 UI 知有冇潤色過。
    """
    row = _load(db, job_id)
    if not row.contact_email:
        raise HTTPException(status_code=400, detail="呢份工冇聯絡 email，唔可以用 email 申請")
    latest = (db.query(CoverLetter).filter_by(application_id=row.id)
              .order_by(CoverLetter.version.desc()).first())
    _sync_jd_language(db, row)
    from ..services.cv_loader import resolve_cv_path
    # 同實際發送一致：用版本階梯（AI -> Full-stack -> Developer -> 通用）
    cv_path, _variant = resolve_cv_for_job(row.title, row.jd_language)
    if not cv_path:
        cv_path = resolve_cv_path(row.jd_language) or resolve_cv_path(
            "zh" if row.jd_language == "en" else "en")
    email, polished, original_body = await build_email_polished(
        row, latest.content if latest else "", cv_path, template)
    return EmailPreview(
        to=email["to"], contact_person=row.contact_person,
        subject=email["subject"], body=email["body"], attachment=email["attachment"],
        polished=polished, body_original=original_body,
    )


class ApplyIn(BaseModel):
    """auto=None -> follow profile/settings; True -> auto-submit; False -> manual review."""
    auto: bool | None = None
    template: str = "standard"


@router.post("/{job_id}/apply")
async def start_apply(job_id: int, payload: ApplyIn | None = None, db: Session = Depends(get_db)):
    """Run the application flow (browser or Mail).

    Auto-submits when auto=True (profile default when unset): fills the form +
    CV, clicks submit / sends the email, and marks the job applied on success.
    Never submits external-link jobs; aborts on login walls / missing CL / CV.
    """
    row = _load(db, job_id)
    latest = (db.query(CoverLetter).filter_by(application_id=row.id)
              .order_by(CoverLetter.version.desc()).first())
    cl_text = latest.content if latest else ""

    profile = db.get(Profile, 1)
    auto = payload.auto if (payload and payload.auto is not None) else (
        profile.auto_submit if profile else settings.AUTO_SUBMIT
    )
    template = payload.template if (payload and payload.template) else "standard"

    # 語言校正（會影響揀邊個語言版本嘅 CV／自我介紹）
    _sync_jd_language(db, row)

    # 跨平台重複提醒：同一份工（同公司同職位）可能已經投過另一行
    dup_warn = ""
    if _dup_key_of(row) in _applied_dup_keys(db, row):
        dup_warn = "⚠ 注意：同一份工（同公司同職位）已經喺另一個平台行投過。"

    result = await open_apply(row, cl_text, auto=auto, template_key=template)
    if result.get("submitted"):
        row.status = "applied"
        row.applied_at = row.applied_at or utcnow()
        db.commit()
        result["job_id"] = row.id
    if dup_warn:
        result["message"] = f"{result.get('message', '')} {dup_warn}".strip()
    return result


@router.post("/{job_id}/mark-applied", response_model=JobApplicationOut)
def apply_done(job_id: int, db: Session = Depends(get_db)):
    row = mark_applied(db, job_id)
    if row is None:
        raise HTTPException(status_code=404, detail="job not found")
    return _load(db, job_id)


@router.patch("/{job_id}", response_model=JobApplicationOut)
def update_job(job_id: int, payload: UpdateApplicationIn, db: Session = Depends(get_db)):
    """更新狀態／面試進度／備註。記錄結果（面試中／冇回音／落選／Offer）會順手
    填 `outcome_at`，統計頁嘅漏斗就靠佢。"""
    row = _load(db, job_id)
    if payload.status is not None:
        if payload.status not in VALID_STATUSES:
            raise HTTPException(status_code=400, detail=f"invalid status: {payload.status}")
        row.status = payload.status
        if payload.status in OUTCOME_STATUSES:
            row.outcome_at = utcnow()
        elif payload.status == "applied":
            row.outcome_at = None      # 改返「已投遞」= 結果未定
    if payload.interview_stage is not None:
        row.interview_stage = payload.interview_stage
    if payload.notes is not None:
        row.notes = payload.notes
    db.commit()
    db.refresh(row)
    _attach_cv_variant([row])
    return row
