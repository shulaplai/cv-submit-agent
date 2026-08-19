"""JD language detection: heuristic (CJK char ratio) + optional LLM review."""
from __future__ import annotations

import re

_CJK_RE = re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf]")
_HANZI_COUNT_RE = re.compile(r"[\u4e00-\u9fff]")


def detect_language(text: str) -> str:
    """Return 'zh' if the text is predominantly Chinese, else 'en'.

    Uses ratio of CJK chars to total letters; handles mixed JDs by majority.
    """
    if not text:
        return "en"
    sample = text[:4000]
    cjk = len(_CJK_RE.findall(sample))
    letters = len(re.findall(r"[A-Za-z\u4e00-\u9fff]", sample))
    if letters == 0:
        return "zh" if cjk > 0 else "en"
    return "zh" if cjk / letters > 0.35 else "en"
