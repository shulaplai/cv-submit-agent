"""Profile (onboarding) endpoints — single row id=1."""
import logging

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy.orm import Session

from ..config import settings
from ..db import get_db
from ..models import Profile
from ..schemas import ProfileIn, ProfileOut
from ..services import llm as llm_svc
from ..services.cv_loader import CVError, get_cv_text

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/profile", tags=["profile"])


@router.get("", response_model=ProfileOut)
def get_profile(db: Session = Depends(get_db)):
    profile = db.get(Profile, 1)
    if profile is None:
        profile = Profile(
            id=1, name=settings.APPLICANT_NAME, email=settings.APPLICANT_EMAIL,
            cv_en_path=settings.CV_EN_PATH, cv_zh_path=settings.CV_ZH_PATH,
            gba_age_under_29=settings.GBA_AGE_UNDER_29,
            gba_edu_associate_degree=settings.GBA_EDU_ASSOCIATE_DEGREE,
            it_track_enabled=settings.IT_TRACK_ENABLED,
            general_track_enabled=settings.GENERAL_TRACK_ENABLED,
            govhk_it_max_jobs=settings.GOVHK_IT_MAX_JOBS,
            govhk_general_max_jobs=settings.GOVHK_GENERAL_MAX_JOBS,
            offertoday_it_max_per_search=settings.OFFERTODAY_MAX_PER_SEARCH,
            offertoday_general_max_per_search=settings.OFFERTODAY_GENERAL_MAX_PER_SEARCH,
        )
        db.add(profile)
        db.commit()
        db.refresh(profile)
    return profile


@router.put("", response_model=ProfileOut)
def update_profile(payload: ProfileIn, db: Session = Depends(get_db)):
    profile = db.get(Profile, 1)
    if profile is None:
        profile = Profile(id=1)
        db.add(profile)
    changed = payload.model_dump(exclude_unset=True)
    for field, value in changed.items():
        setattr(profile, field, value)
    db.commit()
    db.refresh(profile)
    # 掃描時間／間隔改咗 -> 即時重建排程（唔使改 .env 重啟）
    if {"scan_hour", "scan_day_interval"} & set(changed):
        try:
            from ..main import reschedule

            reschedule()
        except Exception as e:  # noqa: BLE001 — 排程重建失敗唔應該令儲存失敗
            log.warning("reschedule after profile update failed: %s", e)
    return profile


@router.get("/tuning")
def get_tuning(db: Session = Depends(get_db)):
    """有效掃描／潤色設定（Settings 頁顯示「實際生效」值同估算用）。

    `explicit` = 你喺設定頁明確填過嘅欄位；其餘係 .env 預設。
    """
    from ..services.tuning import ai_search_terms, load_tuning, priority_keywords
    from ..services.cv_loader import ai_title_keywords
    from ..services.classify import builtin_block_config

    sales_kws, industry_kws = builtin_block_config()

    t = load_tuning(db)
    profile = db.get(Profile, 1)
    ttl = profile.cv_ai_title_keywords if profile else ""
    return {
        "max_scan_jobs": t.max_scan_jobs,
        "offertoday_it_max_searches": t.offertoday_it_max_searches,
        "offertoday_general_max_searches": t.offertoday_general_max_searches,
        "cap_bypass_enabled": t.cap_bypass_enabled,
        "cap_bypass_min_score": t.cap_bypass_min_score,
        "priority_extra_max": t.priority_extra_max,
        "priority_keywords_effective": priority_keywords(t),
        "max_enrich_per_scan": t.max_enrich_per_scan,
        "enrich_all_it": t.enrich_all_it,
        "max_enrich_it_per_scan": t.max_enrich_it_per_scan,
        "enrich_general_jobs": t.enrich_general_jobs,
        "ai_search_terms": ai_search_terms(t),
        "ai_search_max_searches": t.ai_search_max_searches,
        "ai_search_max_age_days": t.ai_search_max_age_days,
        "ai_stale_action": t.ai_stale_action,
        "ai_title_keywords": ai_title_keywords(),
        "blocked_sales_keywords": sales_kws,
        "blocked_industry_keywords": industry_kws,
        "ai_check_enabled": t.ai_check_enabled,
        "ai_check_batch_size": t.ai_check_batch_size,
        "ai_check_limit": t.ai_check_limit,
        "ai_check_after_scan": t.ai_check_after_scan,
        "scan_job_delay_min": t.scan_job_delay_min,
        "scan_job_delay_max": t.scan_job_delay_max,
        "scan_hour": t.scan_hour,
        "scan_day_interval": t.scan_day_interval,
        "email_polish_enabled": t.email_polish_enabled,
        "intro_polish_enabled": t.intro_polish_enabled,
        "years_experience": t.years_experience,
        "ai_title_keywords_default": ai_title_keywords() if not ttl else ai_title_keywords(),
    }


@router.post("/cv", response_model=ProfileOut)
async def upload_cv(kind: str = Form(...), file: UploadFile = File(...),
                    db: Session = Depends(get_db)):
    """Pick a CV via the browser file dialog: store it under data/cvs/ and set
    the profile path — the user never types a path manually."""
    # kind: en | zh（通用版）+ 版本版：ai_en / ai_zh / fullstack_en / fullstack_zh /
    # developer_en / developer_zh（申請時按職位揀版本）
    allowed = ("en", "zh") + tuple(f"{v}_{lang}" for v in ("ai", "fullstack", "developer")
                                   for lang in ("en", "zh"))
    if kind not in allowed:
        raise HTTPException(status_code=400, detail=f"kind 必須係 {' / '.join(allowed)}")
    if not (file.filename or "").lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="暫時只支援 PDF（pypdf 讀取）")
    cvs_dir = settings.DATA_DIR / "cvs"
    cvs_dir.mkdir(parents=True, exist_ok=True)
    dest = cvs_dir / f"cv_{kind}.pdf"
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="空檔案")
    dest.write_bytes(content)

    profile = db.get(Profile, 1)
    if profile is None:
        profile = Profile(id=1)
        db.add(profile)
    setattr(profile, f"cv_{kind}_path", str(dest))
    db.commit()
    db.refresh(profile)
    return profile


@router.get("/smtp-status")
def smtp_status(db: Session = Depends(get_db)):
    """SMTP 設定狀態（唔會連線）：UI 顯示「會唔會自動寄得出」。"""
    from ..services.email_bot import smtp_config, smtp_ready

    cfg = smtp_config()
    safe = {k: v for k, v in cfg.items() if k != "password"}
    return {
        "configured": smtp_ready(cfg),
        "has_password": bool(cfg["password"]),
        "method": cfg["method"],
        "config": safe,
    }


@router.post("/test-smtp")
def test_smtp_endpoint(db: Session = Depends(get_db)):
    """連線 + 登入測試（唔會寄信）。設定頁「測試 SMTP」用。"""
    from ..services.email_bot import test_smtp

    return test_smtp()


@router.post("/send-test-email")
def send_test_email(payload: dict | None = None,
                    db: Session = Depends(get_db)):
    """寄一封測試信去自己（SMTP_FROM_EMAIL）—— 真係試「自動寄出」呢條路。"""
    from ..services.email_bot import send_email_smtp, smtp_config

    cfg = smtp_config()
    to = (payload or {}).get("to") or cfg.get("from_email") or cfg.get("user")
    if not to:
        raise HTTPException(status_code=400, detail="未填寄件人（SMTP 帳號）")
    email = {
        "to": to,
        "subject": "[cv-submit] SMTP 測試 — 自動寄信",
        "body": "呢封係 CV Submit Agent 嘅 SMTP 測試信。\n\n"
                "如果你收到，即係「自動寄出 email 申請」呢條路已經通 —— "
                "之後投 gov.hk 工會自動寄出（內文 + CV 附件），唔需要 macOS Mail 權限。",
        "attachment": "",
    }
    ok, note = send_email_smtp(email, cfg)
    return {"ok": ok, "to": to, "note": note}


@router.post("/test-llm")
async def test_llm():
    """Ping the configured LLM (DB key overrides .env). Returns {ok, latency_ms, model, error}."""
    return await llm_svc.test_connection()


@router.post("/extract-skills")
async def extract_skills():
    """Ask the LLM to extract a skills list from the English CV (facts only)."""
    try:
        cv_text = get_cv_text("en")
    except CVError as e:
        raise HTTPException(status_code=400, detail=f"讀唔到 CV：{e}")
    messages = [
        {
            "role": "system",
            "content": (
                "從求職者履歷中抽取技能清單。只可以用履歷出現過嘅技能，"
                "唔好加冇出現嘅。輸出嚴格 JSON：{\"skills\": [\"Skill A\", \"Skill B\"]}，最多 20 項，"
                "用英文輸出技能名。"
            ),
        },
        {"role": "user", "content": cv_text[:5000]},
    ]
    try:
        data = await llm_svc.chat_json(messages)
        skills = data.get("skills", [])
        if not isinstance(skills, list):
            skills = []
        return {"skills": [str(s).strip() for s in skills if str(s).strip()][:20]}
    except llm_svc.LLMError as e:
        raise HTTPException(status_code=502, detail=f"抽取失敗：{e}")


@router.post("/generate-intro")
async def generate_intro(lang: str = Form("zh")):
    """AI-write the self-intro (zh or en) from the CV + skills. Returns text
    for the user to review/edit before saving (never auto-saves)."""
    if lang not in ("zh", "en"):
        raise HTTPException(status_code=400, detail="lang 必須係 zh 或 en")
    try:
        cv_text = get_cv_text(lang)
    except CVError as e:
        raise HTTPException(status_code=400, detail=f"讀唔到 CV：{e}")
    from ..services.cv_loader import load_skills

    skills = load_skills()
    target = "AI 工程師 / Agent Developer / Full-stack Developer"
    if lang == "zh":
        system = (
            "根據求職者履歷寫一段 60–100 字嘅繁體中文自我簡介，用喺申請信開頭。"
            "語氣專業自信、唔吹噓，只可以用履歷事實。直接輸出簡介文字，唔加稱呼/標題。"
        )
    else:
        system = (
            "Write a 50–90 word English self-introduction for the applicant's cover "
            "letters, based ONLY on CV facts. Professional and confident, no "
            "exaggeration. Output only the introduction text."
        )
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": (
            f"目標職位方向：{target}\n技能：{', '.join(skills) if skills else '（未設定）'}\n\n"
            f"履歷：\n{cv_text[:4000]}"
        )},
    ]
    try:
        text = await llm_svc.chat(messages, temperature=0.7)
        return {"lang": lang, "text": text.strip()}
    except llm_svc.LLMError as e:
        raise HTTPException(status_code=502, detail=f"生成失敗：{e}")


@router.post("/generate-after-cv-intro")
async def generate_after_cv_intro(lang: str = Form("zh"), topic: str = Form("it")):
    """AI-write the ~100-char self-intro sent AFTER the CV (OfferToday).

    topic: 'ai' (AI Agent) / 'it' (IT/programming) / 'general'.
    Returns text for review/editing.
    """
    if lang not in ("zh", "en"):
        raise HTTPException(status_code=400, detail="lang 必須係 zh 或 en")
    if topic not in ("ai", "it", "general"):
        raise HTTPException(status_code=400, detail="topic 必須係 ai、it 或 general")
    from ..services.apply_bot import generate_after_cv_intro as _gen

    try:
        text = await _gen(lang, topic)
        return {"lang": lang, "topic": topic, "text": text}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"生成失敗：{e}")
