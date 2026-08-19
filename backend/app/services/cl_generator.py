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


# ------------------------------------------------------------------ quality

def _cjk_ratio(text: str) -> float:
    import re

    cjk = len(re.findall(r"[\u4e00-\u9fff]", text))
    letters = len(re.findall(r"[A-Za-z\u4e00-\u9fff]", text))
    return cjk / letters if letters else (1.0 if cjk else 0.0)


def validate_cl(content: str, language: str) -> list[str]:
    """Return a list of quality problems (empty = OK)."""
    import re

    problems: list[str] = []
    if language == "zh":
        if _cjk_ratio(content) < 0.3:
            problems.append("語言唔啱：應該係繁體中文")
        chars = len(re.findall(r"[\u4e00-\u9fff]", content))
        if chars < 150:
            problems.append(f"太短（約 {chars} 個中文字）")
        elif chars > 900:
            problems.append(f"太長（約 {chars} 個中文字）")
    else:
        if _cjk_ratio(content) > 0.4:
            problems.append("語言唔啱：應該係英文")
        words = len(content.split())
        if words < 100:
            problems.append(f"太短（{words} 字）")
        elif words > 600:
            problems.append(f"太長（{words} 字）")
    return problems


async def generate_cl_checked(cv_text: str, jd: str, job: dict, language: str,
                              instructions: str = "") -> tuple[str, str]:
    """Generate + validate; retries once with a corrective hint on failure.

    Returns (content, warning). Warning is empty on success.
    """
    content = await generate_cl(cv_text, jd, job, language, instructions)
    problems = validate_cl(content, language)
    if not problems:
        return content, ""
    # one corrective retry
    hint = "；".join(problems)
    retry_instr = f"（上次版本問題：{hint}。請修正後重新輸出完整求職信。）"
    retry = await generate_cl(cv_text, jd, job, language,
                              f"{instructions} {retry_instr}".strip())
    problems2 = validate_cl(retry, language)
    if not problems2 or len(retry) >= len(content):
        return retry, ("" if not problems2 else f"CL 質素提示：{'；'.join(problems2)}")
    return content, f"CL 質素提示：{'；'.join(problems)}"
