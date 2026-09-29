"""Match scoring: cheap keyword pre-score, then LLM match score with fallback.

評分會考慮求職者嘅**年資／資歷**（Settings 頁 `years_experience`）：
  - JD 要 5 年+／senior／lead／principal → 扣分，level = "over"（資歷超出）
  - 見習／graduate／junior／0–2 年 → level = "under"
  - 合約／外派／獵頭（EA）→ 扣分（用戶想搵穩定工）
  - IT 支援／helpdesk／data center／solution／pre-sales／BA 等「IT 相鄰穩定」
    職位唔當唔啱
LLM 回唔到 level 就當 ""（唔會亂填）。
"""
from __future__ import annotations

import logging
import re

from . import llm as llm_svc
from .cv_loader import load_skills
from .llm import LLMError
from .tuning import load_tuning

log = logging.getLogger(__name__)

_WORD_RE = re.compile(r"[a-z0-9+#.]+")

# LLM 失敗時嘅關鍵字 fallback：AI 相關職位加最多 AI_BONUS_MAX 分
# （用戶重點搵 AI 工，唔應該因為技能清單未有 AI 詞就一律 0 分沉底）
AI_BONUS_KEYWORDS = (
    "ai", "agent", "llm", "rag", "machine learning", "deep learning", "genai",
    "nlp", "prompt", "computer vision", "人工智能", "機器學習", "深度學習",
    "大模型", "演算法", "算法", "自然語言",
)
AI_BONUS_MAX = 10

VALID_LEVELS = ("under", "fit", "over")


def _tokens(text: str) -> set[str]:
    return set(_WORD_RE.findall((text or "").lower()))


def _ai_bonus(title: str, extra_text: str) -> int:
    hay = f"{title} {extra_text}".lower()
    if not hay.strip():
        return 0
    hits = sum(1 for kw in AI_BONUS_KEYWORDS if kw in hay)
    if not hits:
        return 0
    return min(AI_BONUS_MAX, 4 + (hits - 1) * 3)


def keyword_score(title: str, extra_text: str, skills: list[str] | None = None) -> int:
    """0-100 heuristic overlap between applicant skills and job text.

    冇技能清單 = 中性 50 分（交俾 LLM 判斷）；AI 職位另加少量分數（見上）。
    """
    skills = skills if skills is not None else load_skills()
    bonus = _ai_bonus(title, extra_text)
    if not skills:
        return max(0, min(100, 50 + bonus))
    hay = f"{title} {extra_text}".lower()
    hit = 0
    for s in skills:
        s_low = s.lower()
        if len(s_low) <= 2:
            continue
        if s_low in hay or (len(s_low) > 3 and s_low in _tokens(hay)):
            hit += 1
    score = int(round(hit / len(skills) * 100))
    return max(0, min(100, score + bonus))


def _applicant_profile_line() -> str:
    """求職者資歷（Settings 頁）-> prompt 用嘅一句描述。"""
    t = load_tuning()
    bits: list[str] = []
    if t.years_experience:
        bits.append(f"{t.years_experience} 年相關工作經驗")
    if t.prefer_ai:
        bits.append("優先考慮 AI／人工智能相關職位")
    bits.append("想搵穩定嘅長工（唔想合約／外派／獵頭）"
                if (t.avoid_contract or t.avoid_agency)
                else "想搵穩定嘅長工")
    return "求職者背景：" + "；".join(bits) + "。"


def _build_match_messages(job: dict, skills: list[str]) -> list[dict]:
    jd = job.get("jd_text") or job.get("short_desc") or ""
    t = load_tuning()
    years = t.years_experience or 0
    years_line = (f"求職者有大約 {years} 年工作經驗（中級程度，唔係資深）。"
                  if years else "求職者係中級程度（唔係資深）。")
    return [
        {
            "role": "system",
            "content": (
                "你是一個務實嘅求職匹配分析師。根據求職者嘅技能、年資同職位要求，"
                "判斷呢份工值唔值得申請。\n"
                f"{years_line}\n"
                "評分規則：\n"
                "1) 職位要求明顯超出求職者年資（例如要 5 年或以上、senior／lead／"
                "principal／主管／資深）→ 扣分，level 填 \"over\"。\n"
                "2) 職位係見習／graduate／junior／要求 0–2 年，而求職者經驗更多 → "
                "level 填 \"under\"。\n"
                "3) 大致對等 → level 填 \"fit\"。\n"
                "4) 合約／臨時／兼職／外派／獵頭（EA）職位要扣分（求職者想搵穩定長工）。\n"
                "5) IT 支援／helpdesk／data center／技術支援／solution／pre-sales／"
                "business analyst 等 IT 相鄰職位唔好當唔啱。\n"
                "輸出嚴格 JSON：{\"score\": 0-100 整數, \"reason\": 一句廣東話/中文解釋"
                "點解啱或唔啱, \"level\": \"under\" 或 \"fit\" 或 \"over\"}。"
                "score 要高過 65 先算值得申請。"
            ),
        },
        {
            "role": "user",
            "content": (
                f"{_applicant_profile_line()}\n"
                f"求職者技能：{', '.join(skills)}\n\n"
                f"職位：{job.get('title', '')}\n公司：{job.get('company', '')}\n"
                f"地點：{job.get('location', '')}\n薪酬：{job.get('salary_range', '')}\n\n"
                f"職位描述：\n{jd[:3500]}"
            ),
        },
    ]


def _normalize_level(value) -> str:
    level = str(value or "").strip().lower()
    return level if level in VALID_LEVELS else ""


async def llm_match_score(job: dict, skills: list[str] | None = None) -> tuple[int, str, str]:
    """(score, reason, level)。LLM 失敗 -> 關鍵字 fallback（level 留空）。"""
    skills = skills if skills is not None else load_skills()
    try:
        data = await llm_svc.chat_json(_build_match_messages(job, skills))
        score = int(data.get("score", 50))
        score = max(0, min(100, score))
        reason = str(data.get("reason", "")).strip()
        return score, reason, _normalize_level(data.get("level"))
    except LLMError as e:
        log.warning("llm match failed: %s", e)
        fallback = keyword_score(job.get("title", ""), job.get("jd_text", ""), skills)
        return fallback, "（LLM 失敗，用關鍵字計分）", ""
    except (TypeError, ValueError) as e:
        log.warning("llm match returned garbage: %s", e)
        fallback = keyword_score(job.get("title", ""), job.get("jd_text", ""), skills)
        return fallback, "（LLM 回覆格式唔啱，用關鍵字計分）", ""


async def score_job(job: dict, skills: list[str] | None = None) -> tuple[int, str, str]:
    """LLM score when we have a JD; otherwise keyword pre-score.

    Returns (score, reason, level)；冇 JD 時 level 留空。
    """
    skills = skills if skills is not None else load_skills()
    if not job.get("jd_text"):
        pre = keyword_score(job.get("title", ""), job.get("short_desc", ""), skills)
        return pre, "", ""
    return await llm_match_score(job, skills)


async def summarize_job(job: dict) -> str:
    """AI one-glance job summary in Traditional Chinese for UI display.

    Covers what the role is, core requirements, salary and how to apply.
    """
    jd = (job.get("jd_text") or job.get("short_desc") or "")[:3000]
    messages = [
        {
            "role": "system",
            "content": (
                "你係招聘分析師。用繁體中文寫一段 80–130 字嘅職位摘要，俾求職者一眼睇明："
                "①做咩 ②核心要求 ③薪酬/待遇 ④點申請（email 或平台）。"
                "直接輸出摘要文字，唔好加標題、唔好加 bullet 符號。"
            ),
        },
        {
            "role": "user",
            "content": (
                f"職位：{job.get('title', '')}\n公司：{job.get('company', '')}\n"
                f"地點：{job.get('location', '')}\n薪酬：{job.get('salary_range', '')}\n"
                f"申請方式：{job.get('apply_method', '')}"
                f"{('（email: ' + job['contact_email'] + '）') if job.get('contact_email') else ''}\n\n"
                f"職位描述：\n{jd}"
            ),
        },
    ]
    return (await llm_svc.chat(messages, temperature=0.3)).strip()
