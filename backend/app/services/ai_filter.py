"""批量 AI 檢查：用 LLM **分批**判斷職位係唔係真正 IT／AI 工。

點解要：關鍵字規則（保險／地產封鎖清單、IT／非 IT 字眼）永遠有漏網之魚
（例：「網路銷售代理」有『網路』、非電腦嘅「工程師」）。逐一叫 LLM 太貴，
所以每次攞 **40 份**（標題 + JD 頭 200 字）做一次 call，回一個 JSON array。

成本設計（用戶要求「加埋個批量 AI 檢查」）：
  - 只檢查 **IT 軌**、未檢查過、未投遞嘅工（一般工唔洗錢，跟現有設定）
  - 每次跑最多 `limit` 份（預設 200），一個 batch 一個 call
  - 判為 non_it -> 標低匹配（職位台預設隱藏，但可以喺「低匹配」睇返／還原）
  - 判為 it_ai -> 順手補 ai_match（標題冇 AI 但其實係 AI 工嘅情況）
  - 結果全部入 DB（`ai_verdict` / `ai_verdict_reason` / `ai_checked_at`）可審核
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from ..config import settings
from ..models import JobApplication, utcnow
from . import llm as llm_svc
from .llm import LLMError

log = logging.getLogger(__name__)

DEFAULT_BATCH_SIZE = 40
DEFAULT_LIMIT = 200
JD_HEAD_CHARS = 200

VERDICT_IT_AI = "it_ai"
VERDICT_IT = "it"
VERDICT_NON_IT = "non_it"
VALID_VERDICTS = (VERDICT_IT_AI, VERDICT_IT, VERDICT_NON_IT)

NON_IT_REASON_PREFIX = "AI 檢查：判定唔係 IT 工"


@dataclass
class AiCheckResult:
    checked: int = 0
    batches: int = 0
    non_it: int = 0
    it_ai: int = 0
    it: int = 0
    failed_batches: int = 0
    errors: list[str] = field(default_factory=list)
    marked_ids: list[int] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "checked": self.checked,
            "batches": self.batches,
            "non_it": self.non_it,
            "it_ai": self.it_ai,
            "it": self.it,
            "failed_batches": self.failed_batches,
            "errors": self.errors[:5],
            "marked_ids": self.marked_ids[:200],
        }


def _system_prompt() -> str:
    return (
        "你係招聘資料分類員。用戶係香港求職者，只想要**真正嘅 IT／科技職位**"
        "（軟件開發、程式、系統、網絡、數據、IT 支援、AI／agent 開發…）。\n"
        "逐一判斷每個職位，輸出嚴格 JSON：\n"
        '{"results":[{"id": <同輸入一樣嘅 id>, "verdict": "it_ai" | "it" | "non_it", '
        '"reason": "一句中文解釋"}]}\n'
        "判斷規則：\n"
        "- it_ai：職位本身同 AI／agent／LLM／機器學習／數據科學有關\n"
        "- it：其他真正嘅 IT／科技職位\n"
        "- non_it：**唔係** IT 職位。包括：保險／地產／sales agent、前線銷售、"
        "客戶服務、文職／行政、非電腦嘅工程（機械／土木／電機／工業／生產）、"
        "倉務物流、門市零售、司機、保安…\n"
        "- 只可以按輸入內容判斷，唔肯定就當 it（寧願留返俾用戶自己睇）\n"
        "- results 一定要包含**每一個**輸入 id，唔可以漏"
    )


def _user_prompt(jobs: list[dict]) -> str:
    lines = []
    for job in jobs:
        jd = (job.get("jd_text") or "")[:JD_HEAD_CHARS].replace("\n", " ")
        lines.append(
            f'id={job["id"]}｜標題：{job.get("title", "")}｜公司：{job.get("company", "")}'
            f'｜JD 頭段：{jd}'
        )
    return "職位清單：\n" + "\n".join(lines)


async def check_batch(jobs: list[dict]) -> dict[int, tuple[str, str]]:
    """一個 batch -> {id: (verdict, reason)}。LLM 失敗 -> raise LLMError。"""
    if not jobs:
        return {}
    data = await llm_svc.chat_json([
        {"role": "system", "content": _system_prompt()},
        {"role": "user", "content": _user_prompt(jobs)},
    ])
    out: dict[int, tuple[str, str]] = {}
    for item in data.get("results") or []:
        try:
            jid = int(item.get("id"))
        except (TypeError, ValueError):
            continue
        verdict = str(item.get("verdict", "")).strip().lower()
        if verdict not in VALID_VERDICTS:
            continue
        out[jid] = (verdict, str(item.get("reason", "")).strip()[:300])
    return out


def pending_candidates(db: Session, limit: int) -> list[JobApplication]:
    """未做過 AI 檢查嘅 IT 工（未投遞；低匹配都包括，可以翻案）。"""
    return (
        db.query(JobApplication)
        .filter(JobApplication.category == "it",
                JobApplication.ai_verdict == "",
                JobApplication.status.notin_(("applied", "interviewing", "rejected",
                                              "offer", "no_response")))
        .order_by(JobApplication.match_score.desc(), JobApplication.id.desc())
        .limit(max(1, limit))
        .all()
    )


def count_pending(db: Session) -> int:
    return (
        db.query(JobApplication)
        .filter(JobApplication.category == "it",
                JobApplication.ai_verdict == "",
                JobApplication.status.notin_(("applied", "interviewing", "rejected",
                                              "offer", "no_response")))
        .count()
    )


def apply_verdict(row: JobApplication, verdict: str, reason: str) -> None:
    """將判定寫入 row（唔 commit，由 caller 一次過 commit）。"""
    row.ai_verdict = verdict
    row.ai_verdict_reason = reason
    row.ai_checked_at = utcnow()
    if verdict == VERDICT_NON_IT:
        if row.status not in ("applied", "interviewing", "rejected", "offer",
                              "no_response"):
            row.status = "low_match"
            row.match_reason = f"{NON_IT_REASON_PREFIX}：{reason}" if reason \
                else NON_IT_REASON_PREFIX
            row.ai_match = False
            row.ai_strength = ""
    elif verdict == VERDICT_IT_AI:
        row.ai_match = True
        if not row.ai_strength:
            row.ai_strength = "jd"
        if row.status == "low_match" and (row.match_reason or "").startswith(
                NON_IT_REASON_PREFIX):
            # 之前誤判 -> 還原
            row.status = "pending_review"
            row.match_reason = ""
    else:  # it
        if row.status == "low_match" and (row.match_reason or "").startswith(
                NON_IT_REASON_PREFIX):
            row.status = "pending_review"
            row.match_reason = ""


async def run_ai_check(db: Session, *, limit: int = DEFAULT_LIMIT,
                       batch_size: int = DEFAULT_BATCH_SIZE,
                       progress: dict | None = None) -> AiCheckResult:
    """分批跑 AI 檢查（每 batch 一個 LLM call）。"""
    result = AiCheckResult()
    rows = pending_candidates(db, limit)
    if not rows:
        return result
    batch_size = max(1, min(200, int(batch_size or DEFAULT_BATCH_SIZE)))
    for start in range(0, len(rows), batch_size):
        chunk = rows[start:start + batch_size]
        payload = [{"id": r.id, "title": r.title or "", "company": r.company or "",
                    "jd_text": r.jd_text or ""} for r in chunk]
        if progress is not None:
            progress.update({"phase": "ai_check", "done": result.checked,
                             "total": len(rows), "batch": result.batches + 1})
        try:
            verdicts = await check_batch(payload)
        except LLMError as e:
            result.failed_batches += 1
            result.errors.append(str(e)[:200])
            log.warning("ai check batch failed: %s", e)
            continue
        result.batches += 1
        for row in chunk:
            hit = verdicts.get(row.id)
            if hit is None:
                continue          # LLM 漏咗呢個 id -> 下次再檢查
            verdict, reason = hit
            apply_verdict(row, verdict, reason)
            result.checked += 1
            if verdict == VERDICT_NON_IT:
                result.non_it += 1
                result.marked_ids.append(row.id)
            elif verdict == VERDICT_IT_AI:
                result.it_ai += 1
            else:
                result.it += 1
        db.commit()
    if progress is not None:
        progress.update({"phase": "done", "done": result.checked, "total": len(rows)})
    return result
