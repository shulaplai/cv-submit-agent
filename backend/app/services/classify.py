"""Shared job-track classification: IT vs 一般 (non-IT).

Every job lands on exactly one of two tracks:
  - ``it``       jobs whose title matches the IT keywords
  - ``general``  everything else that matches the general-track keywords

Keyword resolution (user-adjustable in the Settings page):
  IT       profile.it_keywords / .env JOB_KEYWORDS  UNION built-in defaults
           (user keywords first — they double as the focused search terms;
           the built-ins are the safety net so e.g. "Software Engineer" is
           never classified as non-IT because the user list is narrow)
  general  profile.general_job_keywords -> .env GENERAL_JOB_KEYWORDS -> built-in defaults
           (a whitelist: only jobs matching these are kept on the 一般 page)

Matching: CJK keywords are substring matches; latin keywords are whole-word
(case-insensitive) so "data" does not match "database" and "it" does not
match "its".
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..config import settings

# Union of the keyword sets previously duplicated across the scrapers and
# apply_bot. A title matching any of these is an IT / tech job.
DEFAULT_IT_KEYWORDS = [
    # 注意：呢度**唔可以**放裸 "agent"。用戶實測：一放就會將 Hotline Agent／
    # Property Agent／reservations agent／報關 agent 全部當成 IT 工收（112 份），
    # 因為「agent」在香港招聘市場大多數指「客服／代理／經紀」而唔係 AI agent。
    # 真 AI agent 職位標題一定仲有 AI／開發／工程師／algorithm 等字（照樣命中）。
    "ai", "agentic", "agent developer", "ai agent", "agent builder",
    "agent engineer", "agent architect",
    "developer", "programmer", "programming", "software",
    "solution architect", "software architect", "system architect",
    "data architect", "cloud architect", "架構師", "架构师",
    "engineer", "engineering", "frontend", "backend", "full stack", "full-stack",
    "python", "javascript", "typescript", "java", "node", "react", "sql",
    "database", "devops", "cloud", "llm", "machine learning", "deep learning",
    "ml ", "data ", "algorithm", "qa", "it ", "it support", "system admin",
    "systems admin", "system administrator", "systems administrator", "sysadmin",
    "helpdesk", "help desk", "desktop support", "technical support", "network",
    "資訊科技", "資訊技術", "工程師", "程式", "程序", "程式設計", "系統", "軟件",
    "軟體", "前端", "後端", "人工智能", "技術員", "數據", "數據分析", "大模型",
    "模型", "機器學習", "深度學習", "網絡", "網路", "網絡安全", "雲端", "雲",
    "全棧", "計算機", "計算機科學", "編程", "演算法", "編碼", "科技", "開發",
    "測試", "技術支援", "桌面", "維護",
    # 簡體字版本：OfferToday 好多大陸公司／外包用簡體出標題
    # （例：「创始工程师（agent）」、「数据开发」），唔加就會漏咗一批 IT 工。
    "工程师", "软件", "系统", "技术员", "数据", "数据分析", "机器学习",
    "深度学习", "网络", "网络安全", "云端", "云", "全栈", "计算机",
    "计算机科学", "编程", "算法", "编码", "开发", "测试", "技术支持", "维护",
]

# 「唔似 IT」嘅職位字眼：標題有呢啲就唔會入 IT 軌（除非同時有強 IT 字眼）。
# 用嚟擋住好闊嘅 default 關鍵字（engineer／工程師／技術員／桌面…）造成嘅誤分類，
# 例如 MECHANICAL ENGINEER、桌面排版操作員、工料測量師。
DEFAULT_NON_IT_KEYWORDS = [
    "mechanical", "civil", "structural", "electrical", "electronic", "building services",
    "quantity surveying", "chemical", "production", "manufacturing", "logistics",
    "sales", "marketing", "accounting", "clerk", "driver", "security guard",
    "土木", "結構", "機械", "電機", "電子", "建築", "測量", "化驗", "生產", "製造",
    "品質", "品管", "排版", "影音", "外勤", "保安", "清潔", "司機", "倉務", "物流",
    "銷售", "營業", "市場", "會計", "文員", "接待",
]

# 「肯定係 IT」嘅字眼：就算標題有上面嘅排除字眼，有呢啲都照當 IT
# （例：資訊保安工程師 = IT；機械工程師 = 一般）
STRONG_IT_KEYWORDS = [
    "ai", "developer", "programmer", "software", "python", "javascript", "typescript",
    "java", "react", "node", "sql", "database", "devops", "cloud", "llm", "web",
    "frontend", "backend", "full stack", "full-stack", "machine learning", "deep learning",
    "network", "sysadmin", "system admin", "helpdesk", "help desk", "data center",
    "cyber", "it ", "it support", "資訊", "程式", "編程", "軟件", "軟體", "系統",
    "網絡", "網路", "雲端", "數據", "人工智能", "機器學習", "全棧", "前端", "後端",
    "演算法", "計算機",
    # 簡體字版本（同上：大陸公司標題）
    "工程师", "软件", "系统", "网络", "云端", "数据", "机器学习", "全栈",
    "算法", "计算机", "编程", "开发",
]


# Non-IT (一般) track: office / admin / customer-service style roles the
# applicant might accept as backup. Broadly editable in Settings.
DEFAULT_GENERAL_KEYWORDS = [
    "文員", "行政助理", "辦公室助理", "秘書", "客戶服務", "會計", "助理",
    "營運", "跟單", "採購", "資料輸入", "接待員", "店務", "收銀",
    "clerk", "admin", "assistant", "secretary", "customer service",
    "accounting", "operation", "merchandiser", "purchasing", "receptionist",
]


def parse_keywords(text: str) -> list[str]:
    """Split a comma-separated keyword string into cleaned non-empty tokens."""
    return [k.strip() for k in (text or "").split(",") if k.strip()]


def resolve_it_keywords(profile_text: str = "") -> list[str]:
    """Effective IT keywords: user list (profile -> .env) UNION built-in defaults.

    User keywords come first so they double as the focused JobsDB/OfferToday
    search terms; the defaults guarantee a sane classification breadth even
    when the user list is narrow (e.g. only "AI Engineer").
    """
    user = []
    if profile_text.strip():
        user = parse_keywords(profile_text)
    elif (settings.JOB_KEYWORDS or "").strip():
        user = parse_keywords(settings.JOB_KEYWORDS)
    seen: set[str] = set()
    merged = [k for k in user if not (k in seen or seen.add(k))]
    for k in DEFAULT_IT_KEYWORDS:
        if k not in seen:
            seen.add(k)
            merged.append(k)
    return merged


def resolve_general_keywords(profile_text: str = "") -> list[str]:
    """Effective general-track keywords: profile -> .env -> built-in defaults."""
    if profile_text.strip():
        kws = parse_keywords(profile_text)
        if kws:
            return kws
    if (settings.GENERAL_JOB_KEYWORDS or "").strip():
        kws = parse_keywords(settings.GENERAL_JOB_KEYWORDS)
        if kws:
            return kws
    return list(DEFAULT_GENERAL_KEYWORDS)


def match_keyword(keyword: str, text: str) -> bool:
    """One keyword against text: CJK -> substring; latin -> whole-word.

    Latin boundaries use lookarounds on [a-zA-Z0-9] instead of ``\\b`` so a
    latin keyword directly attached to CJK still matches (e.g. "ai" in
    "AI基礎架構"), while "data" still does not match "database".
    """
    kw = keyword.strip()
    if not kw:
        return False
    if any(ord(c) > 127 for c in kw):
        return kw in text
    return re.search(
        rf"(?<![a-zA-Z0-9]){re.escape(kw)}(?![a-zA-Z0-9])",
        text,
        re.IGNORECASE,
    ) is not None


def resolve_wanted_locations(profile_text: str = "") -> list[str]:
    """一般工「想去嘅地點」白名單：profile -> .env -> []（空 = 唔篩）。

    用戶要求：唔用黑名單（排除機場），而係只收自己想去嘅地區；機場／赤鱲角
    自然唔會出現，因為唔會填佢落名單。空名單 = 唔篩（避免一填錯就清空塊板）。
    """
    text = (profile_text or "").strip() or (settings.GENERAL_WANTED_LOCATIONS or "").strip()
    return parse_keywords(text) if text else []


# 香港主要地區（18 區 + 常見地點）。用途：分辨「寫咗另一個地區」（肯定唔想去
# -> 篩走）同「完全冇寫地點」（唔確定 -> 保留但標示，保護供應）。
KNOWN_HK_LOCATIONS = [
    "中西區", "灣仔", "東區", "南區", "油尖旺", "深水埗", "九龍城", "黃大仙",
    "觀塘", "葵青", "荃灣", "屯門", "元朗", "北區", "大埔", "沙田", "西貢", "離島",
    "中環", "金鐘", "上環", "西環", "北角", "鰂魚涌", "太古", "柴灣", "筲箕灣",
    "香港仔", "黃竹坑", "鴨脷洲", "赤柱", "薄扶林", "數碼港", "旺角", "尖沙咀",
    "佐敦", "油麻地", "太子", "深水埗", "長沙灣", "荔枝角", "美孚", "石硤尾",
    "九龍塘", "九龍灣", "牛頭角", "觀塘", "藍田", "油塘", "慈雲山", "鑽石山",
    "新蒲崗", "黃大仙", "樂富", "紅磡", "土瓜灣", "何文田", "啟德", "葵涌",
    "葵芳", "青衣", "荔景", "荃灣", "深井", "屯門", "天水圍", "元朗", "粉嶺",
    "上水", "大埔", "沙田", "火炭", "馬鞍山", "大圍", "將軍澳", "西貢", "東涌",
    "機場", "赤鱲角", "迪士尼", "科學園", "白石角", "龍鼓灘", "掃管笏",
]
# 泛指（唔代表任何具體地區）-> 當「唔確定」處理，唔會用嚟做篩走理由：
# 「港九新界」「香港島」「九龍」「新界」「全港」「各區」等等。
VAGUE_LOCATIONS = ["港九新界", "香港島", "九龍半島", "九龍區", "新界區", "全港", "各區"]
# 香港以外（大陸／大灣區城市）：一般工寫呢啲 = 肯定唔喺想去名單
KNOWN_OTHER_LOCATIONS = [
    "深圳", "廣州", "珠海", "東莞", "佛山", "中山", "江門", "惠州", "肇慶",
    "澳門", "上海", "北京", "台北", "新加坡", "shenzhen", "guangzhou",
]


def known_location_match(texts) -> str:
    """文字入面提到嘅**具體**地區（香港各區或者大陸城市）；冇提到就回 ""。

    泛指字眼（「港九新界」等）唔算具體，會回 "" -> 當「唔確定」保留。
    """
    for text in texts:
        if not text:
            continue
        for loc in KNOWN_HK_LOCATIONS + KNOWN_OTHER_LOCATIONS:
            if match_keyword(loc, text):
                return loc
    return ""


def wanted_location_match(texts, wanted) -> str:
    """命中嘅想去地區（冇命中回 ""）。

    工地點嘅判斷要靠幾處一齊睇：``location``（gov.hk 列表已有／OfferToday 開完
    詳情先有）＋ 職位標題 ＋ JD 內文（OfferToday 好多時 location 空白，只有 JD
    寫住「工作地點：觀塘」）。
    """
    if not wanted:
        return ""
    for text in texts:
        if not text:
            continue
        for kw in wanted:
            if match_keyword(kw, text):
                return kw
    return ""


def title_matches(title: str, keywords: list[str]) -> bool:
    """True when any keyword matches the (lowercased) title."""
    if not keywords or not title:
        return False
    return any(match_keyword(k, title) for k in keywords)


def resolve_non_it_keywords(profile_text: str = "") -> list[str]:
    """Effective non-IT exclusion keywords: profile -> .env -> built-in defaults."""
    text = (profile_text or "").strip() or (settings.NON_IT_KEYWORDS or "").strip()
    if text:
        return parse_keywords(text)
    return list(DEFAULT_NON_IT_KEYWORDS)


def classify(title: str, it_keywords: list[str] | None = None,
             non_it_keywords: list[str] | None = None) -> str:
    """Classify a job title into 'it' or 'general'.

    IT wins when the title matches the IT keywords; everything else is
    'general'. (JD text is deliberately NOT considered — JDs mention computers
    everywhere and would drown the general track.)
    """
    kws = it_keywords if it_keywords is not None else resolve_it_keywords()
    if not title_matches(title, kws):
        return "general"
    # 排除字眼：標題似 non-IT（機械／土木／排版…）就唔算 IT，
    # 除非同時有「肯定係 IT」嘅字眼。
    excl = non_it_keywords if non_it_keywords is not None else resolve_non_it_keywords()
    if title_matches(title, excl) and not title_matches(title, STRONG_IT_KEYWORDS):
        return "general"
    return "it"


# 「搵唔到優先工就唔好再揭頁」嘅容忍度：軟上限用盡之後，如果連續咁多個候選
# 都唔係優先工（AI 相關／高分），就當呢個渠道冇貨，收手（免得住死揭 30 頁）。
PRIORITY_SCAN_SLACK = 60


@dataclass
class TrackConfig:
    """Per-track scan settings passed down to the scrapers.

    One instance per enabled track (it / general). The scanner builds these
    from the user's Profile (Settings page) with .env / built-in fallbacks;
    ``defaults()`` is the no-DB fallback used by tests and run_once().
    """
    name: str                 # "it" | "general"
    label: str                # "IT" | "一般"
    keywords: list[str]       # filter keywords for this track
    it_keywords: list[str]    # IT classification keywords (exclusion for general)
    govhk_max_jobs: int       # gov.hk per-scan cap (IT category / general quickview)
    offertoday_max_per_search: int
    offertoday_search_terms: list[str] = field(default_factory=list)
    max_searches: int = 0     # OfferToday general: cap on keyword searches
    non_it_keywords: list[str] = field(default_factory=list)  # 唔當 IT 嘅字眼
    # 一般 track：「想去嘅地點」白名單 — 只收寫得明確又喺名單內嘅工。空 = 唔篩。
    wanted_locations: list[str] = field(default_factory=list)
    # ---- 高分豁免上限（用戶要求）----
    # 開啟後：命中 priority_keywords 或者 keyword pre-score >= cap_bypass_min_score
    # 嘅工唔計入軟上限（govhk_max_jobs / offertoday_max_per_search /
    # MAX_SCAN_JOBS），照樣入庫；每渠道最多豁免 priority_extra_max 份。
    cap_bypass_enabled: bool = False
    cap_bypass_min_score: int = 70
    priority_keywords: list[str] = field(default_factory=list)
    priority_extra_max: int = 50
    hard_cap: int = 0         # 每渠道硬上限（0 = 用內建：gov.hk 30 頁／OfferToday 12 scroll）
    # ---- AI 搜尋組（用戶要求：專門搵 AI／agent 工，7 日內、過期即停）----
    ai_search_terms: list[str] = field(default_factory=list)
    ai_search_max_searches: int = 0
    ai_search_max_age_days: int = 7
    ai_stale_action: str = "channel"   # channel = 停該渠道；scan = 暫停成個掃描
    # 保險／地產／sales agent 類封鎖字眼（絕對否決，唔會入 IT 軌）
    blocked_keywords: list[str] = field(default_factory=list)

    @staticmethod
    def defaults(name: str) -> "TrackConfig":
        """Track config from pure settings/env — used when no Profile row exists."""
        from .tuning import load_tuning
        from .tuning import priority_keywords as _priority_kws

        it_kws = resolve_it_keywords()
        non_it = resolve_non_it_keywords()
        t = load_tuning()
        from .tuning import ai_search_terms as _ai_terms
        ai_group = dict(
            blocked_keywords=resolve_blocked_keywords(),
            ai_search_terms=_ai_terms(t),
            ai_search_max_searches=t.ai_search_max_searches,
            ai_search_max_age_days=t.ai_search_max_age_days,
            ai_stale_action=t.ai_stale_action,
        )
        bypass = dict(
            cap_bypass_enabled=t.cap_bypass_enabled,
            cap_bypass_min_score=t.cap_bypass_min_score,
            priority_keywords=_priority_kws(t),
            priority_extra_max=t.priority_extra_max,
        )
        if name == "it":
            return TrackConfig(
                name="it", label="IT",
                keywords=it_kws, it_keywords=it_kws, non_it_keywords=non_it,
                govhk_max_jobs=settings.GOVHK_IT_MAX_JOBS,
                offertoday_max_per_search=settings.OFFERTODAY_MAX_PER_SEARCH,
                **bypass,
                **ai_group,
            )
        general_kws = resolve_general_keywords()
        return TrackConfig(
            name="general", label="一般",
            keywords=general_kws, it_keywords=it_kws, non_it_keywords=non_it,
            wanted_locations=resolve_wanted_locations(),
            govhk_max_jobs=settings.GOVHK_GENERAL_MAX_JOBS,
            offertoday_max_per_search=settings.OFFERTODAY_GENERAL_MAX_PER_SEARCH,
            offertoday_search_terms=(
                parse_keywords(settings.OFFERTODAY_GENERAL_SEARCH_TERMS) or general_kws
            ),
            max_searches=settings.OFFERTODAY_GENERAL_MAX_SEARCHES,
            **bypass,
            **ai_group,
        )


# 「保險／地產／sales agent」類職位：**絕對否決**（唔理有冇強 IT 字眼）。
# 為咩要絕對：呢類標題好多時夾雜 IT 字（例：「網路銷售代理」有『網路』＝強 IT 字），
# 令之前嘅軟性排除失效，結果保險／地產 agent 工照入 IT 軌（實測 40 份）。
# 用戶要求：呢類唔要。清單可以喺設定頁改（profile.it_blocked_keywords）。
# 硬封鎖：明顯係 sales／agent 角色，一律唔要（唔理有冇 tech 字）
DEFAULT_BLOCKED_SALES_KEYWORDS = [
    "代理", "經紀", "營業員", "佣金", "跑數", "門市", "直銷",
    "insurance agent", "property agent", "estate agent", "sales agent",
    "real estate agent", "commission based",
]

# 行業字眼：職位本身係 AI／dev（見 _TECH_RESCUE）就保留
# （例：「AI Engineer (大型保險公司)」係真 AI 工，唔應該因為「保險」兩個字被殺）
DEFAULT_BLOCKED_INDUSTRY_KEYWORDS = [
    # 「前線」放呢層：前線銷售要封，但「前線部署工程師」(FDE) 係真技術工，
    # 有技術訊號就會保留
    "前線",
    "保險", "地產", "房地產", "樓盤", "物業", "經紀行",
    "insurance", "real estate", "property management", "brokerage",
]

# 行業字眼嘅「例外訊號」：標題有呢啲 = 真技術職位，唔當 sales
_TECH_RESCUE_KEYWORDS = [
    "ai", "agent developer", "ai agent", "developer", "programmer", "software",
    "程式", "編程", "軟件", "軟體", "系統", "數據", "資料庫", "演算法", "計算機",
    "data", "python", "java", "javascript", "typescript", "react", "node", "sql",
    "devops", "cloud", "雲端", "前端", "後端", "full stack", "full-stack", "llm",
    "資訊科技", "it support", "helpdesk", "qa", "測試", "網絡工程師", "系統工程師",
]

# （相容舊名）
DEFAULT_BLOCKED_KEYWORDS = DEFAULT_BLOCKED_SALES_KEYWORDS + DEFAULT_BLOCKED_INDUSTRY_KEYWORDS


def resolve_blocked_keywords(profile_text: str = "") -> list[str]:
    """有效「封鎖字眼」。

    profile（設定頁）有填 = 你自訂嘅硬封鎖清單（完全取代內建）；
    留空 = 內建兩層清單（sales 硬封 + 行業字眼有技術例外）。
    """
    text = (profile_text or "").strip()
    if text:
        kws = parse_keywords(text)
        if kws:
            return kws
    return list(DEFAULT_BLOCKED_KEYWORDS)


def builtin_block_config() -> tuple[list[str], list[str]]:
    """內建 (sales 硬封清單, 行業字眼清單)—— 設定頁顯示用。"""
    return list(DEFAULT_BLOCKED_SALES_KEYWORDS), list(DEFAULT_BLOCKED_INDUSTRY_KEYWORDS)


def blocked_reason(title: str, blocked: list[str] | None = None) -> str:
    """標題係唔係「保險／地產 sales agent」類（回傳命中嘅字，否則 ""）。

    兩層判斷（用戶要求「地產或者保險嘅都唔要」，但唔想殺錯真 AI 工）：
      1. **硬封鎖**（代理／經紀／跑數／insurance agent…）：一律唔要。
      2. **行業字眼**（保險／地產／物業…）：只有當標題**冇**技術職位訊號
         （AI／developer／系統／程式／數據…）先封鎖 —— 所以
         「AI Engineer (大型保險公司)」會保留，「物業工程師」照封。
    ``blocked`` 有傳就當成硬封鎖清單（設定頁自訂）。
    """
    t = title or ""
    if not t:
        return ""
    if blocked is not None:
        for kw in blocked:
            if kw and match_keyword(kw, t):
                return kw
        return ""
    for kw in DEFAULT_BLOCKED_SALES_KEYWORDS:
        if match_keyword(kw, t):
            return kw
    for kw in DEFAULT_BLOCKED_INDUSTRY_KEYWORDS:
        if match_keyword(kw, t):
            if any(match_keyword(r, t) for r in _TECH_RESCUE_KEYWORDS):
                return ""          # 真技術職位（例：AI Engineer @ 保險公司）
            return kw
    return ""


def tech_role_score(title: str) -> int:
    """標題嘅技術職位訊號數量（俾 AI 檢查／除錯用）。"""
    return sum(1 for r in _TECH_RESCUE_KEYWORDS if match_keyword(r, title or ""))


def is_priority_job(title: str, extra_text: str = "", skills: list[str] | None = None,
                    cfg: "TrackConfig | None" = None,
                    min_score: int | None = None,
                    keywords: list[str] | None = None) -> bool:
    """True = 呢份工係「高分／優先」，唔應該被數量上限擋走（用戶要求）。

    判斷喺 list 階段做（標題 + 卡片文字 + 技能清單）——**零 LLM 成本**：
      1. 標題命中優先字詞（預設 = AI 職位關鍵字；用戶可喺設定頁加）
      2. 或者 keyword pre-score >= 門檻（預設 70）
    冇配置（cfg=None 又冇 keywords）就一律回 False（＝舊行為）。
    """
    kws = list(keywords or [])
    if cfg is not None:
        if not cfg.cap_bypass_enabled:
            return False
        kws = kws or list(cfg.priority_keywords or [])
        threshold = cfg.cap_bypass_min_score if min_score is None else min_score
    else:
        if min_score is None:
            return bool(kws) and title_matches(title, kws)
        threshold = min_score
    if kws and title_matches(title, kws):
        return True
    # 標題唔中，就睇技能重疊分（唔用 JD，避免要開詳情頁）
    from .matcher import keyword_score  # lazy: avoid import cycle at module load

    return keyword_score(title, extra_text, skills) >= max(1, int(threshold))

