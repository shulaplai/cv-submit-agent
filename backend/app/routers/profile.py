"""Profile (onboarding) endpoints — single row id=1."""
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy.orm import Session

from ..config import settings
from ..db import get_db
from ..models import Profile
from ..schemas import ProfileIn, ProfileOut
from ..services import llm as llm_svc
from ..services.cv_loader import CVError, get_cv_text

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
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(profile, field, value)
    db.commit()
    db.refresh(profile)
    return profile


@router.post("/cv", response_model=ProfileOut)
async def upload_cv(kind: str = Form(...), file: UploadFile = File(...),
                    db: Session = Depends(get_db)):
    """Pick a CV via the browser file dialog: store it under data/cvs/ and set
    the profile path — the user never types a path manually."""
    if kind not in ("en", "zh"):
        raise HTTPException(status_code=400, detail="kind 必須係 en 或 zh")
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

    topic: 'it' (IT/programming) or 'general'. Returns text for review/editing.
    """
    if lang not in ("zh", "en"):
        raise HTTPException(status_code=400, detail="lang 必須係 zh 或 en")
    if topic not in ("it", "general"):
        raise HTTPException(status_code=400, detail="topic 必須係 it 或 general")
    from ..services.apply_bot import generate_after_cv_intro as _gen

    try:
        text = await _gen(lang, topic == "it")
        return {"lang": lang, "topic": topic, "text": text}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"生成失敗：{e}")
