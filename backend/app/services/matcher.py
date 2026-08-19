"""Match scoring: cheap keyword pre-score, then LLM match score with fallback."""
from __future__ import annotations

import logging
import re

from . import llm as llm_svc
from .cv_loader import load_skills
from .llm import LLMError

log = logging.getLogger(__name__)

_WORD_RE = re.compile(r"[a-z0-9+#.]+")


def _tokens(text: str) -> set[str]:
    return set(_WORD_RE.findall((text or "").lower()))


def keyword_score(title: str, extra_text: str, skills: list[str] | None = None) -> int:
    """0-100 heuristic overlap between applicant skills and job text."""
    skills = skills if skills is not None else load_skills()
    if not skills:
        return 50  # unknown skills -> neutral, let LLM decide
    hay = f"{title} {extra_text}".lower()
    hit = 0
    for s in skills:
        s_low = s.lower()
        if len(s_low) <= 2:
            continue
        if s_low in hay or (len(s_low) > 3 and s_low in _tokens(hay)):
            hit += 1
    if not skills:
        return 50
    score = int(round(hit / len(skills) * 100))
    return max(0, min(100, score))


def _build_match_messages(job: dict, skills: list[str]) -> list[dict]:
    jd = job.get("jd_text") or job.get("short_desc") or ""
    return [
        {
            "role": "system",
            "content": (
                "你是一個務實嘅求職匹配分析師。根據求職者嘅技能同職位要求，"
                "判斷呢份工值唔值得申請。輸出嚴格 JSON：{\"score\": 0-100 整數, "
                "\"reason\": 一句廣東話/中文解釋點解啱或唔啱}。score 要高過 65 先算值得申請。"
            ),
        },
        {
            "role": "user",
            "content": (
                f"求職者技能：{', '.join(skills)}\n\n"
                f"職位：{job.get('title', '')}\n公司：{job.get('company', '')}\n"
                f"地點：{job.get('location', '')}\n薪酬：{job.get('salary_range', '')}\n\n"
                f"職位描述：\n{jd[:3500]}"
            ),
        },
    ]


async def llm_match_score(job: dict, skills: list[str] | None = None) -> tuple[int, str]:
    skills = skills if skills is not None else load_skills()
    try:
        data = await llm_svc.chat_json(_build_match_messages(job, skills))
        score = int(data.get("score", 50))
        score = max(0, min(100, score))
        reason = str(data.get("reason", "")).strip()
        return score, reason
    except LLMError as e:
        log.warning("llm match failed: %s", e)
        fallback = keyword_score(job.get("title", ""), job.get("jd_text", ""), skills)
        return fallback, "（LLM 失敗，用關鍵字計分）"


async def score_job(job: dict, skills: list[str] | None = None) -> tuple[int, str]:
    """LLM score when we have a JD; otherwise keyword pre-score."""
    skills = skills if skills is not None else load_skills()
    if not job.get("jd_text"):
        pre = keyword_score(job.get("title", ""), job.get("short_desc", ""), skills)
        return pre, ""
    return await llm_match_score(job, skills)
