"""發送前 AI 潤色（用戶要求）：Email 內文 同 OfferToday 自我介紹。

兩個用途都係「最後一執」：令封信／自我介紹讀落自然，唔似 AI 拼砌。

鐵律（寫入 prompt + 輸出後檢查，唔過就用原文）：
  1. 唔可以新增履歷以外嘅事實、唔可以改公司名／職位名
  2. 第一行稱呼同最後簽名（姓名／email）逐字保留
  3. 唔可以刪走重要內容（字數 -25% ~ +20%）；刪重複就得
  4. 語言要一致（中文 = 繁體書面語、唔好廣東話口語；英文 = 純英文）
  5. 唔可以出現「AI／助手／潤色」等字眼

失敗（LLM 出錯／檢查唔過）一律 raise ``PolishError``，呼叫方用原文，
所以永遠唔會因為潤色而漏寄或者寄空氣。
"""
from __future__ import annotations

import logging
import re

from . import llm as llm_svc
from .llm import LLMError

log = logging.getLogger(__name__)

# 自我介紹硬上限（OfferToday 訊息框會截 1000 字，留大量空間）
INTRO_MAX_CHARS = 280
INTRO_MIN_CHARS = 30

_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
_LETTER_RE = re.compile(r"[A-Za-z\u4e00-\u9fff]")
_FORBIDDEN_RE = re.compile(r"(AI 助手|AI助手|潤色|polish(ed)? by|as an ai)", re.IGNORECASE)
_GREETING_TAIL = (",", "，", ":", "：")


class PolishError(RuntimeError):
    """潤色輸出唔合格（格式／語言／長度／事實風險）—— 用原文。"""


def _cjk_ratio(text: str) -> float:
    cjk = len(_CJK_RE.findall(text or ""))
    letters = len(_LETTER_RE.findall(text or ""))
    return cjk / letters if letters else 0.0


def _first_line(text: str) -> str:
    for line in (text or "").splitlines():
        if line.strip():
            return line.strip()
    return ""


def _last_line(text: str) -> str:
    for line in reversed((text or "").splitlines()):
        if line.strip():
            return line.strip()
    return ""


def _looks_like_greeting(line: str) -> bool:
    return line.endswith(_GREETING_TAIL) and len(line) <= 60


def _tail_block(text: str, n: int = 3) -> str:
    lines = [ln for ln in (text or "").splitlines() if ln.strip()]
    return "\n".join(lines[-n:])


# --------------------------------------------------------------------- email

def _email_system(lang: str, instructions: str = "") -> str:
    extra = f"\n用戶額外要求：{instructions}" if instructions.strip() else ""
    if lang == "zh":
        return (
            "你係一位專業嘅香港求職信編輯。用戶會俾你一封已經寫好嘅求職 email 內文"
            "（包括稱呼、自我介紹、求職信段落、結尾同簽名）。請將佢潤飾到讀落自然、"
            "專業、似人手寫，唔似 AI 拼砌。\n"
            "你可以：順句、改措辭、刪走重複內容（自我介紹同求職信成日重複講同一件事）、"
            "將廣東話口語改成繁體書面語（例如「貴公司嘅回覆」→「貴公司的回覆」、"
            "「唔會」→「不會」）。\n"
            "鐵律：\n"
            "1) 只可以用原有內容嘅事實，絕對唔准新增任何履歷以外嘅資料、唔准誇大；\n"
            "2) 第一行稱呼（例如「陳先生 您好，」／「致招聘經理／人事部：」）同最後"
            "簽名（姓名、email）要逐字保留，唔准改；\n"
            "3) 唔可以刪走重要內容（可以刪重複句）；總長度同原文相若（-25% 至 +20%）；\n"
            "4) 用繁體中文、書面語，唔好中英夾雜，唔好出現 emoji；\n"
            "5) 唔准提「AI」或「潤色」等字眼。\n"
            "直接輸出潤飾後嘅完整 email 內文，唔好加任何解釋、標題或者 markdown。"
            + extra
        )
    return (
        "You are a professional editor for job-application emails. The user gives "
        "you a finished email body (greeting, self-introduction, cover-letter "
        "paragraphs, closing and signature). Make it read naturally and "
        "professionally, like a human wrote it — not assembled by AI.\n"
        "You may: smooth sentences, rephrase, drop duplicated content (the intro "
        "and cover letter often repeat the same point).\n"
        "IRON RULES:\n"
        "1) Use only facts already present — never add CV facts, never exaggerate;\n"
        "2) Keep the FIRST line (the greeting) and the FINAL signature lines "
        "(name, email) word-for-word unchanged;\n"
        "3) Do not drop important content (dedupe is fine); total length must stay "
        "similar to the original (-25% to +20%);\n"
        "4) Output only the polished email body — no explanation, heading or "
        "markdown; no emoji; never mention AI or polishing.\n"
        + extra
    )


def _email_user(lang: str, body: str, ctx: dict | None) -> str:
    ctx = ctx or {}
    head = ""
    if ctx.get("title") or ctx.get("company"):
        head = (f"職位：{ctx.get('title', '')}｜公司：{ctx.get('company', '')}\n\n"
                if lang == "zh"
                else f"Role: {ctx.get('title', '')} | Company: {ctx.get('company', '')}\n\n")
    tail = "請潤飾以下 email 內文並輸出完整版本：" if lang == "zh" \
        else "Polish the email body below and output the full version:"
    return f"{head}{tail}\n\n{body[:6000]}"


def validate_email_polish(original: str, polished: str, lang: str) -> list[str]:
    """回傳問題清單（空 = 通過）。"""
    problems: list[str] = []
    op, pp = (original or "").strip(), (polished or "").strip()
    if not pp:
        return ["輸出係空"]
    if len(op) < 40:
        return []          # 太短嘅原文唔值得做嚴格檢查
    if _FORBIDDEN_RE.search(pp):
        problems.append("提到 AI／潤色字眼")
    ratio = len(pp) / max(1, len(op))
    if ratio < 0.75:
        problems.append(f"刪得太多（原文 {len(op)} 字，輸出 {len(pp)} 字）")
    elif ratio > 1.2:
        problems.append(f"寫得太長（原文 {len(op)} 字，輸出 {len(pp)} 字）")
    if lang == "zh":
        if _cjk_ratio(pp) < 0.35:
            problems.append("語言唔啱：應該係繁體中文")
    else:
        if _cjk_ratio(pp) > 0.4:
            problems.append("語言唔啱：應該係英文")
    orig_greet = _first_line(op)
    if orig_greet and _looks_like_greeting(orig_greet) and _first_line(pp) != orig_greet:
        problems.append("第一行稱呼被改咗")
    orig_tail = _tail_block(op)
    for token in _signature_tokens(op):
        if token not in pp:
            problems.append(f"簽名／署名唔見咗：{token}")
            break
    if orig_tail and not pp.endswith(_last_line(op)) and _last_line(op) not in pp:
        # 最後一行（通常係簽名）唔見咗
        problems.append("最後一行（簽名）唔見咗")
    return problems


def _signature_tokens(body: str) -> list[str]:
    """原文最後幾行入面嘅 email／姓名（要喺輸出保留）。"""
    tail = _tail_block(body)
    tokens: list[str] = []
    for m in re.finditer(r"[\w.+-]+@[\w-]+\.[\w.]+", tail):
        tokens.append(m.group(0))
    return tokens


async def polish_email_body(body: str, lang: str, ctx: dict | None = None,
                            instructions: str = "") -> str:
    """潤色整封 email 內文。檢查唔過 / LLM 失敗 -> raise PolishError。"""
    text = (body or "").strip()
    if not text:
        raise PolishError("冇內容可以潤色")
    messages = [
        {"role": "system", "content": _email_system(lang, instructions)},
        {"role": "user", "content": _email_user(lang, text, ctx)},
    ]
    try:
        polished = (await llm_svc.chat(messages, temperature=0.3)).strip()
    except LLMError as e:
        raise PolishError(str(e)) from e
    problems = validate_email_polish(text, polished, lang)
    if problems:
        raise PolishError("；".join(problems))
    return polished


# --------------------------------------------------------------------- intro

def _intro_system(lang: str, instructions: str = "") -> str:
    extra = f"\n用戶額外要求：{instructions}" if instructions.strip() else ""
    if lang == "zh":
        return (
            "你係求職者嘅助手。下面係一段用喺求職平台（OfferToday）發完 CV 之後"
            "跟住送出嘅自我介紹。請潤飾到讀落自然、似人手寫、專業自信，"
            "唔似 AI 生成。\n"
            "鐵律：只可以改措辭同刪重複，唔准新增履歷以外嘅事實、唔准誇大；"
            "保持 80–120 字繁體中文書面語；唔加稱呼、唔加標題、唔加 emoji、"
            "唔好分 bullet；唔准提「AI」或「潤色」。直接輸出潤飾後嘅文字。"
            + extra
        )
    return (
        "You help an applicant polish a short self-introduction that is sent "
        "right after their CV on a job platform. Make it read naturally, like a "
        "human wrote it — professional and confident, not AI-generated.\n"
        "IRON RULES: only rephrase and dedupe; never add facts that are not in "
        "the CV, never exaggerate; keep it 60–110 words in English; no greeting, "
        "no heading, no emoji, no bullets; never mention AI or polishing. "
        "Output only the polished text."
        + extra
    )


def validate_intro_polish(polished: str, lang: str) -> list[str]:
    problems: list[str] = []
    text = (polished or "").strip()
    if not text:
        return ["輸出係空"]
    if len(text) > INTRO_MAX_CHARS:
        problems.append(f"太長（{len(text)} 字，上限 {INTRO_MAX_CHARS}）")
    if len(text) < INTRO_MIN_CHARS:
        problems.append(f"太短（{len(text)} 字）")
    if _FORBIDDEN_RE.search(text):
        problems.append("提到 AI／潤色字眼")
    # 科技人嘅自我介紹一定有 React／Docker／API 等英文字，所以門檻唔可以太高
    # （0.3 已經足夠擋住「成段英文」嘅輸出）
    if lang == "zh" and _cjk_ratio(text) < 0.3:
        problems.append("語言唔啱：應該係繁體中文")
    if lang != "zh" and _cjk_ratio(text) > 0.3:
        problems.append("語言唔啱：應該係英文")
    return problems


async def polish_intro(text: str, lang: str, topic: str = "general",
                       title: str = "", instructions: str = "") -> str:
    """潤色 OfferToday 自我介紹。檢查唔過 / LLM 失敗 -> raise PolishError。"""
    original = (text or "").strip()
    if not original:
        raise PolishError("冇自我介紹可以潤色")
    angle = {"ai": "AI Agent / 大型語言模型應用",
             "it": "IT / 程式開發",
             "general": "一般專業"}.get(topic, "一般專業")
    if lang == "zh":
        user = (f"求職方向：{angle}\n職位：{title or '（未指定）'}\n\n"
                f"現有自我介紹：\n{original[:1200]}\n\n請潤飾並輸出完整文字。")
    else:
        user = (f"Target: {angle}\nRole: {title or '(unspecified)'}\n\n"
                f"Current introduction:\n{original[:1200]}\n\n"
                "Polish it and output the full text.")
    messages = [
        {"role": "system", "content": _intro_system(lang, instructions)},
        {"role": "user", "content": user},
    ]
    try:
        polished = (await llm_svc.chat(messages, temperature=0.3)).strip()
    except LLMError as e:
        raise PolishError(str(e)) from e
    problems = validate_intro_polish(polished, lang)
    if problems:
        raise PolishError("；".join(problems))
    return polished
