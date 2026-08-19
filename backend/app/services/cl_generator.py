"""Cover letter generation: CV-facts-only, JD-language, versioned by the caller."""
from __future__ import annotations

import logging

from . import llm as llm_svc
from .llm import LLMError

log = logging.getLogger(__name__)


def _cl_system(language: str) -> str:
    if language == "zh":
        return (
            "你係一位專業求職信作家。寫一封繁體中文求職信。"
            "鐵律：只可以用求職者履歷入面出現過嘅事實（技能、經驗、學歷、專案），"
            "絕對唔准虛構、誇大或聲稱冇喺履歷出現過嘅嘢。"
        )
    return (
        "You are a professional cover-letter writer. "
        "IRON RULE: use ONLY facts that appear in the applicant's CV "
        "(skills, experience, education, projects). Never fabricate, exaggerate, "
        "or claim anything not present in the CV."
    )


def _cl_user(language: str, cv_text: str, jd: str, job: dict, instructions: str) -> str:
    if language == "zh":
        return (
            f"求職者履歷（只可用以下內容）：\n{cv_text[:4000]}\n\n"
            f"職位：{job.get('title', '')}\n公司：{job.get('company', '')}\n"
            f"職位描述：\n{jd[:3500]}\n\n"
            "寫一封約 250-300 字嘅繁體中文求職信，三段式：\n"
            "1) 點解我對呢份工有興趣\n2) 具體對應 JD 要求嘅技能/經驗（每項都要有 CV 根據）\n"
            "3) 結尾（可提供聯絡方式）\n"
            "語氣專業、誠實。直接輸出求職信內文，唔好加標題或簽名範本。"
            + (f"\n\n用戶額外要求：{instructions}" if instructions else "")
        )
    return (
        f"Applicant CV (use ONLY this content):\n{cv_text[:4000]}\n\n"
        f"Job: {job.get('title', '')}\nCompany: {job.get('company', '')}\n"
        f"Job description:\n{jd[:3500]}\n\n"
        "Write a 200-300 word English cover letter in three paragraphs:\n"
        "1) why I am interested in this role\n"
        "2) specific skills/experience matching the JD requirements (each backed by the CV)\n"
        "3) closing (contact details allowed)\n"
        "Professional, honest tone. Output only the letter body — no heading or signature block."
        + (f"\n\nUser extra instructions: {instructions}" if instructions else "")
    )


async def generate_cl(cv_text: str, jd: str, job: dict, language: str,
                      instructions: str = "") -> str:
    """Return generated cover letter text. Raises LLMError on total failure."""
    messages = [
        {"role": "system", "content": _cl_system(language)},
        {"role": "user", "content": _cl_user(language, cv_text, jd, job, instructions)},
    ]
    try:
        return (await llm_svc.chat(messages, temperature=0.6)).strip()
    except LLMError:
        raise
